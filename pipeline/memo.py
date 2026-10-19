"""Stage 6 - Recommend (memo-writer role).

The memo model receives ONLY saved aggregates, claim tables and a bounded
evidence pack - never the raw dataset. Code then checks every cited claim ID,
issue ID, review ID and number against the saved calculations.
"""
import csv
import hashlib
import json
import re
from collections import Counter, defaultdict

from . import config, llm
from .analysis import COMPLAINT_INTENTS, mean_string
from .io_utils import read_jsonl, write_csv, write_json

AREAS = {"access": ["access"], "usability": ["usability"], "playback": ["playback", "downloads"],
         "billing/support": ["billing", "support"]}
TOP_ISSUES = 10


def _rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def build_claims(run_dir, records):
    ranking = _rows(run_dir / "ranking.csv")
    issues = {i["issue_id"]: i for i in json.loads((run_dir / "issues.json").read_text())["issues"]}
    claims, n = [], 0
    for row in ranking[:TOP_ISSUES]:
        for metric in ("complaint_count", "severity_sum", "mean_severity", "priority_score"):
            n += 1
            claims.append({"claim_id": f"C{n:03d}", "issue_id": row["issue_id"], "metric": metric, "value": row[metric]})
    write_csv(run_dir / "claims.csv", ["claim_id", "issue_id", "metric", "value"], claims)
    # Area/topic-level context claims (not part of the issue-level grading claims file).
    completed = [r for r in records if r["status"] == "completed"]
    complaints = [r for r in completed if r["intent"] in COMPLAINT_INTENTS]
    area_rows, k = [], 0
    total = len(complaints)
    for area, topics in AREAS.items():
        rs = [r for r in complaints if r["topic"] in topics]
        s = sum(r["severity"] for r in rs)
        vals = {"complaint_count": len(rs), "severity_sum": s, "mean_severity": mean_string(s, len(rs)) if rs else "0",
                "share_of_all_complaints_pct": f"{100 * len(rs) / total:.1f}" if total else "0",
                "severity_4_plus": sum(r["severity"] >= 4 for r in rs),
                "cancellations": sum(r["intent"] == "cancellation" for r in rs)}
        for metric, v in vals.items():
            k += 1
            area_rows.append({"claim_id": f"A{k:02d}", "area": area, "topics": "+".join(topics), "metric": metric, "value": str(v)})
    k += 1
    area_rows.append({"claim_id": f"A{k:02d}", "area": "all", "topics": "all", "metric": "complaint_count", "value": str(total)})
    write_csv(run_dir / "area_claims.csv", ["claim_id", "area", "topics", "metric", "value"], area_rows)
    return claims, area_rows, ranking, issues


def evidence_pack(run_dir, records, ranking, issues, per_issue=3):
    recs = {r["review_id"]: r for r in records}
    members = defaultdict(list)
    for m in _rows(run_dir / "membership.csv"):
        members[m["issue_id"]].append(recs[m["review_id"]])
    pack = []
    for row in ranking[:6]:
        seen, ex = set(), []
        for r in sorted(members[row["issue_id"]], key=lambda r: hashlib.sha256(r["review_id"].encode()).hexdigest()):  # representative sample
            if r.get("cache_source_id") or r["evidence_quote"] in seen or len(r["evidence_quote"]) < 25:
                continue
            seen.add(r["evidence_quote"])
            ex.append({"review_id": r["review_id"], "severity": r["severity"], "intent": r["intent"], "quote": r["evidence_quote"][:220]})
            if len(ex) == per_issue:
                break
        pack.append({"issue_id": row["issue_id"], "name": issues[row["issue_id"]]["name"], "examples": ex})
    return pack


