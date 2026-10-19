# Cost and runtime calculator: measured 100-review pilot

Everything below is recomputed offline from `pilot_calls.jsonl` / `usage.csv` and the dated, editable `rates.csv` (`python cost/calculator.py`). Paid execution is only `python cost/calculator.py pilot --confirm`.

## Measured pilot (`data/cost_100.csv`, unchanged)

- Input SHA-256 `c884ac3b9be5066995d5063f96ad9af6e5e082975788c1684c4f6b6ea661dd0e`; 100 IDs; completed 100, quarantined 0; unique texts 100.
- **Cold run** (empty result cache, 1 worker): wall clock **33.127 s**, API spend **$0.029170** ($0.2917 per 1,000 inputs; $0.0002917 per completed record; 3.019 records/s end to end). Enrichment requests 2 / attempts 2.
- **Warm run** (same settings, saved results): wall clock **0.154 s**, API spend **$0.000000**, new enrichment calls **0**, new downstream calls 0 (group naming and memo are cached by their exact inputs), result-cache hits 100.
- Provider prompt caching (separate from the result cache) was active: the enrichment system prompt was already cached by an earlier test call within 5 minutes, so the cold pilot shows cache reads, not writes, for enrichment.
- Local compute: unknown (laptop CPU for parsing/SQLite; not metered).

### By stage (cold run)

| stage | model | settings | attempts | ok | failed | input | cache write | cache read | output | cost USD |
|---|---|---|---|---|---|---|---|---|---|---|
| cold/enrich | claude-haiku-5-5 | {"output_config": {"effort": "low"}, "thinking": {"type": "disabled"}} | 2 | 2 | 0 | 4046 | 0 | 3572 | 3528 | 0.002204 |
| cold/group | claude-haiku-5-5 | {"output_config": {"effort": "low"}, "thinking": {"type": "disabled"}} | 1 | 1 | 0 | 3753 | 0 | 0 | 1111 | 0.000931 |
| cold/memo | claude-sonnet-5-5 | {"output_config": {"effort": "low"}} | 1 | 1 | 0 | 4508 | 0 | 535 | 1676 | 0.025830 |
| cold/verify | claude-haiku-5-5 | {"output_config": {"effort": "low"}, "thinking": {"type": "disabled"}} | 1 | 1 | 0 | 925 | 0 | 567 | 215 | 0.000206 |

Prompt/schema versions: enrich_v1 + schema_v1, verify_v1, group_v1, memo_v1. Batch size: up to 50 reviews per enrichment request. Verification sample: 20 of 100 in the pilot. Each charge = billed units x price per unit, with mutually exclusive uncached-input, cache-write, cache-read and output items; thinking is disabled for Haiku roles so no reasoning tokens are billed.

**Rate check:** doubling all API rates with usage fixed gives $0.058341 (exactly 2 x $0.029170); measured time is unchanged.

## Refreshed at 500 and 10,000 reviews

| checkpoint | distinct texts | enrich requests | enrich USD | USD per distinct text | quarantined |
|---|---|---|---|---|---|
| dev500 | 479 | 11 | 0.0089 | 0.00001851 | 0 |
| dev10k | 8448 | 170 | 0.1535 | 0.00001817 | 0 |

## Full-run projection (660,622 rows; 660,609 nonempty; 13 empty quarantined; 484,189 distinct texts)

Budget: **$10.00**. Controls: {"budget_usd": 10.0, "enrich_output_token_cap_per_request": 4000, "max_reviews_per_request": 50, "max_workers": 4, "fallback_fraction_cap": 0.0, "invalid_output_retries": 1, "transient_retries": 4}.

| scenario | texts sent | tier | first pass | retries | sync premium | verification | fallback | fixed (group+memo) | **total API USD** | est. elapsed h | over budget? |
|---|---|---|---|---|---|---|---|---|---|---|---|
| base_batch_with_reuse | 484,189 | batch | 5.3365 | 0.0534 | 0.0267 | 0.0103 | 0.0 | 0.0268 | **5.4536** | 1.0 | no |
| conservative_batch_with_reuse | 484,189 | batch | 5.3365 | 0.2668 | 0.1067 | 0.0103 | 0.0 | 0.0535 | **5.7739** | 24.0 | no |
| no_reuse_batch | 660,609 | batch | 7.281 | 0.0728 | 0.0364 | 0.0103 | 0.0 | 0.0268 | **7.4272** | 1.0 | no |
| standard_sync_with_reuse_4_workers | 484,189 | standard | 10.6731 | 0.1067 | 0.0 | 0.0103 | 0.0 | 0.0268 | **10.8168** | 4.32 | **YES** |

Assumptions are in `assumptions.json` (editable). Per-text enrichment usage comes from the measured cold pilot; fixed overhead (one grouping call, one memo) is added once, not per review. Batch turnaround is an assumption (most batches finish within 1 hour, max 24 h). Sync elapsed time uses measured per-request throughput x workers and ignores rate limits, so it is optimistic.

**Decision:** run the bulk of the full corpus through the Message Batches API with exact-text reuse (base scenario), a small live sync phase for the interruption/resume demonstration, no stronger-model fallback (fallback cap 0), 1 worker for sync, and a hard $10 global spend cap enforced before every dispatch.

## Replay instructions

```bash
python cost/calculator.py                      # offline replay (default)
python cost/calculator.py replay --rate-multiplier 2   # rate sensitivity
python cost/calculator.py replay --budget 5   # budget warning demo
python cost/report.py                          # re-render this report
```
