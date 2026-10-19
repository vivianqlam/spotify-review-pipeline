"""Stage 3 - Verify (independent verifier role).

A separate prompt re-labels a declared random sample WITHOUT seeing the first
prediction. Code compares the two; disagreements are saved for inspection.
`planted_error_test` re-runs the comparison on a test copy with deliberately
wrong labels to prove the check catches them (synthetic; never exported).
"""
import json
import random
from collections import Counter

from . import config, llm
from .enrich import build_user, chunk
from .io_utils import write_csv, write_json

FIELDS = ("topic", "intent", "severity")


def parse(output, n):
    got = {}
    for line in output.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 4 or not parts[0].isdigit():
            continue
        i = int(parts[0])
        try:
            sev = int(parts[3])
        except ValueError:
            continue
        if 1 <= i <= n and parts[1] in config_topics() and parts[2] in config_intents() and 1 <= sev <= 5:
            got[i] = {"topic": parts[1], "intent": parts[2], "severity": sev}
    return got


def config_topics():
    from .enrich import TOPICS
    return TOPICS


def config_intents():
    from .enrich import INTENTS
    return INTENTS


def sample(st, size, seed=config.VERIFY_SEED):
    rows = st.db.execute("SELECT text_sha, rep_id, text, attempts, result FROM texts WHERE status='completed' ORDER BY rep_ord").fetchall()
    rng = random.Random(seed)
    picked = rows if len(rows) <= size else rng.sample(rows, size)
    return sorted(picked, key=lambda r: r[1])


def run(st, run_dir, run_id, size=config.VERIFY_SAMPLE_SIZE, phase="initial", budget=None):
    out_path = run_dir / "verify_predictions.json"
    saved = json.loads(out_path.read_text()) if out_path.exists() else {}
    system = (config.PROMPTS / f"{config.VERIFY_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    picked = sample(st, size)
    todo = [r for r in picked if r[1] not in saved]
    for items in chunk([(r[0], r[1], r[2], r[3]) for r in todo]):
        ids = [x[1] for x in items]
        text, usage, rid, _ = llm.call(role="verify", model=config.VERIFY_MODEL, system=system, user=build_user(items),
                                      max_tokens=1500, settings=config.VERIFY_SETTINGS, review_ids=ids,
                                      calls_path=run_dir / "calls.jsonl", run_id=run_id, phase=phase,
                                      label_config=f"{config.VERIFY_MODEL}+{config.VERIFY_PROMPT_VERSION}", budget=budget)
        got = parse(text, len(items))
        for i, (_, rep, _, _) in enumerate(items, 1):
            saved[rep] = got.get(i, {"error": "missing_from_verifier_output"})
        out_path.write_text(json.dumps(saved, indent=1, sort_keys=True))
    return compare(st, run_dir, picked, saved)


def compare(st, run_dir, picked, verifier, label="verify", planted=None):
    first = {r[1]: json.loads(r[4]) for r in picked}
    texts = {r[1]: r[2] for r in picked}
    agree, n_valid = Counter(), 0
    rows = []
    for rep, v in verifier.items():
        if rep not in first or "error" in v:
            continue
        f = dict(first[rep])
        if planted and rep in planted:
            f.update(planted[rep])
        n_valid += 1
        diffs = [k for k in FIELDS if f[k] != v[k]]
        for k in FIELDS:
            agree[k] += f[k] == v[k]
        agree["all"] += not diffs
        if diffs:
            rows.append({"review_id": rep, "fields": ";".join(diffs), "enricher": json.dumps({k: f[k] for k in FIELDS}),
                         "verifier": json.dumps(v), "planted": bool(planted and rep in planted),
                         "text": texts[rep][:300]})
    sev_mae = sum(abs(first[r]["severity"] - v["severity"]) for r, v in verifier.items()
                  if r in first and "error" not in v) / max(1, n_valid)
    report = {"sample_size": len(picked), "verifier_valid": n_valid,
              "verifier_missing": sum("error" in v for r, v in verifier.items() if r in first),
              "agreement": {k: round(agree[k] / max(1, n_valid), 4) for k in (*FIELDS, "all")},
              "severity_mae_between_roles": round(sev_mae, 4), "disagreements": len(rows),
              "procedure": "Declared seeded random sample of completed distinct texts; verifier prompt sees only review text "
                           "and the rubric, never the enricher's labels; code compares topic/intent/severity."}
    write_json(run_dir / f"{label}_report.json", report)
    write_csv(run_dir / f"{label}_disagreements.csv", ["review_id", "fields", "enricher", "verifier", "planted", "text"], rows)
    return report


def planted_error_test(st, run_dir, n=25, seed=7):
    """Synthetic test copy: flip the enricher topic on n verified records where both roles agreed."""
    verifier = json.loads((run_dir / "verify_predictions.json").read_text())
    picked = sample(st, config.VERIFY_SAMPLE_SIZE)
    first = {r[1]: json.loads(r[4]) for r in picked}
    agreeing = sorted(r for r, v in verifier.items() if r in first and "error" not in v
                      and all(first[r][k] == v[k] for k in FIELDS))
    rng = random.Random(seed)
    chosen = rng.sample(agreeing, min(n, len(agreeing)))
    from .enrich import TOPICS
    planted = {}
    for r in chosen:
        wrong = [t for t in TOPICS if t != first[r]["topic"]]
        planted[r] = {"topic": rng.choice(wrong)}
    rep = compare(st, run_dir, picked, {r: verifier[r] for r in chosen}, label="planted_error", planted=planted)
    caught = rep["disagreements"]
    result = {"synthetic": True, "planted": len(planted), "caught_by_verifier_comparison": caught,
              "catch_rate": round(caught / max(1, len(planted)), 4), "planted_labels": planted,
              "note": "Test copy only; real records are unchanged and these rows never enter business aggregates."}
    write_json(run_dir / "planted_error_test.json", result)
    return result
