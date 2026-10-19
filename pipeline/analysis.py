"""Stages 4-5 - Group and Rank, plus the final record table.

Code owns membership and arithmetic. The grouper model only proposes readable
names for code-defined issues from a bounded evidence pack; the accepted
mapping is saved so ranking never needs a model again.
"""
import hashlib
import json
import re
from collections import Counter, defaultdict
from decimal import ROUND_HALF_UP, Decimal

from . import config, llm
from .io_utils import read_jsonl, write_csv, write_json, write_jsonl

COMPLAINT_INTENTS = {"complaint", "cancellation"}
PREFIX = {"access": "ACC", "usability": "USA", "playback": "PLA", "downloads": "DOW", "catalog": "CAT",
          "billing": "BIL", "support": "SUP", "other": "OTH"}
# Ordered keyword rules: first match wins inside the record's topic; else "general".
SUBTOPICS = {
    "access": [("password_reset", r"password|reset|forgot"),
               ("account_hacked_or_lost", r"hack|stolen|someone else|lost my account|account (?:was |got )?(?:disabled|banned|suspended|deleted)"),
               ("login_failure", r"log ?in|sign ?in|logged out|log out|login|logging"),
               ("signup", r"sign ?up|create (?:an )?account|register")],
    "usability": [("ads_interruptions", r"\bads?\b|advert|commercial"),
                  ("shuffle_queue_controls", r"shuffle|queue|repeat|skip|button|control"),
                  ("library_playlist_management", r"playlist|library|liked songs|sort|folder|organi"),
                  ("ui_redesign", r"update|design|layout|interface|\bui\b|home|look|feed|scroll")],
    "playback": [("crash_or_wont_open", r"crash|won'?t open|doesn'?t open|not opening|keeps closing|force clos|close[sd]? (?:itself|by itself)"),
                 ("connectivity_and_devices", r"bluetooth|android auto|\bcar\b|connect|chromecast|speaker|watch|\btv\b|internet|network|wifi"),
                 ("stops_skips_pauses", r"stop|pause|skip|cut(?:s|ting)? off|interrupt"),
                 ("lag_loading", r"lag|slow|load|buffer|freez|stuck"),
                 ("audio_quality", r"quality|volume|sound|audio|loud"),
                 ("battery_storage_data", r"battery|storage|data usage|memory|space|cache")],
    "downloads": [("downloads_disappear", r"disappear|deleted|gone|re-?download|lost|removed"),
                  ("offline_playback", r"offline|no internet|without internet|airplane|no signal"),
                  ("download_failures", r"download")],
    "catalog": [("lyrics", r"lyric"),
                ("recommendations_repetition", r"recommend|algorithm|same songs|repeat|suggest|smart shuffle|daily mix|discover|dj\b"),
                ("search", r"search|find"),
                ("podcasts_audiobooks", r"podcast|audiobook|episode"),
                ("missing_or_removed_content", r"missing|removed|not available|unavailable|grey|gray|ban|censor|delet")],
    "billing": [("premium_paywall_free_limits", r"free|skip|shuffle|premium only|pay to|paywall|forced|limit|upgrade"),
                ("charges_refunds", r"charg|refund|money|payment|card|bill|paid"),
                ("price", r"price|expensive|cost|increase|\$|£|€|₹|rupee|afford"),
                ("subscription_management", r"subscri|cancel|trial|plan|family|student|duo")],
    "support": [],
    "other": [],
}


def mean_string(total, n):
    return str((Decimal(total) / Decimal(n)).quantize(Decimal("0.000001"), rounding=ROUND_HALF_UP))


def build_records(st):
    """Exactly one final record per source ID, in source order."""
    texts = {r[0]: r for r in st.db.execute("SELECT text_sha, rep_id, status, result, label_config, reason, attempts FROM texts")}
    records = []
    for rid, ord_, ssha, tsha, rep, empty in st.db.execute("SELECT review_id, ord, source_sha256, text_sha, rep_id, empty FROM reviews ORDER BY ord"):
        if empty:
            records.append({"review_id": rid, "source_sha256": ssha, "status": "quarantined", "reason": "empty_review_text", "attempts": 0})
            continue
        _, rep_id, status, result, lc, reason, attempts = texts[tsha]
        if status != "completed":
            records.append({"review_id": rid, "source_sha256": ssha, "status": "quarantined",
                            "reason": reason or f"not_completed ({status})", "attempts": attempts})
            continue
        res = json.loads(result)
        rec = {"review_id": rid, "source_sha256": ssha, "status": "completed", "topic": res["topic"], "intent": res["intent"],
               "sentiment": res["sentiment"], "severity": res["severity"], "entities": res["entities"],
               "evidence_quote": res["evidence_quote"], "needs_review": res["needs_review"], "label_config": lc}
        if rid != rep_id:
            rec["cache_source_id"] = rep_id
        records.append(rec)
    return records


