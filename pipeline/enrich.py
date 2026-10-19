"""Stage 2 - Classify (enricher role).

Code owns batching (<=50 reviews/request), validation, quote checking, retries,
state and accounting; the model only reads language and returns compact lines.
Two dispatch modes share the same prompt, parser and validator:
  sync  - Messages API, 1..MAX_WORKERS workers (pilot, dev checkpoints, resume demo)
  batch - Message Batches API at 50% price (bulk of the full run)
"""
import json
import re
import signal
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import config, llm
from .io_utils import append_jsonl

TOPICS = ("access", "usability", "playback", "downloads", "catalog", "billing", "support", "other")
INTENTS = ("cancellation", "complaint", "request", "praise", "unclear")
SENTIMENTS = (-1.0, -0.5, 0.0, 0.5, 1.0)
MAX_REQUEST_CHARS = 40000

_stop = threading.Event()


def system_prompt():
    return (config.PROMPTS / f"{config.ENRICH_PROMPT_VERSION}.txt").read_text(encoding="utf-8")


def build_user(items):
    parts = [f"Label these {len(items)} reviews. Return exactly {len(items)} lines.\n"]
    for i, (_, _, text, _) in enumerate(items, 1):
        safe = text.replace("</review", "</ review")
        parts.append(f'<review i="{i}">{safe}</review>')
    return "\n".join(parts)


def chunk(pending, size=config.MAX_REVIEWS_PER_REQUEST):
    batch, chars = [], 0
    for item in pending:
        if batch and (len(batch) >= size or chars + len(item[2]) > MAX_REQUEST_CHARS):
            yield batch
            batch, chars = [], 0
        batch.append(item)
        chars += len(item[2])
    if batch:
        yield batch


LINE = re.compile(r"^\s*(\d+)\s*\|(.*)$")


def parse(output, n):
    """Return ({i: fields}, {i: error}) for 1..n. Unparseable or missing items are errors."""
    got, errors = {}, {}
    for line in output.splitlines():
        m = LINE.match(line)
        if not m:
            continue
        i = int(m.group(1))
        parts = m.group(2).split("|", 6)
        if not 1 <= i <= n:
            continue
        if i in got:
            errors[i] = "duplicate item number"
            continue
        if len(parts) < 6:
            errors[i] = f"expected 8 fields, got {len(parts) + 1}"
            continue
        if len(parts) == 6:
            parts.append("")
        topic, intent, sev, sent, nr, ents, quote = (p.strip() for p in parts)
        quote = parts[6].strip("\r\n")
        try:
            sev = int(sev)
            sent = float(sent)
        except ValueError:
            errors[i] = "non-numeric severity/sentiment"
            continue
        if topic not in TOPICS or intent not in INTENTS or not 1 <= sev <= 5 or not -1 <= sent <= 1 or nr not in ("0", "1"):
            errors[i] = f"invalid value(s): {topic}|{intent}|{sev}|{sent}|{nr}"
            continue
        got[i] = {"topic": topic, "intent": intent, "severity": sev, "sentiment": sent, "needs_review": nr == "1",
                  "entities_raw": ents, "quote_raw": quote}
    for i in range(1, n + 1):
        if i not in got and i not in errors:
            errors[i] = "missing from output"
    for i in got:
        errors.pop(i, None)
    return got, errors


def finalize(text, fields):
    """Deterministic post-processing: exact evidence quote and supported entities only."""
    out = {k: fields[k] for k in ("topic", "intent", "severity", "sentiment", "needs_review")}
    s = out["sentiment"]
    out["sentiment"] = min(SENTIMENTS, key=lambda x: abs(x - s))  # snap to the declared 5-level scale
    q = fields["quote_raw"].strip().strip('"')
    if len(text) <= config.SHORT_REVIEW_CHARS:
        out["evidence_quote"], out["quote_source"] = text, "full_text"
    elif q and q in text:
        out["evidence_quote"], out["quote_source"] = q, "model_exact"
    else:
        span = _loose_find(q, text) if q else None
        if span:
            out["evidence_quote"], out["quote_source"] = span, "model_whitespace_repaired"
        else:
            cut = text[:config.SHORT_REVIEW_CHARS]
            cut = cut[:cut.rfind(" ")] if " " in cut else cut
            out["evidence_quote"], out["quote_source"] = cut, "fallback_prefix"
            out["needs_review"] = True
    if not out["evidence_quote"].strip():
        out["evidence_quote"], out["quote_source"] = text, "full_text"
    ents, dropped = [], []
    low = text.lower()
    for e in fields["entities_raw"].split(";"):
        e = e.strip()
        if not e or e == "-":
            continue
        (ents if e.lower() in low and e not in ents else dropped).append(e)
    out["entities"], out["dropped_entities"] = ents, dropped
    return out


