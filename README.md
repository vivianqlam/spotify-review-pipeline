# Spotify review pipeline: multi-agent large data processing

Turns all **660,622** Spotify Google Play reviews (May 2022 - Nov 2023, 97.4 MB) into a product-priority
recommendation where every number traces back to saved records, issue membership and source review IDs.

**Question:** where should next quarter's product effort go - access, usability, playback, or billing/support?
**Answer (memo):** [`memo.md`](memo.md) - prioritize **playback** (playback + downloads: 40,662 complaints, mean severity 3.12, 10,936 at severity 4+), starting with general playback bugs, music stopping/skipping, plus a targeted login fix; usability (volume) and billing/support (most cancellation intent) are the main alternatives.

> A reviewer can inspect every conclusion without an API key and re-run the deterministic ranking and the
> cost calculator offline (see [Reproduce without paid calls](#reproduce-without-paid-calls)).

---

## Results summary (final full-corpus run `runs/full`)

| | value | evidence |
|---|---|---|
| Source rows read / unique IDs | 660,622 / 660,622 (0 duplicate IDs) | [`runs/full/ingestion_report.json`](runs/full/ingestion_report.json), [`grading/ingestion.json`](grading/ingestion.json) |
| Empty texts quarantined (`empty_review_text`) | 13 | [`runs/full/quarantine.jsonl`](runs/full/quarantine.jsonl) |
| Nonempty reviews successfully classified | 660,603 of 660,609 | [`grading/records.jsonl.gz`](grading/records.jsonl.gz) |
| Other quarantines (model-stage failures) | 6 (invalid model output twice) | [`runs/full/quarantine.jsonl`](runs/full/quarantine.jsonl) |
| Distinct texts sent to the model | 484,189 (exact-text reuse covers 176,420 rows via `cache_source_id`) | `records.jsonl.gz` |
| Missing app versions (kept, classified) | 159,701 | ingestion report |
| Golden-50 agreement vs my hand labels | topic 80.0%, intent 90.0%, severity exact 84.0% (MAE 0.20), sentiment within ±0.5 96.0%; 8 ambiguous cases | [`evals/golden_results.json`](evals/golden_results.json), [`evals/golden_cases.csv`](evals/golden_cases.csv) |
| Independent verifier (1,000 random texts) | topic 84.9%, intent 90.5%, severity 85.5%; 305 disagreements | [`runs/full/verify_report.json`](runs/full/verify_report.json), [`runs/full/verify_disagreements.csv`](runs/full/verify_disagreements.csv) |
| Planted-error test | 25/25 planted wrong labels caught | [`runs/full/planted_error_test.json`](runs/full/planted_error_test.json) |
| Prompt-injection test | 5/5 synthetic injected reviews labeled by the rules, not the injection | [`evals/system_tests_results.json`](evals/system_tests_results.json) |
| Full-run API cost (measured from usage x dated rates) | **$4.61** total for `runs/full` (enrich $4.572, verify $0.010, group $0.003, memo $0.028); all project spend incl. pilots/dev/tests $4.86 of a $10 cap | [`runs/full/run_summary.json`](runs/full/run_summary.json), [`ledger/spend.jsonl`](ledger/spend.jsonl) |
| Full-run elapsed time | about 57 min end to end (ingest 10.7 s; live initial+resume demo 97 s; Batch API phase 27.6 min for 9,673 requests; verify 52 s; group/rank/memo ~1 min). 1 worker for sync; batches ran concurrently on Anthropic's side. | [`runs/full/run_log.jsonl`](runs/full/run_log.jsonl) |
| Measured 100-review pilot | cold **$0.0292 / 33.1 s**, warm **$0 / 0.15 s with 0 new calls** | [`cost/report.md`](cost/report.md) |

Measured results vs estimates: everything in this table is measured from saved logs except the pre-run projections in
`cost/report.md`, which are labeled as estimates. "Accounted for" (660,622 = completed + quarantined) is different from
"successfully classified" (660,603).

## Rubric map

| Rubric criterion | Where the evidence is |
|---|---|
| **D1** accessible code/setup and artifacts | [Setup](#setup), [`requirements.txt`](requirements.txt), [`.env.example`](.env.example), this README, [`grading/`](grading/) |
| **D2** architecture, shared schema, provenance | [`docs/architecture.md`](docs/architecture.md), [Labels and schema](#labels-and-schema), [`runs/full/data_manifest.json`](runs/full/data_manifest.json), row `source_sha256`, `label_config`, `cache_source_id` |
| **D3** memo numbers linked to calculations and evidence | [`memo.md`](memo.md) cites claim IDs from [`grading/claims.csv`](grading/claims.csv) and [`runs/full/area_claims.csv`](runs/full/area_claims.csv); code check [`runs/full/memo_check.json`](runs/full/memo_check.json); [trace](#one-review-traced-end-to-end) |
| **D4** coherent recommendation, alternatives, limitations | [`memo.md`](memo.md), [Limitations](#limitations) |
| **T1** 50 human labels, per-field comparison, error analysis | [`evals/golden_50_labels.csv`](evals/golden_50_labels.csv), [`evals/golden_results.json`](evals/golden_results.json), [`evals/golden_cases.csv`](evals/golden_cases.csv), [Golden-set evaluation](#golden-set-evaluation) |
| **T2** independent verification, planted-error and injection tests | [`pipeline/verify.py`](pipeline/verify.py), `runs/full/verify_*`, `planted_error_test.json`, [`evals/system_tests_results.json`](evals/system_tests_results.json) |
| **T3** real 100-review cold/warm pilot, offline calculator, retry/spend/recovery controls | [`cost/`](cost/) ([`report.md`](cost/report.md), [`calculator.py`](cost/calculator.py), `pilot_calls.jsonl`, `pilot_records.jsonl`, `usage.csv`, `rates.csv`), [Controls](#retry-spending-and-recovery-controls) |
| **W1** full ingestion, coverage, classification | `grading/records.jsonl.gz`, [`grading/self-check` result](#grading-export-and-self-check) |
| **W2** staged program, bounded calls, saved handoffs, resume | [`run.py`](run.py), `grading/calls.jsonl.gz`, [`grading/checkpoint_before.json`](grading/checkpoint_before.json) / [`after`](grading/checkpoint_after.json), [resume demo](#interruption-and-resume) |
| **W3** reproducible baseline ranking, grounded output | [`grading/ranking.csv`](grading/ranking.csv), `python run.py rerank --run-dir runs/full`, [`memo.md`](memo.md) |

---

## Setup

```bash
git clone <this repo> && cd spotify-review-pipeline
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # Python 3.14 tested
cp .env.example .env        # only needed for PAID runs; put ANTHROPIC_API_KEY=... in .env
```

Data (not committed; 97.4 MB): download the course ZIP (source: [BwandoWando, *3.4 Million Spotify Google Store Reviews*, v2, Kaggle, CC0](https://www.kaggle.com/datasets/bwandowando/3-4-million-spotify-google-store-reviews))
and place the files in `data/` (`spotify_reviews_18months.csv`, `cost_100.csv`, `checkpoint_500.csv`,
`analysis_10000.csv`, `golden_50_to_label.csv`, `manifest.json`).
Expected SHA-256 of `spotify_reviews_18months.csv`: `1fc85de68a304dd8978b537cfa58793d5f41cbaf417fa32cb53899f83a2fcef6`.

Secrets: `.env` is git-ignored (`git check-ignore .env` prints `.env`); only the blank `.env.example` is tracked.
Offline replay, rerank, evaluation and the checker need no key.

## Run commands

```bash
# Paid, full pipeline on ANY input CSV (sync API, 1 worker): ingest -> enrich -> verify -> group -> rank -> memo
python run.py all --input data/checkpoint_500.csv --run-dir runs/my_run

# How the final full-corpus run was executed
python run.py ingest --input data/spotify_reviews_18months.csv --run-dir runs/full
./scripts/resume_demo.sh                                            # live initial phase, SIGINT, checkpoint, resume
python run.py enrich --run-dir runs/full --mode batch --phase resume  # remaining texts via Message Batches (50% price)
python run.py checkpoint --run-dir runs/full --name after
python run.py verify  --run-dir runs/full      # 1,000-text independent verification + planted-error test
python run.py records --run-dir runs/full      # one final record per source ID
python run.py group   --run-dir runs/full      # code membership + model issue names
python run.py rank    --run-dir runs/full      # deterministic baseline
python run.py memo    --run-dir runs/full      # memo writer + automated claim check
python run.py export  --run-dir runs/full --out grading
python run.py status  --run-dir runs/full
```

## Reproduce without paid calls

```bash
python run.py rerank --run-dir runs/full          # rebuild ranking from saved records + membership; asserts identical
python cost/calculator.py                         # offline replay of the measured 100-review pilot + projections
python cost/calculator.py replay --rate-multiplier 2   # doubling rates doubles API spend; time unchanged
python evals/golden_eval.py evaluate --records runs/golden/records.jsonl
python evals/system_tests.py offline
python check_submission.py profile --full data/spotify_reviews_18months.csv --out /tmp/ingestion.json
python check_submission.py reference --full data/spotify_reviews_18months.csv --analysis data/spotify_reviews_18months.csv --out local-reference.json
python check_submission.py check --reference local-reference.json --submission grading --out self-check.json
```

---

## Architecture

Full diagram and stage table: [`docs/architecture.md`](docs/architecture.md).

| # | Stage | Owner | Input -> output (saved handoff) |
|---|---|---|---|
| 1 | Prepare | **code** | CSV -> `state.db` (one row per source ID + one per distinct text), ingestion report, manifest, pending queue |
| 2 | Classify | **enricher model** (Haiku 5.5) + code validation | <=50 pending texts per request -> validated labels, `calls.jsonl`, quarantine |
| 3 | Verify | **verifier model** (separate prompt) + code comparison | seeded random sample, text only -> verifier labels, disagreement report |
| 4 | Group | **code** membership + **grouper model** names | complaint/cancellation records -> `membership.csv`, `issues.json` |
| 5 | Rank | **code** only | records + membership -> `ranking.csv`, `aggregates.csv` |
| 6 | Recommend | **memo model** (Sonnet 5.5) + code claim check + human review | claims + aggregates + evidence pack -> `memo.md`, `memo_check.json` |

**Why each model call is needed / what code does instead.** Only reading free-text customer language needs a model:
topic, intent, severity, sentiment and a supporting quote (enricher); an independent re-read (verifier); short human-readable
issue names (grouper); and the prose argument (memo). Code does everything countable: CSV parsing, hashing, exact-duplicate
detection, empty-text quarantine, request batching, ID mapping, schema/label/range validation, exact-quote checking
(short reviews use their full text; long-review quotes must be exact substrings, otherwise a deterministic prefix is used
and `needs_review=true`), unsupported-entity removal, retries, spend reservations, issue membership, all arithmetic,
ranking and claim checking. The sequence is fixed; code decides the next step.

**Models and settings** (allowlist in [`pipeline/config.py`](pipeline/config.py)): enrich/verify/group = `claude-haiku-5-5`,
thinking disabled, effort low; memo = `claude-sonnet-5-5`, effort low. Prompt versions: [`prompts/`](prompts/)
`enrich_v1`, `verify_v1`, `group_v1`, `memo_v2` (the 100-review pilot used `memo_v1`; why v1 was replaced: [`docs/memo_v1_rejected.md`](docs/memo_v1_rejected.md)); `label_config = claude-haiku-5-5+enrich_v1+schema_v1+effort-low+thinking-off`.
No stronger-model fallback was used (declared fallback cap 0).

## Labels and schema

Common topics (exact), intents (precedence `cancellation > complaint > request > praise > unclear`) and the shared
1-5 severity scale are copied from `GRADING_CONTRACT.md` into [`prompts/enrich_v1.txt`](prompts/enrich_v1.txt) with
seven invented examples (none from the golden set). My additional applied rules:
- **Severity examples:** 2 = "too many ads"; 3 = "lyrics sometimes missing", "free tier forces shuffle"; 4 = "app won't open",
  "offline mode doesn't work at all", "can't log in"; 5 only for explicit unauthorized charges, stolen accounts or lost libraries.
- **Sentiment:** 5 levels {-1, -0.5, 0, 0.5, 1} (snapped in code); evaluation tolerance ±0.5 declared in advance.
- **Ads** interrupting listening = `usability`; complaints about price or paying to remove ads = `billing`; free-tier
  restrictions explicitly tied to Premium (forced shuffle, skip limits) = `billing`.
- **Entities:** only named things literally in the text (devices, features, plan names, artists, other apps); code drops any not found in the text.
- **Subtopics (optional, for grouping):** keyword rules per topic in [`pipeline/analysis.py`](pipeline/analysis.py) `SUBTOPICS`, else `general`.

Completed record schema (`grading/records.jsonl.gz`): `review_id, source_sha256, status, topic, intent, sentiment,
severity, entities, evidence_quote, needs_review, label_config[, cache_source_id]`; quarantined records carry `reason`.
`source_sha256` uses the contract's `row_sha` (identical code in [`pipeline/io_utils.py`](pipeline/io_utils.py)).
Internal results also keep `quote_source` and `dropped_entities` in `runs/*/state.db`.

## Golden-set evaluation

I hand-labeled all 50 reviews in `golden_50_to_label.csv` **before** running any model on them
([`evals/golden_50_labels.csv`](evals/golden_50_labels.csv); spreadsheet converted by `evals/golden_eval.py convert`).
Their labels were never used in prompts, examples, thresholds or grouping. Predictions come from the same
`enrich_v1` configuration ([`runs/golden`](runs/golden)); the prompt was **not** revised after seeing golden results.

| field | agreement with my labels (n=50) | notes |
|---|---|---|
| topic | **80.0%** (40/50) | per-topic: other 27/29, usability 5/7, billing 3/4, catalog 2/4, playback 2/4, downloads 1/1, support 0/1 |
| intent | **90.0%** (45/50) | |
| severity | **84.0%** exact, MAE **0.20** | 7 of 8 misses are one level; 723f07de is off by 3 (see below) |
| sentiment | **96.0%** within the predeclared ±0.5 tolerance, MAE 0.10 | |
| evidence_quote | 50/50 exact substrings | short reviews use full text by design |
| entities | 0 records with entities not in the text | code removes unsupported entities |
| needs_review as a predictor of my ambiguous/needs-review flags | TP 7, FP 5, FN 2 | a useful but noisy signal |
| ambiguous cases (my flag) | 8; model matched my topic on 6/8 | missing/quarantined predictions would count as wrong; none occurred |

Confusion tables: `evals/golden_results.json` -> `confusion`. Predictions are from the final full run (`runs/full/records.jsonl`);
an earlier standalone run on the same 50 (`runs/golden`) gave 82% / 92% / 86%, showing run-to-run variation of about one or two reviews.

**Error analysis (all 17 disagreements are listed in [`evals/golden_cases.csv`](evals/golden_cases.csv)):**
- **Topic boundary disagreements dominate (10 of 17 rows with any miss).** usability vs other for generic "bad update" complaints (991b6b3a, 99b93e31); usability vs billing for free-tier control limits (3ddb3f4e: I chose billing because the review says controls are pushed behind Premium; the model chose usability); usability vs playback for loud ads (46c0b49f) and "can't play the songs I like" (dceb14e7).
- **Clear model errors:** 723f07de - an "Update: support fixed it" review was labeled playback complaint severity 4 from the *old* part of the post (I labeled support praise 1); 215463ae - "can't play a song ... now I can't even open the app" was labeled access instead of playback.
- **Ambiguous / unsupported-language cases:** Tagalog "Lhat ng song nasa spotify" (praise vs unclear), Indonesian lyrics complaint (severity 3 vs 2), the Sweden boycott review (model: cancellation; me: unclear by the boycott rule), the Hindi/Urdu one-word insult and an incoherent dictated review (both agreed: other/unclear). Non-English text was classified by meaning, not translated.
- **Severity** misses are one level except 723f07de (e.g. 47f1406d "cannot playback the songs": 4 vs 3).
- I did **not** revise the prompt after seeing these results, so the golden set remains a held-out check.

## Independent verification, planted errors, injection

- **Verifier:** a separate prompt ([`prompts/verify_v1.txt`](prompts/verify_v1.txt)) re-labels a seeded random sample of
  1,000 completed distinct texts seeing only the text and rubric; code compares topic/intent/severity
  ([`runs/full/verify_report.json`](runs/full/verify_report.json)). All 1,000 verifier outputs were valid; agreement topic 84.9%, intent 90.5%, severity 85.5% (MAE 0.15), all three 69.5%. The 305 disagreement rows are mostly usability/billing/other topic boundaries and one-level severity differences - the same pattern as the golden set - so the verifier is a consistency check, not ground truth.
- **Planted errors:** a synthetic test copy flips the topic on 25 records where both roles agreed; the comparison flags
  25/25 ([`planted_error_test.json`](runs/full/planted_error_test.json)). Real records are untouched.
- **Injection:** 5 synthetic reviews with embedded instructions (fake "SYSTEM:" text, a fake closing tag, a request to
  reveal the prompt, a pipe-formatted fake answer, "respond only with OK") were classified by the rules in all 5 cases
  ([`evals/system_tests_results.json`](evals/system_tests_results.json)). Synthetic rows live in `runs/system_tests`
  and never enter business aggregates.
- **Malformed output:** parser tests for non-numeric fields, invalid labels, missing items, extra prose, out-of-range
  and duplicate indexes, and non-exact quotes (`python evals/system_tests.py offline`).

## Retry, spending and recovery controls

- **Bounded requests:** <=50 reviews and <=40,000 characters per enrichment request; output cap 4,000 tokens.
- **Invalid output:** item retried once, then quarantined with reason and attempt count.
- **Transient errors** (429/5xx/connection/timeout): up to 4 retries, exponential backoff + jitter, honoring `retry-after`;
  every failed attempt is logged (`outcome:"failed"`). Demonstrated with simulated failures in `evals/system_tests_results.json`.
- **Spend cap:** one global ledger (`ledger/spend.jsonl`); before each sync request the worst-case cost is reserved and
  the call is refused when spent + reserved + next > $10. Batches stay reserved while in flight; the full run held back its
  fourth batch (`holding 2161 requests: spent $0.25 + in-flight worst $8.72 + next $2.56 > cap $10.00`, `runs/full_batch.log`)
  until earlier batches were billed.
- **Recovery:** results are written in one SQLite transaction per request; Ctrl-C finishes in-flight work and saves; batch
  IDs are persisted before polling so a restart re-polls instead of resubmitting.

### Interruption and resume

[`scripts/resume_demo.sh`](scripts/resume_demo.sh) on the real full corpus (transcript:
[`docs/resume_demo_transcript.log`](docs/resume_demo_transcript.log); screen recording: [`resume_demo_recording.m4v`](https://github.com/vivianqlam/spotify-review-pipeline/releases/download/v1.0/resume_demo_recording.m4v) (GitHub release asset; a second run of the same script on a separate copy of the corpus, `runs/resume_demo_recorded`, because the first live demo on `runs/full` was recorded without the terminal visible - its text transcript is the evidence for the grading checkpoints)):
1. initial phase, 1 worker: 497 distinct texts completed (covering 63,821 review IDs) -> SIGINT -> in-flight requests saved;
2. `checkpoint_before.json`: 63,821 completed IDs;
3. resume phase: 500 more distinct texts; **0 resume-phase calls re-sent a checkpointed ID**;
4. `checkpoint_after.json`: 77,701 IDs (`before ⊂ after`). The remaining texts then ran in the resume phase via the Batch API.

## Ranking

Baseline (required): completed `complaint` and `cancellation` records, one issue each (`allow_multi_issue=false`);
`complaint_count` = members, `severity_sum` = sum of member severity, `mean_severity` = sum/count (6 decimals, half-up),
`priority_score = complaint_count x mean_severity = severity_sum`; order by score desc, then issue_id asc.
Issues = topic + first matching keyword subtopic (else `general`); names proposed by the grouper from <=6 example quotes
per issue and accepted only for existing IDs. Re-run with `python run.py rerank --run-dir runs/full` (no model calls; checks identity).

| rank | issue_id | name (model-proposed) | complaints | cancellations | sev 4+ | severity_sum | mean_severity | priority_score |
|---|---|---|---|---|---|---|---|---|
| 1 | OTH-general | General dissatisfaction with the app | 93,866 | 9,321 | 195 | 184,272 | 1.963139 | 184,272 |
| 2 | USA-ads_interruptions | Too many unskippable ads | 29,771 | 2,207 | 43 | 62,012 | 2.082967 | 62,012 |
| 3 | BIL-general | Frustration with paywalled features generally | 24,254 | 5,082 | 576 | 61,012 | 2.515544 | 61,012 |
| 4 | BIL-premium_paywall_free_limits | Basic features locked behind premium | 17,349 | 4,997 | 446 | 47,212 | 2.721310 | 47,212 |
| 5 | PLA-general | Frequent bugs during playback | 12,266 | 486 | 3,045 | 37,202 | 3.032937 | 37,202 |
| 6 | USA-general | Ads and restrictive shuffle-only playback | 16,330 | 1,161 | 98 | 37,056 | 2.269198 | 37,056 |
| 7 | USA-shuffle_queue_controls | Shuffle forced, controls removed | 13,603 | 1,902 | 158 | 36,050 | 2.650151 | 36,050 |
| 8 | ACC-login_failure | Cannot log in via phone or Facebook | 6,445 | 375 | 5,773 | 25,037 | 3.884717 | 25,037 |
| 9 | PLA-stops_skips_pauses | Music stops unexpectedly | 6,623 | 504 | 1,187 | 20,756 | 3.133927 | 20,756 |
| 10 | USA-ui_redesign | Unwanted homepage and layout changes | 8,668 | 1,603 | 102 | 20,691 | 2.387056 | 20,691 |
| 11 | CAT-general | Unwanted songs and missing liked songs | 8,024 | 406 | 161 | 19,588 | 2.441176 | 19,588 |
| 12 | BIL-charges_refunds | Payment rejections and paid premium issues | 7,065 | 1,659 | 1,051 | 19,081 | 2.700778 | 19,081 |

All 34 issues: [`grading/ranking.csv`](grading/ranking.csv), [`runs/full/aggregates.csv`](runs/full/aggregates.csv), names in [`runs/full/issues.csv`](runs/full/issues.csv).

Product-area rollup used by the memo ([`runs/full/area_claims.csv`](runs/full/area_claims.csv)): access = access;
usability = usability; playback = playback + downloads; billing/support = billing + support. `catalog` and `other` are reported but not one of the four options.

## One review traced end to end

Review `f5bab94a-bad1-40b3-8e76-86f25ddbea17` (rating 5, 2022-08-12): *"Recently switched from iPhone to Android and boy this version of the app is glitchy! Have to restart it at least once a day... otherwise its v good."*

1. **Source:** row in `spotify_reviews_18months.csv`; `source_sha256 = ac515d8f...6c` (contract `row_sha`).
2. **Enrichment:** sent with 49 other texts in batch request `msgbatch_018QqkwzTKMW5goYLVpa2wix:r001362` (role `enrich`, phase `resume`, `grading/calls.jsonl.gz`) -> `topic=playback, intent=complaint, severity=3, sentiment=-0.5, entities=[Android], needs_review=false`, quote = full text (<=200 chars). Note the 5-star rating is ignored: the text reports degraded function.
3. **Verification:** in the seeded 1,000-text sample; verifier request `msg_011CgC88jAg3yXS9pTV8iD1f` independently returned `playback | complaint | 3` -> agreement on all fields (`runs/full/verify_predictions.json`).
4. **Issue membership:** no keyword subtopic matched -> `PLA-general` ("Frequent bugs during playback"), `grading/membership.csv`.
5. **Ranking:** contributes 3 to `PLA-general` severity_sum = **37,202** (12,266 complaints x mean 3.032937), rank 5 (`grading/ranking.csv`).
6. **Memo claim:** memo item 1 cites `PLA-general` 12,266 complaints **[C017]**, severity sum 37,202 **[C018]**, mean 3.032937 **[C019]** (`grading/claims.csv`), and the playback area total 40,662 [A13].

**A failed case and its handling:** `d18206ab-e90d-45f1-95a8-486d50c61793` ("Very baad and foolish aap i can't find my play list in this aap...") - the enricher twice returned the invented topic `library` (`library|complaint|3|-1.0|1`). Code rejected it both times (not in the allowed topic list), retried once as the policy allows, then **quarantined** it with `reason="invalid_model_output: invalid value(s): library|complaint|3|-1.0|1"`, `attempts=2` (`runs/full/quarantine.jsonl`). It is excluded from aggregates and counted as an incomplete classification; the other 5 such cases are listed in the same file (invented intents `other`, topic `playlist`).

## Grading export and self-check

[`grading/`](grading/) follows `GRADING_CONTRACT.md`: `run.json`, `ingestion.json`, `records.jsonl.gz`, `membership.csv`,
`ranking.csv`, `claims.csv`, `calls.jsonl.gz`, `checkpoint_before.json`, `checkpoint_after.json`.
Self-check result (`self-check.json`, kept outside `grading/`): `status: review_required` with a single issue, `unfinished_classification: 1` - the 6 quarantined nonempty rows. Coverage: 660,622/660,622 IDs, 0 missing/duplicate/invalid, 660,603 valid completed, 19 quarantined (13 empty + 6), 176,420 valid cache reuses; `working_coverage_point_candidate = 1.0` (0.2 x profile match + 0.2 x 1.0 + 0.6 x 0.999991). No ranking, claim, call, checkpoint or resume-evidence issues.

## Limitations

- Reviews are self-selected, historical Google Play reviews; they are not a customer population, carry no revenue,
  plan tier or churn data. Cancellation language is expressed intent, not observed churn. No revenue-at-risk or causal claims.
- The first (May 2022) and last (Nov 2023) months are partial.
- Labels come from a small model at low effort. Golden agreement is measured on only 50 reviews (a diagnostic, not a population estimate);
  the verifier disagrees on 305 of 1,000 sampled texts, mostly on topic boundaries (usability vs billing vs playback).
- Many reviews are short and generic ("bad app"); these become `OTH-general`, which leads the baseline ranking but is not actionable.
- Subtopics come from keyword rules, so some complaints land in `<topic>-general`; issue names are model-written from at most 6 examples.
- Non-English reviews are classified by meaning, with `needs_review` set when unclear; they were not translated.
- The baseline score ignores likes, recency and app version; it rewards volume of common complaints.
- 6 nonempty reviews (0.0009%) remain unclassified after the retry policy; 142,060 completed rows (21.5%) carry `needs_review=true` - mostly model-flagged ambiguity plus long reviews where the model left the quote blank (it cannot reliably count to 200 characters), so code used an exact opening excerpt instead (fallback quote, flagged). Those quotes are exact but may not be the best supporting span.
