"""Central configuration: models, prompt versions, limits, and editable prices.

Everything that changes a model's answer is folded into a label_config string so
saved results are only reused under identical settings.
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PROMPTS = ROOT / "prompts"

# ---- models (allowlist) ------------------------------------------------------
ENRICH_MODEL = "claude-haiku-5-5"
VERIFY_MODEL = "claude-haiku-5-5"
GROUP_MODEL = "claude-haiku-5-5"
MEMO_MODEL = "claude-sonnet-5-5"
MODEL_ALLOWLIST = {ENRICH_MODEL, VERIFY_MODEL, GROUP_MODEL, MEMO_MODEL}

# Cheapest supported settings for bounded classification: thinking off, low effort.
ENRICH_SETTINGS = {"thinking": {"type": "disabled"}, "output_config": {"effort": "low"}}
VERIFY_SETTINGS = ENRICH_SETTINGS
GROUP_SETTINGS = ENRICH_SETTINGS
MEMO_SETTINGS = {"output_config": {"effort": "low"}}

ENRICH_PROMPT_VERSION = "enrich_v1"
VERIFY_PROMPT_VERSION = "verify_v1"
GROUP_PROMPT_VERSION = "group_v1"
MEMO_PROMPT_VERSION = "memo_v2"
SCHEMA_VERSION = "schema_v1"

LABEL_CONFIG = f"{ENRICH_MODEL}+{ENRICH_PROMPT_VERSION}+{SCHEMA_VERSION}+effort-low+thinking-off"

# ---- limits / controls -------------------------------------------------------
MAX_REVIEWS_PER_REQUEST = 50          # hard assignment limit
ENRICH_MAX_OUTPUT_TOKENS = 4000       # output-token cap per enrichment request
MAX_INVALID_RETRIES = 1               # retry invalid output once, then quarantine
MAX_TRANSIENT_RETRIES = 4             # bounded backoff for 429/5xx/connection errors
DEFAULT_WORKERS = 1
MAX_WORKERS = 4
FALLBACK_FRACTION = 0.0               # no stronger-model fallback for enrichment (declared)
GLOBAL_BUDGET_USD = float(os.environ.get("PIPELINE_BUDGET_USD", "10.00"))
SHORT_REVIEW_CHARS = 200              # at or below this, the whole text is the evidence quote
VERIFY_SAMPLE_SIZE = 1000             # declared random verification sample (distinct texts)
VERIFY_SEED = 20261019
BATCH_CHUNK_REQUESTS = 2500           # requests per Message Batch submission

# ---- prices (USD per 1M tokens) — editable; see cost/rates.csv for sources ----
# Claude Haiku 5.5 / Sonnet 5.5 standard first-party rates (prompts <= 100K tokens).
RATES = {
    "claude-haiku-5-5": {"input": 0.10, "output": 0.50, "cache_write": 0.125, "cache_read": 0.01},
    "claude-sonnet-5-5": {"input": 2.00, "output": 10.00, "cache_write": 2.50, "cache_read": 0.10},
}
BATCH_DISCOUNT = 0.5  # Message Batches API bills 50% of standard rates
