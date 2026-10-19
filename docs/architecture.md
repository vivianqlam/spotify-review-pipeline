# Architecture

```mermaid
flowchart TD
    CSV[(input CSV<br/>any path)] --> P1

    subgraph CODE1 [code only]
      P1[1 Prepare / ingest<br/>parse, hash rows, profile,<br/>dedupe exact texts, quarantine empty]
    end
    P1 -->|state.db: reviews + distinct texts, pending queue<br/>ingestion_report.json, ingestion.json, data_manifest.json| P2

    subgraph M1 [model role: ENRICHER - claude-haiku-5-5, thinking off, effort low, prompt enrich_v1]
      P2[2 Classify<br/>&le;50 reviews / request<br/>sync or Message Batches]
    end
    P2 -->|raw lines| V2{code validates<br/>IDs, labels, ranges,<br/>exact quote, entities}
    V2 -->|valid| S2[(state.db completed<br/>records.jsonl / enriched.jsonl)]
    V2 -->|invalid: retry once| P2
    V2 -->|invalid twice / request failed 3x| Q[(quarantine.jsonl<br/>reason + attempts)]
    P2 -. 429/5xx/timeout: bounded backoff + jitter .-> P2
    P2 -. spend cap reached: stop, state saved .-> STOP([stop & resume later])

    S2 --> P3
    subgraph M2 [model role: VERIFIER - separate prompt verify_v1]
      P3[3 Verify<br/>seeded random sample,<br/>sees text + rubric only]
    end
    P3 --> C3{code compares<br/>topic/intent/severity}
    C3 --> R3[(verify_report.json<br/>verify_disagreements.csv<br/>planted_error_test.json)]

    S2 --> P4
    subgraph CODE4 [code]
      P4[4 Group<br/>complaint + cancellation records:<br/>topic + keyword subtopic -> stable issue_id]
    end
    P4 -->|membership.csv| P4M
    subgraph M4 [model role: GROUPER - prompt group_v1]
      P4M[name issues from bounded<br/>evidence pack; cannot change membership]
    end
    P4M -->|issues.json / issues.csv| P5
    subgraph CODE5 [code only - no model]
      P5[5 Rank<br/>priority = count x mean severity = severity_sum<br/>tie-break issue_id asc]
    end
    P5 -->|ranking.csv, aggregates.csv, topic_aggregates.csv| P6
    subgraph M6 [model role: MEMO WRITER - claude-sonnet-5-5, prompt memo_v2]
      P6[6 Recommend<br/>reads claims + evidence pack only]
    end
    P6 --> C6{code checks claim IDs,<br/>issue IDs, review IDs, numbers}
    C6 --> MEMO[(memo_draft.md -> memo.md<br/>memo_check.json, claims.csv)]
```

## Who decides what

| Stage | Input | Output | Owner | Failure behavior | Stop condition |
|---|---|---|---|---|---|
| 1 Prepare | input CSV | `state.db`, `ingestion_report.json`, `ingestion.json`, `data_manifest.json` | code | malformed CSV raises; empty text -> quarantine `empty_review_text` | every row read |
| 2 Classify | pending distinct texts (<=50/request) | completed results, `calls.jsonl`, `validation_errors.jsonl` | Haiku 5.5 reads language; code validates and saves after each request | invalid item retried once then quarantined; transient API errors retried 4x with backoff; request failing 3x -> quarantined | no pending texts, Ctrl-C (in-flight saved), or spend cap |
| 3 Verify | seeded sample of completed texts (text only) | verifier predictions, agreement + disagreements, planted-error test | Haiku 5.5 (separate prompt); code compares | missing verifier lines counted, not guessed | sample done |
| 4 Group | completed complaint/cancellation records | `membership.csv`, `issues.json` | code assigns members; Haiku 5.5 only names issues | unparseable/unknown name lines rejected, code default name kept | every complaint in exactly one issue |
| 5 Rank | records + membership | `ranking.csv`, `aggregates.csv` | code only | n/a (deterministic) | sorted table |
| 6 Recommend | claims + aggregates + evidence pack | memo, `memo_check.json` | Sonnet 5.5 writes; code checks; a person reviews | unsupported IDs/numbers flagged | memo check + human review |

The orchestrator (`run.py`) is a fixed sequence: code chooses the next step, owns record accounting, budgets
(`ledger/spend.jsonl`, reserve-before-dispatch), retries, and logs. Models exercise judgment only inside
the four bounded roles above, each with its own prompt file, model/settings, inputs, outputs and call log.
