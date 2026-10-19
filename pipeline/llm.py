"""Model gateway: the only place that talks to the API.

Owns: model allowlist, spend ledger with pre-dispatch reservations, bounded
retries with backoff + jitter, and an append-only log of every attempt.
"""
import random
import threading
import time
import uuid
from pathlib import Path

from dotenv import load_dotenv

from . import config
from .io_utils import append_jsonl, read_jsonl

load_dotenv(config.ROOT / ".env")

LEDGER = config.ROOT / "ledger" / "spend.jsonl"
_lock = threading.Lock()
_reserved = 0.0


class BudgetExceeded(RuntimeError):
    pass


def cost_usd(model, usage, batch=False):
    r = config.RATES[model]
    total = (usage.get("input_tokens", 0) * r["input"]
             + usage.get("cache_creation_input_tokens", 0) * r["cache_write"]
             + usage.get("cache_read_input_tokens", 0) * r["cache_read"]
             + usage.get("output_tokens", 0) * r["output"]) / 1e6
    return total * (config.BATCH_DISCOUNT if batch else 1.0)


def spent_usd():
    return sum(e.get("cost_usd", 0.0) for e in read_jsonl(LEDGER))


def reserve(amount, budget=None):
    """Reserve worst-case cost before dispatch; refuse when spent+reserved+next > cap."""
    global _reserved
    budget = config.GLOBAL_BUDGET_USD if budget is None else budget
    with _lock:
        spent = spent_usd()
        if spent + _reserved + amount > budget:
            raise BudgetExceeded(f"spent ${spent:.4f} + reserved ${_reserved:.4f} + next ${amount:.4f} > cap ${budget:.2f}")
        _reserved += amount


def release(amount):
    global _reserved
    with _lock:
        _reserved = max(0.0, _reserved - amount)


def record_spend(entry):
    with _lock:
        append_jsonl(LEDGER, entry)


def worst_case_cost(model, input_chars, max_tokens, batch=False):
    # ~3 chars/token is a conservative upper bound for mixed-language text.
    return cost_usd(model, {"input_tokens": input_chars // 3 + 1, "output_tokens": max_tokens}, batch)


_client = None


def client():
    global _client
    if _client is None:
        import anthropic
        _client = anthropic.Anthropic(max_retries=0, timeout=120.0)
    return _client


def usage_dict(u):
    return {
        "input_tokens": u.input_tokens or 0,
        "output_tokens": u.output_tokens or 0,
        "cache_creation_input_tokens": getattr(u, "cache_creation_input_tokens", 0) or 0,
        "cache_read_input_tokens": getattr(u, "cache_read_input_tokens", 0) or 0,
    }


def text_of(msg):
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text")


def call(*, role, model, system, user, max_tokens, settings, review_ids, calls_path, run_id,
         phase="initial", label_config=None, stage=None, budget=None, simulate_failures=0, extra=None):
    """One logical request with bounded transient retries. Returns (text, usage, request_id).

    Every attempt (success or failure) is appended to calls_path and the global ledger.
    """
    import anthropic
    if model not in config.MODEL_ALLOWLIST:
        raise ValueError(f"model {model} not in allowlist")
    worst = worst_case_cost(model, len(system) + len(user), max_tokens)
    reserve(worst, budget)
    try:
        for attempt in range(1, config.MAX_TRANSIENT_RETRIES + 2):
            t0 = time.time()
            base = {"role": role, "stage": stage or role, "model": model, "review_ids": review_ids, "phase": phase,
                    "label_config": label_config or f"{model}+{role}", "run_id": run_id, "attempt": attempt,
                    "mode": "sync", "max_tokens": max_tokens, "settings": settings, **(extra or {})}
            try:
                if attempt <= simulate_failures:
                    raise anthropic.APIConnectionError(request=None, message="simulated transient failure (system test)")
                msg = client().messages.create(
                    model=model, max_tokens=max_tokens,
                    system=[{"type": "text", "text": system, "cache_control": {"type": "ephemeral"}}],
                    messages=[{"role": "user", "content": user}], **settings)
                usage = usage_dict(msg.usage)
                c = cost_usd(model, usage)
                entry = {**base, "request_id": msg.id, "provider_request_id": getattr(msg, "_request_id", None),
                         "outcome": "succeeded", "stop_reason": msg.stop_reason, **usage, "cost_usd": c,
                         "latency_s": round(time.time() - t0, 3), "started_at": t0}
                append_jsonl(calls_path, entry)
                record_spend({"run_id": run_id, "request_id": msg.id, "role": role, "model": model, "cost_usd": c, "at": time.time()})
                return text_of(msg), usage, msg.id, msg.stop_reason
            except (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError,
                    anthropic.APITimeoutError) as e:
                append_jsonl(calls_path, {**base, "request_id": f"failed-{uuid.uuid4().hex[:12]}", "outcome": "failed",
                                          "error": f"{type(e).__name__}: {str(e)[:200]}", "input_tokens": 0, "output_tokens": 0,
                                          "cost_usd": 0.0, "latency_s": round(time.time() - t0, 3), "started_at": t0})
                if attempt > config.MAX_TRANSIENT_RETRIES:
                    raise
                delay = min(60.0, 2.0 ** attempt) + random.uniform(0, 1.0)
                if isinstance(e, anthropic.RateLimitError):
                    try:
                        delay = max(delay, float(e.response.headers.get("retry-after", 0)))
                    except Exception:
                        pass
                time.sleep(delay)
            except anthropic.APIStatusError as e:  # non-retryable 4xx
                append_jsonl(calls_path, {**base, "request_id": f"failed-{uuid.uuid4().hex[:12]}", "outcome": "failed",
                                          "error": f"{type(e).__name__} {e.status_code}: {str(e)[:300]}",
                                          "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0,
                                          "latency_s": round(time.time() - t0, 3), "started_at": t0})
                raise
    finally:
        release(worst)