def _loose_find(q, text):
    """Find q in text ignoring whitespace differences; return the exact source span."""
    words = q.split()
    if not words:
        return None
    pattern = r"\s+".join(re.escape(w) for w in words)
    m = re.search(pattern, text)
    return m.group(0) if m else None


# ---------------------------------------------------------------- persistence
def save_results(st, items, parsed, errors, request_id, phase):
    """Atomically store one request's outcome. Returns (completed, retry, quarantined)."""
    done = retry = quar = 0
    with st.tx() as db:
        for i, (tsha, rep, text, attempts) in enumerate(items, 1):
            if i in parsed:
                res = finalize(text, parsed[i])
                db.execute("UPDATE texts SET status='completed', attempts=?, result=?, request_id=?, phase=?, label_config=?, reason=NULL "
                           "WHERE text_sha=? AND status='pending'",
                           (attempts + 1, json.dumps(res, ensure_ascii=False), request_id, phase, config.LABEL_CONFIG, tsha))
                done += 1
            else:
                att = attempts + 1
                if att > config.MAX_INVALID_RETRIES:
                    db.execute("UPDATE texts SET status='quarantined', attempts=?, reason=? WHERE text_sha=? AND status='pending'",
                               (att, f"invalid_model_output: {errors.get(i, 'unknown')}", tsha))
                    quar += 1
                else:
                    db.execute("UPDATE texts SET attempts=?, reason=? WHERE text_sha=? AND status='pending'", (att, errors.get(i, "unknown"), tsha))
                    retry += 1
    return done, retry, quar


def mark_failed_request(st, items, reason, max_attempts=3):
    """Request-level failure: count the attempt; after max_attempts the text is quarantined with the reason."""
    with st.tx() as db:
        for tsha, *_ in items:
            db.execute("UPDATE texts SET attempts=attempts+1, reason=? WHERE text_sha=? AND status='pending'", (reason, tsha))
            db.execute("UPDATE texts SET status='quarantined' WHERE text_sha=? AND status='pending' AND attempts>=?", (tsha, max_attempts))


# ---------------------------------------------------------------- sync mode
def run_sync(st, run_dir, run_id, phase="initial", workers=1, max_requests=None, budget=None):
    calls = run_dir / "calls.jsonl"
    system = system_prompt()
    prev = signal.signal(signal.SIGINT, lambda *_: (_stop.set(), print("\n[enrich] stop requested: finishing in-flight requests, saving state")))
    sent = 0
    totals = {"completed": 0, "retry": 0, "quarantined": 0, "failed_requests": 0}
    t0 = time.time()
    try:
        while not _stop.is_set():
            wave = max(1, min(workers, config.MAX_WORKERS)) * 2
            pending = st.pending(limit=wave * config.MAX_REVIEWS_PER_REQUEST)
            if not pending:
                break
            # Dispatch in small waves so a stop request (Ctrl-C, budget) takes effect within one wave.
            jobs = list(chunk(pending))[:wave]
            if max_requests is not None:
                jobs = jobs[: max(0, max_requests - sent)]
                if not jobs:
                    break
            with ThreadPoolExecutor(max_workers=max(1, min(workers, config.MAX_WORKERS))) as pool:
                futs = {}
                for items in jobs:
                    if _stop.is_set():
                        break
                    futs[pool.submit(_one_request, items, system, calls, run_id, phase, budget)] = items
                    sent += 1
                for fut in as_completed(futs):
                    items = futs[fut]
                    try:
                        text, usage, rid, stop_reason = fut.result()
                        parsed, errors = parse(text, len(items))
                        d, r, q = save_results(st, items, parsed, errors, rid, phase)
                        totals["completed"] += d; totals["retry"] += r; totals["quarantined"] += q
                        if errors:
                            append_jsonl(run_dir / "validation_errors.jsonl", {"request_id": rid, "stop_reason": stop_reason,
                                         "errors": {items[i - 1][1]: e for i, e in errors.items()}})
                    except llm.BudgetExceeded as e:
                        print(f"[enrich] BUDGET STOP: {e}")
                        _stop.set()
                    except Exception as e:  # request-level failure after bounded retries
                        totals["failed_requests"] += 1
                        mark_failed_request(st, items, f"request_failed: {type(e).__name__}")
                        print(f"[enrich] request failed after retries: {type(e).__name__}: {str(e)[:200]}")
                        if totals["failed_requests"] >= 5:
                            print("[enrich] too many failed requests; stopping to protect budget")
                            _stop.set()
                    c = st.counts()
                    print(f"[enrich] {phase} completed={c.get('completed', 0)} pending={c.get('pending', 0)} "
                          f"quarantined={c.get('quarantined', 0)} requests_sent={sent} spent=${llm.spent_usd():.4f} "
                          f"elapsed={time.time() - t0:.0f}s", flush=True)
    finally:
        signal.signal(signal.SIGINT, prev)
    totals["wall_seconds"] = round(time.time() - t0, 2)
    return totals


