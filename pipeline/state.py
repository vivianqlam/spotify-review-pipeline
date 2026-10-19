"""SQLite run state: one row per source review, one row per distinct text.

Every write happens in a transaction from the orchestrator thread, so workers
can never overwrite or double-count each other's results.
"""
import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS reviews (
  review_id TEXT PRIMARY KEY, ord INTEGER, source_sha256 TEXT, text_sha TEXT, rep_id TEXT, empty INTEGER);
CREATE TABLE IF NOT EXISTS texts (
  text_sha TEXT PRIMARY KEY, rep_id TEXT, rep_ord INTEGER, text TEXT, status TEXT DEFAULT 'pending',
  attempts INTEGER DEFAULT 0, result TEXT, request_id TEXT, phase TEXT, label_config TEXT, reason TEXT);
CREATE INDEX IF NOT EXISTS texts_status ON texts(status, rep_ord);
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, phase TEXT, requests TEXT, status TEXT, submitted_at REAL, ingested INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""


class State:
    def __init__(self, run_dir):
        self.path = Path(run_dir) / "state.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, isolation_level=None, check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    def tx(self):
        return _Tx(self.db)

    def meta_get(self, key, default=None):
        row = self.db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
        return json.loads(row[0]) if row else default

    def meta_set(self, key, value):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, json.dumps(value)))

    def pending(self, limit=None):
        q = "SELECT text_sha, rep_id, text, attempts FROM texts WHERE status='pending' ORDER BY rep_ord"
        if limit:
            q += f" LIMIT {int(limit)}"
        return self.db.execute(q).fetchall()

    def counts(self):
        c = dict(self.db.execute("SELECT status, COUNT(*) FROM texts GROUP BY status").fetchall())
        c["source_rows"] = self.db.execute("SELECT COUNT(*) FROM reviews").fetchone()[0]
        c["empty_rows"] = self.db.execute("SELECT COUNT(*) FROM reviews WHERE empty=1").fetchone()[0]
        return c

    def completed_review_ids(self):
        return [r[0] for r in self.db.execute(
            "SELECT r.review_id FROM reviews r JOIN texts t ON r.text_sha=t.text_sha "
            "WHERE r.empty=0 AND t.status='completed' ORDER BY r.ord")]


class _Tx:
    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, *_):
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