def subtopic(topic, text):
    low = text.lower()
    for name, pat in SUBTOPICS.get(topic, []):
        if re.search(pat, low):
            return name
    return "general"


def group(st, run_dir, run_id, records=None, phase="initial", budget=None, use_model=True):
    records = records if records is not None else build_records(st)
    text_of = dict(st.db.execute("SELECT r.review_id, t.text FROM reviews r JOIN texts t ON r.text_sha=t.text_sha WHERE r.empty=0"))
    members = defaultdict(list)
    for rec in records:
        if rec["status"] == "completed" and rec["intent"] in COMPLAINT_INTENTS:
            iid = f"{PREFIX[rec['topic']]}-{subtopic(rec['topic'], text_of[rec['review_id']])}"
            members[iid].append(rec)
    issues = {}
    for iid in sorted(members):
        topic = next(t for t, p in PREFIX.items() if iid.startswith(p + "-"))
        issues[iid] = {"issue_id": iid, "topic": topic, "subtopic": iid.split("-", 1)[1],
                       "name": iid.split("-", 1)[1].replace("_", " "), "description": "", "name_source": "code_default"}
    membership = [{"issue_id": iid, "review_id": rec["review_id"]} for iid in sorted(members) for rec in members[iid]]
    write_csv(run_dir / "membership.csv", ["issue_id", "review_id"], membership)
    # Bounded evidence pack for naming: up to 6 representative quotes per issue.
    pack = {}
    for iid, recs in members.items():
        seen, ex = set(), []
        # Representative, not extreme: seeded pseudo-random order (sha256 of review_id), original records only.
        for rec in sorted(recs, key=lambda r: hashlib.sha256(r["review_id"].encode()).hexdigest()):
            q = rec["evidence_quote"][:200]
            if q in seen or rec.get("cache_source_id") or len(q) < 20:
                continue
            seen.add(q)
            ex.append({"review_id": rec["review_id"], "quote": q})
            if len(ex) == 6:
                break
        pack[iid] = ex
    prior_pack = json.loads((run_dir / "group_evidence_pack.json").read_text()) if (run_dir / "group_evidence_pack.json").exists() else None
    prior_issues = json.loads((run_dir / "issues.json").read_text())["issues"] if (run_dir / "issues.json").exists() else None
    write_json(run_dir / "group_evidence_pack.json", pack)
    if prior_pack == json.loads(json.dumps(pack)) and prior_issues and all(i["name_source"] != "code_default" for i in prior_issues):
        # Downstream result cache: identical bounded input + config -> reuse accepted names, no model call.
        for i in prior_issues:
            if i["issue_id"] in issues:
                issues[i["issue_id"]].update(name=i["name"], description=i["description"], name_source=i["name_source"])
        print("[group] evidence pack unchanged; reused saved issue names (0 model calls)")
    elif use_model and issues:
        _name_issues(issues, pack, members, run_dir, run_id, phase, budget)
    write_json(run_dir / "issues.json", {"issues": list(issues.values()), "rules": {t: [n for n, _ in r] for t, r in SUBTOPICS.items()},
                                          "membership_rule": "one issue per completed complaint/cancellation record: topic + first matching keyword subtopic, else general",
                                          "allow_multi_issue": False})
    write_csv(run_dir / "issues.csv", ["issue_id", "topic", "subtopic", "name", "description", "name_source", "members"],
              [{**v, "members": len(members[k])} for k, v in issues.items()])
    return issues, membership


def _name_issues(issues, pack, members, run_dir, run_id, phase, budget):
    system = (config.PROMPTS / f"{config.GROUP_PROMPT_VERSION}.txt").read_text(encoding="utf-8")
    blocks = []
    for iid in sorted(issues):
        lines = "\n".join(f"- ({e['review_id']}) {e['quote']}" for e in pack[iid])
        blocks.append(f"<issue id=\"{iid}\" topic=\"{issues[iid]['topic']}\" members=\"{len(members[iid])}\">\n{lines}\n</issue>")
    user = "Name each issue.\n\n" + "\n\n".join(blocks)
    text, usage, rid, _ = llm.call(role="group", model=config.GROUP_MODEL, system=system, user=user, max_tokens=4000,
                                  settings=config.GROUP_SETTINGS, review_ids=[], calls_path=run_dir / "calls.jsonl", run_id=run_id,
                                  phase=phase, label_config=f"{config.GROUP_MODEL}+{config.GROUP_PROMPT_VERSION}", budget=budget,
                                  extra={"input_artifact": "group_evidence_pack.json"})
    accepted, rejected = 0, []
    for line in text.splitlines():
        parts = [p.strip() for p in line.split("|")]
        if len(parts) != 3 or parts[0] not in issues:
            if line.strip():
                rejected.append(line[:200])
            continue
        name, desc = parts[1], parts[2]
        if 2 <= len(name) <= 80 and len(desc) <= 300:
            issues[parts[0]].update(name=name, description=desc, name_source=f"{config.GROUP_MODEL}:{rid}")
            accepted += 1
    write_json(run_dir / "group_naming_log.json", {"request_id": rid, "accepted": accepted, "rejected_lines": rejected,
                                                   "rule": "model may only name existing code-defined issue IDs; membership never changes"})