def _one_request(items, system, calls, run_id, phase, budget):
    ids = [rep for _, rep, _, _ in items]
    return llm.call(role="enrich", model=config.ENRICH_MODEL, system=system, user=build_user(items),
                    max_tokens=config.ENRICH_MAX_OUTPUT_TOKENS, settings=config.ENRICH_SETTINGS, review_ids=ids,
                    calls_path=calls, run_id=run_id, phase=phase, label_config=config.LABEL_CONFIG, budget=budget)


# ---------------------------------------------------------------- batch mode
def run_batch(st, run_dir, run_id, phase="resume", budget=None, poll_seconds=60, max_requests=None):
    """Submit pending texts through the Message Batches API; safe to re-run (polls saved batch IDs)."""
    import anthropic
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
    from anthropic.types.messages.batch_create_params import Request

    client = llm.client()
    calls = run_dir / "calls.jsonl"
    system = system_prompt()
    try:
        st.db.execute("ALTER TABLE batches ADD COLUMN worst REAL DEFAULT 0")
    except Exception:
        pass
    submitted_requests = [0]

    def open_reserved():
        return st.db.execute("SELECT COALESCE(SUM(worst),0) FROM batches WHERE ingested=0").fetchone()[0]

    def submit_what_fits():
        """Submit chunks while spent + in-flight worst case + next chunk <= cap (in-flight batches stay reserved)."""
        cap = config.GLOBAL_BUDGET_USD if budget is None else budget
        open_rows = st.db.execute("SELECT requests FROM batches WHERE ingested=0").fetchall()
        in_flight = {t for (reqs,) in open_rows for v in json.loads(reqs).values() for t in v}
        pending = [p for p in st.pending() if p[0] not in in_flight]
        jobs = list(chunk(pending))
        if max_requests:
            jobs = jobs[: max(0, max_requests - submitted_requests[0])]
        for start in range(0, len(jobs), config.BATCH_CHUNK_REQUESTS):
            group = jobs[start:start + config.BATCH_CHUNK_REQUESTS]
            worst = sum(llm.worst_case_cost(config.ENRICH_MODEL, len(system) + sum(len(x[2]) for x in g),
                                            config.ENRICH_MAX_OUTPUT_TOKENS, batch=True) for g in group)
            spent, reserved = llm.spent_usd(), open_reserved()
            if spent + reserved + worst > cap:
                print(f"[batch] holding {len(jobs) - start} requests: spent ${spent:.2f} + in-flight worst ${reserved:.2f} "
                      f"+ next ${worst:.2f} > cap ${cap:.2f}", flush=True)
                return
            seq = st.meta_get("batch_seq", 0)
            reqs, mapping = [], {}
            for k, items in enumerate(group):
                cid = f"r{seq + k:06d}"
                mapping[cid] = [x[0] for x in items]
                reqs.append(Request(custom_id=cid, params=MessageCreateParamsNonStreaming(
                    model=config.ENRICH_MODEL, max_tokens=config.ENRICH_MAX_OUTPUT_TOKENS,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": build_user(items)}], **config.ENRICH_SETTINGS)))
            b = client.messages.batches.create(requests=reqs)
            with st.tx() as db:  # persist before polling so an interruption never resubmits (no double charge)
                db.execute("INSERT INTO batches (batch_id, phase, requests, status, submitted_at, ingested, worst) VALUES (?,?,?,?,?,0,?)",
                           (b.id, phase, json.dumps(mapping), b.processing_status, time.time(), worst))
                db.execute("INSERT OR REPLACE INTO meta VALUES ('batch_seq', ?)", (json.dumps(seq + len(group)),))
            submitted_requests[0] += len(group)
            print(f"[batch] submitted {b.id}: {len(reqs)} requests, {sum(len(v) for v in mapping.values())} texts, "
                  f"worst-case ${worst:.2f} reserved", flush=True)

    totals = {"completed": 0, "retry": 0, "quarantined": 0, "errored_requests": 0}
    t0 = time.time()
    while True:
        submit_what_fits()
        rows = st.db.execute("SELECT batch_id, phase, requests FROM batches WHERE ingested=0").fetchall()
        if not rows:
            break
        for batch_id, bphase, reqs in rows:
            b = client.messages.batches.retrieve(batch_id)
            rc = b.request_counts
            print(f"[batch] {batch_id} {b.processing_status} processing={rc.processing} succeeded={rc.succeeded} "
                  f"errored={rc.errored} elapsed={time.time() - t0:.0f}s", flush=True)
            if b.processing_status != "ended":
                continue
            mapping = json.loads(reqs)
            by_sha = {r[0]: r for r in st.db.execute(
                "SELECT text_sha, rep_id, text, attempts FROM texts WHERE text_sha IN (%s)" %
                ",".join("?" * sum(len(v) for v in mapping.values())), [t for v in mapping.values() for t in v])}
            for res in client.messages.batches.results(batch_id):
                items = [by_sha[t] for t in mapping[res.custom_id] if t in by_sha]
                ids = [x[1] for x in items]
                base = {"role": "enrich", "stage": "enrich", "model": config.ENRICH_MODEL, "review_ids": ids, "phase": bphase,
                        "label_config": config.LABEL_CONFIG, "run_id": run_id, "mode": "batch", "batch_id": batch_id,
                        "custom_id": res.custom_id, "attempt": 1, "max_tokens": config.ENRICH_MAX_OUTPUT_TOKENS,
                        "settings": config.ENRICH_SETTINGS}
                rid = f"{batch_id}:{res.custom_id}"
                if res.result.type == "succeeded":
                    msg = res.result.message
                    usage = llm.usage_dict(msg.usage)
                    c = llm.cost_usd(config.ENRICH_MODEL, usage, batch=True)
                    append_jsonl(calls, {**base, "request_id": rid, "provider_message_id": msg.id, "outcome": "succeeded",
                                         "stop_reason": msg.stop_reason, **usage, "cost_usd": c})
                    llm.record_spend({"run_id": run_id, "request_id": rid, "role": "enrich", "model": config.ENRICH_MODEL,
                                      "cost_usd": c, "at": time.time(), "mode": "batch"})
                    text = llm.text_of(msg)
                    parsed, errors = parse(text, len(items))
                    d, r, q = save_results(st, items, parsed, errors, rid, bphase)
                    totals["completed"] += d; totals["retry"] += r; totals["quarantined"] += q
                    if errors:
                        append_jsonl(run_dir / "validation_errors.jsonl", {"request_id": rid, "stop_reason": msg.stop_reason,
                                     "errors": {items[i - 1][1]: e for i, e in errors.items()}})
                else:
                    totals["errored_requests"] += 1
                    err = getattr(res.result, "error", None)
                    append_jsonl(calls, {**base, "request_id": rid, "outcome": "failed", "error": f"{res.result.type}: {err}",
                                         "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0})
                    mark_failed_request(st, items, f"batch_{res.result.type}")
            with st.tx() as db:
                db.execute("UPDATE batches SET ingested=1, status='ended' WHERE batch_id=?", (batch_id,))
            c = st.counts()
            print(f"[batch] ingested {batch_id}: completed={c.get('completed', 0)} pending={c.get('pending', 0)} "
                  f"quarantined={c.get('quarantined', 0)} spent=${llm.spent_usd():.4f}", flush=True)
        if st.db.execute("SELECT COUNT(*) FROM batches WHERE ingested=0").fetchone()[0]:
            time.sleep(poll_seconds)
    totals["wall_seconds"] = round(time.time() - t0, 2)
    return totals