def write(run_dir, run_id, records, context, phase="initial", budget=None):
    claims, area_claims, ranking, issues = build_claims(run_dir, records)
    pack = evidence_pack(run_dir, records, ranking, issues)
    table = "\n".join(f"{c['claim_id']},{c['issue_id']},{issues[c['issue_id']]['name']},{c['metric']},{c['value']}" for c in claims)
    areas = "\n".join(f"{a['claim_id']},{a['area']},{a['topics']},{a['metric']},{a['value']}" for a in area_claims)
    payload = {"issue_claims_csv": "claim_id,issue_id,issue_name,metric,value\n" + table,
               "area_claims_csv": "claim_id,area,topics,metric,value\n" + areas,
               "evidence_pack": pack, "context": context}
    if (run_dir / "memo_inputs.json").exists() and (run_dir / "memo_draft.md").exists() and \
            json.loads((run_dir / "memo_inputs.json").read_text()) == json.loads(json.dumps(payload)):
        text = (run_dir / "memo_draft.md").read_text(encoding="utf-8")
        print("[memo] inputs unchanged; reused saved memo (0 model calls)")
        report = check(text, claims, area_claims, pack, issues, context)
        report["reused"] = True
        write_json(run_dir / "memo_check.json", report)
        return text, report
    write_json(run_dir / "memo_inputs.json", payload)
    system = (config.PROMPTS / f"{config.MEMO_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    user = "Saved analysis inputs (JSON):\n" + json.dumps(payload, ensure_ascii=False, indent=1)
    text, usage, rid, stop = llm.call(role="memo", model=config.MEMO_MODEL, system=system, user=user, max_tokens=6000,
                                     settings=config.MEMO_SETTINGS, review_ids=[], calls_path=run_dir / "calls.jsonl",
                                     run_id=run_id, phase=phase, label_config=f"{config.MEMO_MODEL}+{config.MEMO_PROMPT_VERSION}",
                                     budget=budget, extra={"input_artifact": "memo_inputs.json"})
    (run_dir / "memo_draft.md").write_text(text, encoding="utf-8")
    report = check(text, claims, area_claims, pack, issues, context)
    report["request_id"] = rid
    write_json(run_dir / "memo_check.json", report)
    return text, report


def _num_forms(v):
    v = str(v)
    out = {v}
    try:
        f = float(v)
        out |= {f"{f:,.0f}", f"{f:.0f}", f"{f:.1f}", f"{f:.2f}", f"{f:,.1f}"}
        if f == int(f):
            out.add(f"{int(f):,}")
    except ValueError:
        pass
    return out


def check(text, claims, area_claims, pack, issues, context):
    ids = {c["claim_id"] for c in claims} | {a["claim_id"] for a in area_claims}
    cited = set(re.findall(r"\b([CA]\d{2,3})\b", text))
    pack_ids = {e["review_id"] for p in pack for e in p["examples"]}
    review_ids = set(re.findall(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text))
    issue_ids = set(re.findall(r"\b([A-Z]{3}-[a-z_]+)\b", text))
    allowed = set()
    for c in list(claims) + list(area_claims):
        allowed |= _num_forms(c["value"])
    for v in _flatten(context):
        allowed |= _num_forms(v)
    numbers = set(re.findall(r"(?<![\w.-])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?![\w-])", re.sub(r"[0-9a-f]{8}-[0-9a-f-]{27}", "", re.sub(r"\b[CA]\d{2,3}\b", "", text))))
    trivial = {str(i) for i in range(0, 11)} | {"2022", "2023", "2026"}
    unsupported = sorted(n for n in numbers if n not in allowed and n not in trivial)
    return {"cited_claim_ids": sorted(cited), "unknown_claim_ids": sorted(cited - ids),
            "review_ids_cited": sorted(review_ids), "review_ids_not_in_evidence_pack": sorted(review_ids - pack_ids),
            "issue_ids_cited": sorted(issue_ids), "unknown_issue_ids": sorted(issue_ids - set(issues)),
            "numbers_checked": len(numbers), "unsupported_numbers": unsupported,
            "pass": not (cited - ids or review_ids - pack_ids or issue_ids - set(issues) or unsupported),
            "note": "Automated check of IDs and numbers only; a person still reviews the argument."}


def _flatten(x):
    if isinstance(x, dict):
        for v in x.values():
            yield from _flatten(v)
    elif isinstance(x, list):
        for v in x:
            yield from _flatten(v)
    elif isinstance(x, (int, float, str)) and re.fullmatch(r"-?\d[\d,]*(\.\d+)?", str(x)):
        yield x
