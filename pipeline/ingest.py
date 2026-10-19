"""Stage 1 - Prepare (code only, no model).

Reads every row, preserves IDs and values, hashes each source row, profiles
quality, finds exact duplicate texts, quarantines empty texts, and queues one
pending task per distinct nonempty text.
"""
import sys
import time
from collections import Counter

from . import config
from .io_utils import file_sha, read_rows, row_sha, text_sha, write_json
from .state import State

sys.path.insert(0, str(config.ROOT))
import check_submission  # provided deterministic profiler (exact grading format)


def ingest(input_csv, run_dir, run_id):
    t0 = time.time()
    st = State(run_dir)
    if st.meta_get("ingested"):
        print(f"[ingest] already ingested {st.meta_get('input_csv')}; reusing saved state")
        return st.meta_get("ingestion_report")
    ids, texts_seen = set(), {}
    stats = Counter()
    ratings, months, missing_by_field = Counter(), Counter(), Counter()
    dup_text_rows = 0
    reviews, texts = [], []
    for ord_, row in enumerate(read_rows(input_csv)):
        stats["rows"] += 1
        rid = row["review_id"]
        if rid in ids:
            stats["duplicate_review_ids"] += 1
        ids.add(rid)
        for k, v in row.items():
            if not v.strip():
                missing_by_field[k] += 1
        ratings[row["review_rating"]] += 1
        months[row["review_timestamp"][:7]] += 1
        t = row["review_text"]
        empty = not t.strip()
        tsha = text_sha(t)
        if empty:
            rep = None
        elif tsha in texts_seen:
            rep = texts_seen[tsha]
            dup_text_rows += 1
        else:
            rep = rid
            texts_seen[tsha] = rid
            texts.append((tsha, rid, ord_, t))
        reviews.append((rid, ord_, row_sha(row), tsha, rep, int(empty)))
    with st.tx() as db:
        db.executemany("INSERT OR IGNORE INTO reviews VALUES (?,?,?,?,?,?)", reviews)
        db.executemany("INSERT OR IGNORE INTO texts (text_sha, rep_id, rep_ord, text) VALUES (?,?,?,?)", texts)
    report = {
        "run_id": run_id,
        "input_csv": str(input_csv),
        "input_sha256": file_sha(input_csv),
        "source_rows": stats["rows"],
        "unique_review_ids": len(ids),
        "duplicate_review_ids": stats["duplicate_review_ids"],
        "empty_review_text": missing_by_field["review_text"],
        "nonempty_reviews": stats["rows"] - missing_by_field["review_text"],
        "distinct_nonempty_texts": len(texts),
        "rows_reusing_an_identical_text": dup_text_rows,
        "missing_values_by_field": dict(missing_by_field),
        "missing_app_version": missing_by_field["app_version"],
        "reviews_by_rating": dict(sorted(ratings.items())),
        "reviews_by_month": dict(sorted(months.items())),
        "quarantine_rule": "review_text empty after whitespace strip -> quarantined, reason empty_review_text",
        "exclusions": "none besides empty texts; missing app_version does not block classification",
        "seconds": round(time.time() - t0, 2),
    }
    write_json(run_dir / "ingestion_report.json", report)
    write_json(run_dir / "ingestion.json", check_submission.profile(str(input_csv)))
    write_json(run_dir / "data_manifest.json", {
        "run_id": run_id, "input_csv": str(input_csv), "input_sha256": report["input_sha256"],
        "label_config": config.LABEL_CONFIG,
        "prompts": {v: file_sha(config.PROMPTS / f"{v}.txt") for v in (
            config.ENRICH_PROMPT_VERSION, config.VERIFY_PROMPT_VERSION, config.GROUP_PROMPT_VERSION, config.MEMO_PROMPT_VERSION)
            if (config.PROMPTS / f"{v}.txt").exists()},
        "models": {"enrich": config.ENRICH_MODEL, "verify": config.VERIFY_MODEL, "group": config.GROUP_MODEL, "memo": config.MEMO_MODEL},
    })
    st.meta_set("ingested", True)
    st.meta_set("input_csv", str(input_csv))
    st.meta_set("ingestion_report", report)
    print(f"[ingest] {report['source_rows']} rows, {report['distinct_nonempty_texts']} distinct nonempty texts, "
          f"{report['empty_review_text']} empty -> quarantine ({report['seconds']}s)")
    return report
