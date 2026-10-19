#!/usr/bin/env python3
"""Orchestrator: runs the six stages on any input CSV with saved handoffs.

  python run.py all --input data/cost_100.csv --run-dir runs/pilot      # every stage
  python run.py enrich --run-dir runs/full --mode batch --phase resume  # one stage
  python run.py rerank --run-dir runs/full                              # offline, no model calls

Code decides the next step (fixed sequence); models only exercise judgment
inside enrich / verify / group-naming / memo, each with its own prompt and log.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

from pipeline import analysis, config, enrich, ingest, llm, memo, verify
from pipeline.io_utils import file_sha, read_json, read_jsonl, write_json, write_jsonl
from pipeline.state import State


def log(run_dir, stage, **kw):
    from pipeline.io_utils import append_jsonl
    append_jsonl(run_dir / "run_log.jsonl", {"stage": stage, "at": time.time(), **kw})


def cmd_ingest(a):
    a.run_dir.mkdir(parents=True, exist_ok=True)
    t = time.time()
    rep = ingest.ingest(a.input, a.run_dir, a.run_dir.name)
    log(a.run_dir, "ingest", seconds=round(time.time() - t, 2), rows=rep["source_rows"], distinct=rep["distinct_nonempty_texts"])


def cmd_enrich(a):
    st = State(a.run_dir)
    t = time.time()
    if a.mode == "batch":
        res = enrich.run_batch(st, a.run_dir, a.run_dir.name, phase=a.phase, budget=a.budget, max_requests=a.max_requests)
        if st.pending() and not a.no_sync_retry:  # leftovers (invalid output / errored requests) get one bounded sync pass
            print(f"[enrich] {len(st.pending())} texts still pending after batch; sync retry pass")
            res["sync_retry"] = enrich.run_sync(st, a.run_dir, a.run_dir.name, phase=a.phase, workers=a.workers, budget=a.budget)
    else:
        res = enrich.run_sync(st, a.run_dir, a.run_dir.name, phase=a.phase, workers=a.workers,
                              max_requests=a.max_requests, budget=a.budget)
    log(a.run_dir, "enrich", phase=a.phase, mode=a.mode, workers=a.workers, seconds=round(time.time() - t, 2),
        result=res, counts=st.counts(), spent_total=llm.spent_usd())
    print("[enrich]", json.dumps(res), st.counts())


def cmd_checkpoint(a):
    st = State(a.run_dir)
    ids = st.completed_review_ids()
    write_json(a.run_dir / f"checkpoint_{a.name}.json", {"completed_ids": ids, "count": len(ids), "at": time.time(),
                                                          "counts": st.counts()})
    log(a.run_dir, "checkpoint", name=a.name, completed=len(ids))
    print(f"[checkpoint] {a.name}: {len(ids)} completed review IDs saved")


def cmd_verify(a):
    st = State(a.run_dir)
    t = time.time()
    rep = verify.run(st, a.run_dir, a.run_dir.name, size=a.verify_size, budget=a.budget)
    pe = verify.planted_error_test(st, a.run_dir)
    log(a.run_dir, "verify", seconds=round(time.time() - t, 2), report=rep, planted=pe["catch_rate"])
    print("[verify]", json.dumps(rep["agreement"]), "disagreements:", rep["disagreements"], "planted caught:", pe["caught_by_verifier_comparison"], "/", pe["planted"])


def cmd_records(a):
    st = State(a.run_dir)
    recs = analysis.build_records(st)
    analysis.save_records(a.run_dir, recs)
    c = {s: sum(r["status"] == s for r in recs) for s in ("completed", "quarantined")}
    log(a.run_dir, "records", **c)
    print("[records]", c)
    return recs


def cmd_group(a):
    st = State(a.run_dir)
    recs = read_jsonl(a.run_dir / "records.jsonl") or cmd_records(a)
    t = time.time()
    issues, membership = analysis.group(st, a.run_dir, a.run_dir.name, recs, budget=a.budget, use_model=not a.no_model)
    log(a.run_dir, "group", seconds=round(time.time() - t, 2), issues=len(issues), memberships=len(membership))
    print(f"[group] {len(issues)} issues, {len(membership)} memberships")


def cmd_rank(a):
    rows = analysis.rank(a.run_dir)
    log(a.run_dir, "rank", issues=len(rows))
    print("[rank] top 5:", [(r["issue_id"], r["priority_score"]) for r in rows[:5]])


def cmd_rerank(a):
    """Regenerate ranking from saved records + membership with no model call; prove it is identical."""
    before = (a.run_dir / "ranking.csv").read_text()
    analysis.rank(a.run_dir)
    after = (a.run_dir / "ranking.csv").read_text()
    same = before == after
    write_json(a.run_dir / "rerank_check.json", {"identical": same, "ranking_sha256": file_sha(a.run_dir / "ranking.csv"),
                                                 "model_calls": 0, "at": time.time()})
    print(f"[rerank] identical to saved ranking: {same}")
    if not same:
        sys.exit(1)


def memo_context(run_dir):
    recs = read_jsonl(run_dir / "records.jsonl")
    ctx = {"source_rows": len(recs), "completed": sum(r["status"] == "completed" for r in recs),
           "quarantined_empty_text": sum(r.get("reason") == "empty_review_text" for r in recs),
           "quarantined_other": sum(r["status"] != "completed" and r.get("reason") != "empty_review_text" for r in recs),
           "review_window": "2022-05 to 2023-11 (first and last months partial)"}
    if (run_dir / "verify_report.json").exists():
        v = read_json(run_dir / "verify_report.json")
        ctx["independent_verifier"] = {"sample": v["sample_size"],
                                       "topic_agreement_pct": f"{100 * v['agreement']['topic']:.1f}",
                                       "intent_agreement_pct": f"{100 * v['agreement']['intent']:.1f}",
                                       "severity_agreement_pct": f"{100 * v['agreement']['severity']:.1f}"}
    g = config.ROOT / "evals" / "golden_results.json"
    if g.exists():
        gr = read_json(g)
        ctx["golden_50_human_labels"] = {k: gr["summary"][k] for k in ("n", "topic_agreement_pct", "intent_agreement_pct",
                                                                         "severity_exact_pct", "severity_mae")}
    return ctx


def cmd_memo(a):
    recs = read_jsonl(a.run_dir / "records.jsonl")
    text, rep = memo.write(a.run_dir, a.run_dir.name, recs, memo_context(a.run_dir), budget=a.budget)
    log(a.run_dir, "memo", check=rep)
    print("[memo] check pass:", rep["pass"], "| unsupported numbers:", rep["unsupported_numbers"][:10])


def cmd_export(a):
    """Standardized grading/ export (GRADING_CONTRACT.md)."""
    out = a.out
    out.mkdir(parents=True, exist_ok=True)
    st = State(a.run_dir)
    rep = st.meta_get("ingestion_report")
    write_json(out / "run.json", {"version": "a5-audit-v1", "analysis_count": rep["source_rows"], "analysis_sha256": rep["input_sha256"],
                                  "classification_input_fields": ["review_text"], "allow_multi_issue": False,
                                  "run_id": a.run_dir.name, "label_config": config.LABEL_CONFIG})
    shutil.copy(a.run_dir / "ingestion.json", out / "ingestion.json")
    recs = read_jsonl(a.run_dir / "records.jsonl")
    keep = ("review_id", "source_sha256", "status", "topic", "intent", "sentiment", "severity", "entities", "evidence_quote",
            "needs_review", "label_config", "cache_source_id", "reason")
    write_jsonl(out / "records.jsonl.gz", [{k: r[k] for k in keep if k in r} for r in recs], compress=True)
    for name in ("membership.csv", "ranking.csv", "claims.csv"):
        shutil.copy(a.run_dir / name, out / name)
    calls = read_jsonl(a.run_dir / "calls.jsonl")
    write_jsonl(out / "calls.jsonl.gz" if len(calls) > 2000 else out / "calls.jsonl", calls, compress=len(calls) > 2000)
    for name in ("before", "after"):
        p = a.run_dir / f"checkpoint_{name}.json"
        if p.exists():
            write_json(out / f"checkpoint_{name}.json", {"completed_ids": read_json(p)["completed_ids"]})
    print(f"[export] wrote {out}")


def cmd_all(a):
    cmd_ingest(a)
    cmd_enrich(a)
    cmd_verify(a)
    cmd_records(a)
    cmd_group(a)
    cmd_rank(a)
    if not a.skip_memo:
        cmd_memo(a)
    cmd_status(a)


def cmd_status(a):
    st = State(a.run_dir)
    calls = read_jsonl(a.run_dir / "calls.jsonl")
    by_role = {}
    for c in calls:
        d = by_role.setdefault(c["role"], {"attempts": 0, "succeeded": 0, "failed": 0, "input_tokens": 0, "output_tokens": 0,
                                           "cache_read": 0, "cache_write": 0, "cost_usd": 0.0})
        d["attempts"] += 1
        d[c["outcome"]] += 1
        d["input_tokens"] += c.get("input_tokens", 0)
        d["output_tokens"] += c.get("output_tokens", 0)
        d["cache_read"] += c.get("cache_read_input_tokens", 0)
        d["cache_write"] += c.get("cache_creation_input_tokens", 0)
        d["cost_usd"] += c.get("cost_usd", 0.0)
    summary = {"run_id": a.run_dir.name, "counts": st.counts(), "calls_by_role": by_role,
               "run_cost_usd": round(sum(d["cost_usd"] for d in by_role.values()), 6),
               "global_spend_usd": round(llm.spent_usd(), 6), "budget_usd": config.GLOBAL_BUDGET_USD,
               "label_config": config.LABEL_CONFIG, "models": {"enrich": config.ENRICH_MODEL, "verify": config.VERIFY_MODEL,
                                                               "group": config.GROUP_MODEL, "memo": config.MEMO_MODEL}}
    write_json(a.run_dir / "run_summary.json", summary)
    print(json.dumps(summary, indent=1))


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    def add(name, fn, **kw):
        s = sub.add_parser(name)
        s.add_argument("--run-dir", type=Path, required=True)
        s.add_argument("--budget", type=float, default=None, help="global spend cap in USD (default config / $10)")
        s.set_defaults(fn=fn)
        return s
    for name, fn in (("all", cmd_all), ("ingest", cmd_ingest)):
        s = add(name, fn)
        s.add_argument("--input", type=Path, required=True)
        if name == "all":
            s.add_argument("--mode", choices=("sync", "batch"), default="sync")
            s.add_argument("--phase", default="initial")
            s.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
            s.add_argument("--max-requests", type=int, default=None)
            s.add_argument("--verify-size", type=int, default=config.VERIFY_SAMPLE_SIZE)
            s.add_argument("--skip-memo", action="store_true")
            s.add_argument("--no-model", action="store_true")
            s.add_argument("--no-sync-retry", action="store_true")
    s = add("enrich", cmd_enrich)
    s.add_argument("--mode", choices=("sync", "batch"), default="sync")
    s.add_argument("--phase", choices=("initial", "resume"), default="initial")
    s.add_argument("--workers", type=int, default=config.DEFAULT_WORKERS)
    s.add_argument("--max-requests", type=int, default=None, help="stop after N requests (bounded runs)")
    s.add_argument("--no-sync-retry", action="store_true")
    s = add("checkpoint", cmd_checkpoint)
    s.add_argument("--name", choices=("before", "after"), required=True)
    s = add("verify", cmd_verify)
    s.add_argument("--verify-size", type=int, default=config.VERIFY_SAMPLE_SIZE)
    add("records", cmd_records)
    s = add("group", cmd_group)
    s.add_argument("--no-model", action="store_true", help="keep code default issue names")
    add("rank", cmd_rank)
    add("rerank", cmd_rerank)
    add("memo", cmd_memo)
    s = add("export", cmd_export)
    s.add_argument("--out", type=Path, default=Path("grading"))
    add("status", cmd_status)
    a = p.parse_args()
    if a.budget is not None:
        config.GLOBAL_BUDGET_USD = a.budget
    a.fn(a)


if __name__ == "__main__":
    main()
