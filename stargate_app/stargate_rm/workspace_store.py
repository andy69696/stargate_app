"""Shared state for the Stargate Workspace pages (approvals, proposals, exception ledger, settings, pricing deals …).

On claude.ai the workspace kept these in the artifact's shared database; on Databricks they live in one Delta table:

    <APP_STATE_SCHEMA>.workspace_db (collection STRING, id STRING, data STRING, updated TIMESTAMP, updated_by STRING)

one row per document, data = the document as JSON.  The browser shim (web_runtime/claude.js) reads everything with one GET
and writes one document at a time.  Reads are served from memory and refreshed from the table at most every REFRESH_S
seconds, so polling browsers do not each hit the warehouse.

If the table cannot be created or written (schema missing, no grant, or sample mode) the store runs in memory: everything
works, but it is per-app-instance and lost on restart.  /api/status reports which mode is live and why.
TODO(databricks): grant the app's service principal USE SCHEMA, CREATE TABLE, SELECT and MODIFY on <APP_STATE_SCHEMA>.
"""
from __future__ import annotations

import json
import re
import threading
import time

from . import config, db

TABLE = "workspace_db"
REFRESH_S = 10
_NAME = re.compile(r"^[^/\x00-\x1f]{1,300}$")   # any printable id without a slash (values are bound, never pasted into SQL)


class Store:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._docs: dict[str, dict[str, object]] = {}
        self._loaded_at = 0.0
        self.mode = "memory"
        self.reason = "not started"
        self._ready = False

    @property
    def table(self) -> str:
        return f"{config.APP_STATE_SCHEMA}.{TABLE}"

    def _init(self) -> None:
        if self._ready:
            return
        self._ready = True
        if config.SAMPLE_MODE:
            self.mode, self.reason = "memory", "sample mode"
            return
        try:
            db.execute(f"CREATE TABLE IF NOT EXISTS {self.table} (collection STRING, id STRING, data STRING, updated TIMESTAMP, updated_by STRING)")
            self.mode, self.reason = "delta", self.table
        except Exception as e:  # noqa: BLE001
            self.mode, self.reason = "memory", f"{self.table} not available: {str(e).splitlines()[0][:200]}"

    def _reload(self, force: bool = False) -> None:
        if self.mode != "delta" or (not force and time.time() - self._loaded_at < REFRESH_S):
            return
        try:
            df = db.query(f"SELECT collection, id, data FROM {self.table}", cached=False)
        except Exception as e:  # noqa: BLE001 - keep serving the last good copy
            self.reason = f"{self.table} read failed: {str(e).splitlines()[0][:200]}"
            return
        docs: dict[str, dict[str, object]] = {}
        for c, i, d in df.itertuples(index=False):
            try:
                docs.setdefault(c, {})[i] = json.loads(d)
            except (TypeError, ValueError):
                continue
        self._docs = docs
        self._loaded_at = time.time()

    @staticmethod
    def check(name: str) -> str:
        if not _NAME.match(name or ""):
            raise ValueError(f"bad collection or document id: {name!r}")
        return name

    def all(self) -> dict:
        with self._lock:
            self._init()
            self._reload()
            return self._docs

    def set(self, collection: str, doc_id: str, data: object, user: str | None) -> None:
        self.check(collection); self.check(doc_id)
        payload = json.dumps(data)
        with self._lock:
            self._init()
            if self.mode == "delta":
                db.execute(
                    f"""MERGE INTO {self.table} t
                        USING (SELECT :c AS collection, :i AS id, :d AS data, :u AS updated_by) s
                        ON t.collection = s.collection AND t.id = s.id
                        WHEN MATCHED THEN UPDATE SET data = s.data, updated = current_timestamp(), updated_by = s.updated_by
                        WHEN NOT MATCHED THEN INSERT (collection, id, data, updated, updated_by)
                             VALUES (s.collection, s.id, s.data, current_timestamp(), s.updated_by)""",
                    {"c": collection, "i": doc_id, "d": payload, "u": user or ""})
            self._docs.setdefault(collection, {})[doc_id] = json.loads(payload)

    def delete(self, collection: str, doc_id: str) -> None:
        self.check(collection); self.check(doc_id)
        with self._lock:
            self._init()
            if self.mode == "delta":
                db.execute(f"DELETE FROM {self.table} WHERE collection = :c AND id = :i", {"c": collection, "i": doc_id})
            self._docs.get(collection, {}).pop(doc_id, None)

    def status(self) -> dict:
        with self._lock:
            self._init()
            return {"mode": self.mode, "detail": self.reason}


store = Store()
