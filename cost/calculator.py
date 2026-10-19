#!/usr/bin/env python3
"""100-review cost and runtime calculator.

  python cost/calculator.py                    # DEFAULT: offline replay - no API key, no model calls
  python cost/calculator.py replay --rate-multiplier 2 --budget 5
  python cost/calculator.py pilot --confirm    # EXPLICIT paid run: real cold + warm pilot on data/cost_100.csv

Replay recomputes every charge as billed_units x price_per_unit from saved
usage (cost/usage.csv) and editable dated rates (cost/rates.csv), then projects
the full 660,622-row run from measured per-stage rates and assumptions.json.
Importing this module never makes a call.
"""
import argparse
import csv
import json
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
PILOT_INPUT = ROOT / "data" / "cost_100.csv"
COLD, WARM = HERE / "runs" / "pilot_cold", None  # warm run reuses the cold run's saved results in place
FULL = {"rows": 660622, "nonempty": 660609, "empty_quarantine": 13, "distinct_texts": 484189}
ITEMS = ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens", "output_tokens")
RATE_KEY = {"input_tokens": "input", "cache_creation_input_tokens": "cache_write", "cache_read_input_tokens": "cache_read",
            "output_tokens": "output"}


# ------------------------------------------------------------------ helpers
def read_csv(p):
    with open(p, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def read_jsonl(p):
    p = Path(p)
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def load_rates(mult=1.0):
    """{(model, tier, item): usd_per_unit}; tier in {standard, batch}; units = tokens."""
    rates = {}
    for r in read_csv(HERE / "rates.csv"):
        rates[(r["model"], r["tier"], r["billing_item"])] = float(r["usd_per_million_tokens"]) * mult / 1_000_000
    return rates


def call_cost(row, rates):
    tier = "batch" if row.get("mode") == "batch" else "standard"
    parts = {}
    for item in ITEMS:
        units = int(row.get(item) or 0)
        parts[item] = units * rates[(row["model"], tier, RATE_KEY[item])]
    return sum(parts.values()), parts


# ------------------------------------------------------------------ replay
def replay(rate_multiplier=1.0, budget=None, out=True):
    rates = load_rates(rate_multiplier)
    a = json.loads((HERE / "assumptions.json").read_text())
    budget = a["controls"]["budget_usd"] if budget is None else budget
    usage = read_csv(HERE / "usage.csv")
    timing = json.loads((HERE / "pilot_timing.json").read_text())
    records = read_jsonl(HERE / "pilot_records.jsonl")
    stages = defaultdict(lambda: {"attempts": 0, "succeeded": 0, "failed": 0, **{i: 0 for i in ITEMS}, "cost_usd": 0.0,
                                  "models": set(), "review_ids": 0, "settings": set()})
    by_run = defaultdict(float)
    for u in usage:
        c, _ = call_cost(u, rates)
        s = stages[(u["pilot_run"], u["role"])]
        s["attempts"] += 1
        s[u["outcome"]] += 1
        for i in ITEMS:
            s[i] += int(u[i] or 0)
        s["cost_usd"] += c
        s["models"].add(u["model"])
        s["settings"].add(u["settings"])
        s["review_ids"] += int(u["n_review_ids"] or 0)
        by_run[u["pilot_run"]] += c
    completed = sum(r["status"] == "completed" for r in records)
    cold = timing["cold"]
    warm = timing["warm"]
    cold_cost = by_run.get("cold", 0.0)
    warm_cost = by_run.get("warm", 0.0)
    enrich = stages[("cold", "enrich")]
    # ---- projection (per-stage, from measured cold-run usage) ----
    n_inputs = cold["distinct_texts_sent"]
    per_text = {i: enrich[i] / max(1, n_inputs) for i in ITEMS}
    def stage_cost(units_per_text, n_texts, model, tier):
        return sum(units_per_text[i] * n_texts * rates[(model, tier, RATE_KEY[i])] for i in ITEMS)
    scen = {}
    for name, sc in a["scenarios"].items():
        n_texts = FULL["distinct_texts"] if sc["exact_text_reuse"] else FULL["nonempty"]
        tier = sc["enrich_tier"]
        first_pass = stage_cost(per_text, n_texts, a["enrich_model"], tier)
        retries = first_pass * sc["invalid_retry_rate"]
        sync_part = stage_cost(per_text, n_texts * sc["sync_fraction"], a["enrich_model"], "standard") - \
            stage_cost(per_text, n_texts * sc["sync_fraction"], a["enrich_model"], tier)
        v = stages[("cold", "verify")]
        v_per = {i: v[i] / max(1, v["review_ids"]) for i in ITEMS}
        verify = stage_cost(v_per, sc["verify_sample"], a["verify_model"], "standard")
        fallback = first_pass * sc["fallback_fraction"] * a["fallback_cost_multiplier"]
        fixed = sum(stages[("cold", r)]["cost_usd"] for r in ("group", "memo")) * sc["fixed_overhead_multiplier"]
        total = first_pass + retries + sync_part + verify + fallback + fixed
        if tier == "batch":
            hours = sc["batch_turnaround_hours"]
        else:
            hours = n_texts / max(1e-9, cold["enrich_texts_per_second"] * sc["workers"]) / 3600
        scen[name] = {"texts_classified": n_texts, "enrich_tier": tier, "first_pass_usd": round(first_pass, 4),
                      "retries_usd": round(retries, 4), "sync_portion_premium_usd": round(sync_part, 4),
                      "verification_usd": round(verify, 4), "fallback_usd": round(fallback, 4), "fixed_overhead_usd": round(fixed, 4),
                      "total_api_usd": round(total, 4), "estimated_elapsed_hours": round(hours, 2),
                      "exceeds_budget": total > budget}
    result = {
        "rate_multiplier": rate_multiplier, "budget_usd": budget,
        "measured_pilot": {
            "input_file": str(PILOT_INPUT.name), "input_sha256": cold["input_sha256"], "ids": len(records),
            "completed": completed, "quarantined": len(records) - completed, "unique_texts": cold["distinct_texts_sent"],
            "cold": {"wall_seconds": cold["wall_seconds"], "api_usd": round(cold_cost, 6),
                     "usd_per_1000_inputs": round(1000 * cold_cost / len(records), 6),
                     "usd_per_completed_record": round(cold_cost / max(1, completed), 8),
                     "records_per_second": round(len(records) / cold["wall_seconds"], 3),
                     "enrich_requests": enrich["succeeded"], "enrich_attempts": enrich["attempts"]},
            "warm": {"wall_seconds": warm["wall_seconds"], "api_usd": round(warm_cost, 6),
                     "new_enrich_calls": warm["new_enrich_calls"], "new_downstream_calls": warm["new_downstream_calls"],
                     "result_cache_hits": warm["result_cache_hits"]},
            "local_compute": a["local_compute"],
            "by_stage": {f"{k[0]}/{k[1]}": {**{x: v[x] for x in ("attempts", "succeeded", "failed", *ITEMS)},
                                            "cost_usd": round(v["cost_usd"], 6), "models": sorted(v["models"]),
                                            "settings": sorted(v["settings"])} for k, v in sorted(stages.items())},
        },
        "controls": a["controls"], "projection_scenarios": scen,
        "full_run_scope": FULL,
    }
    if out:
        (HERE / "replay_result.json").write_text(json.dumps(result, indent=2, sort_keys=True))
        print(json.dumps({"cold_usd": result["measured_pilot"]["cold"]["api_usd"], "warm_usd": result["measured_pilot"]["warm"]["api_usd"],
                          "cold_seconds": cold["wall_seconds"], "warm_seconds": warm["wall_seconds"],
                          "scenarios": {k: (v["total_api_usd"], v["estimated_elapsed_hours"], "OVER BUDGET" if v["exceeds_budget"] else "ok")
                                        for k, v in scen.items()}}, indent=1))
        for k, v in scen.items():
            if v["exceeds_budget"]:
                print(f"WARNING: scenario '{k}' (${v['total_api_usd']:.2f}) exceeds the ${budget:.2f} budget")
    return result


# ------------------------------------------------------------------ paid pilot (explicit)
def pilot(confirm, fresh):
    if not confirm:
        sys.exit("Refusing to spend money without --confirm. The default command is offline replay.")
    if COLD.exists():
        if not fresh:
            sys.exit(f"{COLD} exists; pass --fresh to delete it and re-run the cold pilot with an empty cache.")
        shutil.rmtree(COLD)
    py = sys.executable
    def run_all(tag):
        t0 = time.time()
        subprocess.run([py, str(ROOT / "run.py"), "all", "--input", str(PILOT_INPUT), "--run-dir", str(COLD),
                        "--workers", "1", "--verify-size", "20"], check=True, cwd=ROOT)
        return round(time.time() - t0, 3)
    cold_s = run_all("cold")
    calls_cold = read_jsonl(COLD / "calls.jsonl")
    warm_s = run_all("warm")
    (HERE / "pilot_wall_seconds.json").write_text(json.dumps({"cold": cold_s, "warm": warm_s}))
    calls_all = read_jsonl(COLD / "calls.jsonl")
    for k, c in enumerate(calls_all):
        c["pilot_run"] = "cold" if k < len(calls_cold) else "warm"
    new = calls_all[len(calls_cold):]
    with open(HERE / "pilot_calls.jsonl", "w", encoding="utf-8") as f:
        for c in calls_all:
            f.write(json.dumps(c, sort_keys=True) + "\n")
    shutil.copy(COLD / "records.jsonl", HERE / "pilot_records.jsonl")
    ing = json.loads((COLD / "ingestion_report.json").read_text())
    enrich_calls = [c for c in calls_cold if c["role"] == "enrich" and c["outcome"] == "succeeded"]
    enrich_lat = sum(c.get("latency_s", 0) for c in enrich_calls)
    timing = {"cold": {"wall_seconds": cold_s, "input_sha256": ing["input_sha256"], "distinct_texts_sent": ing["distinct_nonempty_texts"],
                       "enrich_call_seconds_summed": round(enrich_lat, 3),
                       "enrich_texts_per_second": round(ing["distinct_nonempty_texts"] / max(1e-9, enrich_lat), 4),
                       "workers": 1, "stage_log": read_jsonl(COLD / "run_log.jsonl")},
              "warm": {"wall_seconds": warm_s, "new_enrich_calls": sum(c["role"] == "enrich" for c in new),
                       "new_downstream_calls": sum(c["role"] != "enrich" for c in new),
                       "result_cache_hits": ing["distinct_nonempty_texts"]},
              "measured_at": time.strftime("%Y-%m-%d %H:%M:%S %Z")}
    (HERE / "pilot_timing.json").write_text(json.dumps(timing, indent=2))
    write_usage(calls_all)
    print(f"[pilot] cold {cold_s}s, warm {warm_s}s, warm new enrich calls: {timing['warm']['new_enrich_calls']}")
    replay()


def write_usage(calls):
    rates = load_rates()
    cols = ["pilot_run", "role", "model", "mode", "request_id", "outcome", "attempt", "n_review_ids", "settings", *ITEMS,
            "latency_s", "logged_cost_usd", "recomputed_cost_usd"]
    with open(HERE / "usage.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        for c in calls:
            row = {"pilot_run": c["pilot_run"], "role": c["role"], "model": c["model"], "mode": c.get("mode", "sync"),
                   "request_id": c["request_id"], "outcome": c["outcome"], "attempt": c.get("attempt", 1),
                   "n_review_ids": len(c.get("review_ids", [])), "settings": json.dumps(c.get("settings", {}), sort_keys=True),
                   **{i: c.get(i, 0) for i in ITEMS}, "latency_s": c.get("latency_s", ""), "logged_cost_usd": c.get("cost_usd", 0)}
            row["recomputed_cost_usd"] = round(call_cost(row, rates)[0], 10)
            w.writerow(row)


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd")
    r = sub.add_parser("replay", help="offline (default)")
    r.add_argument("--rate-multiplier", type=float, default=1.0)
    r.add_argument("--budget", type=float, default=None)
    pp = sub.add_parser("pilot", help="PAID: real cold+warm pilot")
    pp.add_argument("--confirm", action="store_true")
    pp.add_argument("--fresh", action="store_true")
    a = p.parse_args()
    if a.cmd == "pilot":
        pilot(a.confirm, a.fresh)
    else:
        replay(getattr(a, "rate_multiplier", 1.0), getattr(a, "budget", None))