def rank(run_dir, records=None):
    """Deterministic baseline: priority = complaint_count x mean_severity = severity_sum. No model."""
    if records is None:  # fresh clones only have the compressed grading export (large files are not committed)
        p = run_dir / "records.jsonl"
        records = read_jsonl(p if p.exists() else config.ROOT / "grading" / "records.jsonl.gz")
    recs = {r["review_id"]: r for r in records}
    mpath = run_dir / "membership.csv"
    membership = list(_csv(mpath if mpath.exists() else config.ROOT / "grading" / "membership.csv"))
    issues = {i["issue_id"]: i for i in json.loads((run_dir / "issues.json").read_text())["issues"]}
    sev = defaultdict(list)
    cancels = Counter()
    for m in membership:
        r = recs[m["review_id"]]
        sev[m["issue_id"]].append(r["severity"])
        cancels[m["issue_id"]] += r["intent"] == "cancellation"
    rows = [{"issue_id": iid, "complaint_count": len(v), "severity_sum": sum(v), "mean_severity": mean_string(sum(v), len(v)),
             "priority_score": sum(v)} for iid, v in sev.items()]
    rows.sort(key=lambda x: (-x["priority_score"], x["issue_id"]))
    for k, row in enumerate(rows, 1):
        row["rank"] = k
    write_csv(run_dir / "ranking.csv", ["rank", "issue_id", "complaint_count", "severity_sum", "mean_severity", "priority_score"], rows)
    agg = [{**row, "topic": issues[row["issue_id"]]["topic"], "name": issues[row["issue_id"]]["name"],
            "cancellation_count": cancels[row["issue_id"]],
            "severity_4_plus": sum(s >= 4 for s in sev[row["issue_id"]])} for row in rows]
    write_csv(run_dir / "aggregates.csv", ["rank", "issue_id", "topic", "name", "complaint_count", "cancellation_count",
                                            "severity_4_plus", "severity_sum", "mean_severity", "priority_score"], agg)
    # Topic and product-area rollups (supporting context, not the baseline ranking).
    completed = [r for r in recs.values() if r["status"] == "completed"]
    by_topic = defaultdict(lambda: Counter())
    for r in completed:
        t = by_topic[r["topic"]]
        t["records"] += 1
        if r["intent"] in COMPLAINT_INTENTS:
            t["complaints"] += 1
            t["severity_sum"] += r["severity"]
            t["severity_4_plus"] += r["severity"] >= 4
            t["cancellations"] += r["intent"] == "cancellation"
    total_c = sum(t["complaints"] for t in by_topic.values())
    trows = [{"topic": k, "records": v["records"], "complaints": v["complaints"], "cancellations": v["cancellations"],
              "severity_sum": v["severity_sum"], "mean_severity": mean_string(v["severity_sum"], v["complaints"]) if v["complaints"] else "",
              "severity_4_plus": v["severity_4_plus"], "share_of_complaints": f"{v['complaints'] / total_c:.6f}" if total_c else ""}
             for k, v in sorted(by_topic.items(), key=lambda kv: -kv[1]["severity_sum"])]
    write_csv(run_dir / "topic_aggregates.csv", ["topic", "records", "complaints", "cancellations", "severity_sum", "mean_severity",
                                                 "severity_4_plus", "share_of_complaints"], trows)
    return rows


def _csv(path):
    import csv
    with open(path, encoding="utf-8", newline="") as f:
        yield from csv.DictReader(f)


def save_records(run_dir, records):
    write_jsonl(run_dir / "records.jsonl", records)
    write_jsonl(run_dir / "enriched.jsonl", [r for r in records if r["status"] == "completed"])
    write_jsonl(run_dir / "quarantine.jsonl", [r for r in records if r["status"] != "completed"])
