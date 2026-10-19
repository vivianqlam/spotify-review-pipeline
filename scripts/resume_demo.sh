#!/bin/zsh
# Interruption / resume demonstration on the FULL corpus run (runs/full).
# 1) initial phase: live enrichment, interrupted with SIGINT (same as Ctrl-C) after ~45s
# 2) checkpoint_before: completed IDs saved before the interruption
# 3) resume phase: new completed work; already-completed IDs are never re-sent
# 4) checkpoint snapshot after resume
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
RUN=${1:-runs/full}
LOG=docs/resume_demo_transcript_$(basename $RUN).log
mkdir -p docs
exec > >(tee -a "$LOG") 2>&1
echo "=== resume demo $(date) ==="
if [ ! -f $RUN/state.db ]; then $PY run.py ingest --input data/spotify_reviews_18months.csv --run-dir $RUN; fi
echo "--- state before starting ---"
$PY run.py status --run-dir $RUN | head -12
echo "--- 1) initial phase (1 worker). Will be interrupted with SIGINT after 45s ---"
$PY run.py enrich --run-dir $RUN --phase initial --workers 1 &
PID=$!
sleep 45
echo ">>> sending SIGINT (Ctrl-C) to enrichment process $PID"
kill -INT $PID
wait $PID || true
echo "--- 2) checkpoint after interruption ---"
$PY run.py checkpoint --run-dir $RUN --name before
$PY -c "import sqlite3;db=sqlite3.connect('$RUN/state.db');print('texts by status:',dict(db.execute('select status,count(*) from texts group by status').fetchall()))"
echo "--- 3) resume phase: 10 more requests; completed IDs are skipped (no paid relabeling) ---"
$PY run.py enrich --run-dir $RUN --phase resume --workers 1 --max-requests 10
echo "--- 4) checkpoint after resume ---"
$PY run.py checkpoint --run-dir $RUN --name after
$PY - $RUN <<'EOF'
import json
import sys
run = sys.argv[1]
b = set(json.load(open(f"{run}/checkpoint_before.json"))["completed_ids"])
a = set(json.load(open(f"{run}/checkpoint_after.json"))["completed_ids"])
calls = [json.loads(l) for l in open(f"{run}/calls.jsonl")]
resume_ids = {i for c in calls if c["role"] == "enrich" and c["phase"] == "resume" for i in c["review_ids"]}
print(f"before={len(b)} after={len(a)} before_subset_of_after={b < a} new_completed={len(a - b)}")
print(f"resume-phase enrichment calls that re-sent a checkpointed ID: {len(resume_ids & b)}")
EOF
echo "=== demo complete $(date) ==="
