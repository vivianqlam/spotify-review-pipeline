#!/bin/zsh
# Post-enrichment stages for the final full-corpus run, then export and self-check.
set -e
cd "$(dirname "$0")/.."
PY=.venv/bin/python
RUN=runs/full
$PY run.py verify  --run-dir $RUN
$PY run.py records --run-dir $RUN
$PY evals/golden_eval.py evaluate --records $RUN/records.jsonl          # official golden numbers from the full run
$PY run.py group   --run-dir $RUN
$PY run.py rank    --run-dir $RUN
$PY run.py memo    --run-dir $RUN
$PY run.py rerank  --run-dir $RUN
$PY run.py status  --run-dir $RUN > /dev/null
$PY run.py export  --run-dir $RUN --out grading
$PY check_submission.py profile --full data/spotify_reviews_18months.csv --out /tmp/ingestion_check.json
cmp -s /tmp/ingestion_check.json grading/ingestion.json && echo "ingestion.json matches checker profile"
$PY check_submission.py check --reference local-reference.json --submission grading --out self-check.json
$PY -c "import json;d=json.load(open('self-check.json'));print(d['status'], d['working_coverage_point_candidate'], json.dumps(d['coverage']), d['issue_counts'])"
$PY cost/report.py
