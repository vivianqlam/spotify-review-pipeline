#!/usr/bin/env python3
"""Render cost/report.md from saved evidence only (no API calls).

  python cost/report.py
"""
import csv
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
import sys
sys.path.insert(0, str(HERE))
import calculator  # noqa: E402


def run_summary(run):
    p = ROOT / "runs" / run / "run_summary.json"
    return json.loads(p.read_text()) if p.exists() else None


def main():
    r = calculator.replay(out=False)
    r2 = calculator.replay(rate_multiplier=2.0, out=False)
    m = r["measured_pilot"]
    L = ["# Cost and runtime calculator: measured 100-review pilot", "",
         "Everything below is recomputed offline from `pilot_calls.jsonl` / `usage.csv` and the dated, editable `rates.csv` "
         "(`python cost/calculator.py`). Paid execution is only `python cost/calculator.py pilot --confirm`.", "",
         "## Measured pilot (`data/cost_100.csv`, unchanged)", "",
         f"- Input SHA-256 `{m['input_sha256']}`; {m['ids']} IDs; completed {m['completed']}, quarantined {m['quarantined']}; unique texts {m['unique_texts']}.",
         f"- **Cold run** (empty result cache, 1 worker): wall clock **{m['cold']['wall_seconds']} s**, API spend **${m['cold']['api_usd']:.6f}** "
         f"(${m['cold']['usd_per_1000_inputs']:.4f} per 1,000 inputs; ${m['cold']['usd_per_completed_record']:.7f} per completed record; "
         f"{m['cold']['records_per_second']} records/s end to end). Enrichment requests {m['cold']['enrich_requests']} / attempts {m['cold']['enrich_attempts']}.",
         f"- **Warm run** (same settings, saved results): wall clock **{m['warm']['wall_seconds']} s**, API spend **${m['warm']['api_usd']:.6f}**, "
         f"new enrichment calls **{m['warm']['new_enrich_calls']}**, new downstream calls {m['warm']['new_downstream_calls']} "
         f"(group naming and memo are cached by their exact inputs), result-cache hits {m['warm']['result_cache_hits']}.",
         "- Provider prompt caching (separate from the result cache) was active: the enrichment system prompt was already cached "
         "by an earlier test call within 5 minutes, so the cold pilot shows cache reads, not writes, for enrichment.",
         f"- Local compute: {m['local_compute']['api_free_local_compute_usd']}.", "",
         "### By stage (cold run)", "",
         "| stage | model | settings | attempts | ok | failed | input | cache write | cache read | output | cost USD |",
         "|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in m["by_stage"].items():
        L.append(f"| {k} | {', '.join(v['models'])} | {'; '.join(v['settings'])} | {v['attempts']} | {v['succeeded']} | {v['failed']} | "
                 f"{v['input_tokens']} | {v['cache_creation_input_tokens']} | {v['cache_read_input_tokens']} | {v['output_tokens']} | {v['cost_usd']:.6f} |")
    L += ["", "Prompt/schema versions: enrich_v1 + schema_v1, verify_v1, group_v1, memo_v1. Batch size: up to 50 reviews per enrichment request. "
          "Verification sample: 20 of 100 in the pilot. Each charge = billed units x price per unit, with mutually exclusive "
          "uncached-input, cache-write, cache-read and output items; thinking is disabled for Haiku roles so no reasoning tokens are billed.", "",
          "**Rate check:** doubling all API rates with usage fixed gives "
          f"${r2['measured_pilot']['cold']['api_usd']:.6f} (exactly 2 x ${m['cold']['api_usd']:.6f}); measured time is unchanged.", "",
          "## Refreshed at 500 and 10,000 reviews", "",
          "| checkpoint | distinct texts | enrich requests | enrich USD | USD per distinct text | quarantined |", "|---|---|---|---|---|---|"]
    for run in ("dev500", "dev10k"):
        s = run_summary(run)
        if not s:
            continue
        e = s["calls_by_role"].get("enrich", {})
        n = s["counts"].get("completed", 0) + s["counts"].get("quarantined", 0)
        L.append(f"| {run} | {n} | {e.get('succeeded', 0)} | {e.get('cost_usd', 0):.4f} | {e.get('cost_usd', 0) / max(1, n):.8f} | {s['counts'].get('quarantined', 0)} |")
    L += ["", f"## Full-run projection (660,622 rows; 660,609 nonempty; 13 empty quarantined; 484,189 distinct texts)", "",
          f"Budget: **${r['budget_usd']:.2f}**. Controls: {json.dumps(r['controls'])}.", "",
          "| scenario | texts sent | tier | first pass | retries | sync premium | verification | fallback | fixed (group+memo) | **total API USD** | est. elapsed h | over budget? |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for k, v in r["projection_scenarios"].items():
        L.append(f"| {k} | {v['texts_classified']:,} | {v['enrich_tier']} | {v['first_pass_usd']} | {v['retries_usd']} | {v['sync_portion_premium_usd']} | "
                 f"{v['verification_usd']} | {v['fallback_usd']} | {v['fixed_overhead_usd']} | **{v['total_api_usd']}** | {v['estimated_elapsed_hours']} | "
                 f"{'**YES**' if v['exceeds_budget'] else 'no'} |")
    L += ["", "Assumptions are in `assumptions.json` (editable). Per-text enrichment usage comes from the measured cold pilot; "
          "fixed overhead (one grouping call, one memo) is added once, not per review. Batch turnaround is an assumption "
          "(most batches finish within 1 hour, max 24 h). Sync elapsed time uses measured per-request throughput x workers and "
          "ignores rate limits, so it is optimistic.", "",
          "**Decision:** run the bulk of the full corpus through the Message Batches API with exact-text reuse (base scenario), "
          "a small live sync phase for the interruption/resume demonstration, no stronger-model fallback (fallback cap 0), "
          "1 worker for sync, and a hard $10 global spend cap enforced before every dispatch.", "",
          "## Replay instructions", "", "```bash", "python cost/calculator.py                      # offline replay (default)",
          "python cost/calculator.py replay --rate-multiplier 2   # rate sensitivity", "python cost/calculator.py replay --budget 5   # budget warning demo",
          "python cost/report.py                          # re-render this report", "```", ""]
    (HERE / "report.md").write_text("\n".join(L), encoding="utf-8")
    print("wrote cost/report.md")


if __name__ == "__main__":
    main()
