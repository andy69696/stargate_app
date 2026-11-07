"""Databricks SQL warehouse access for the app.

Uses databricks-sql-connector with the app's own OAuth credentials (Databricks Apps sets DATABRICKS_HOST,
DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET for the service principal).  Every query is read-only except the
small app-state writes in state.py.  SELECT results are cached in-process for an hour (works the same under Streamlit and
under the FastAPI server, which do not share Streamlit's cache); clear_cache() empties it for a rebuild.
"""
from __future__ import annotations

import contextvars
import decimal
import os
import threading
import time
from typing import Any

import pandas as pd

from . import config

CACHE_TTL = 3600
_cache: dict[str, tuple[float, pd.DataFrame]] = {}
_cache_lock = threading.Lock()

# Diagnostics hooks: the pipeline labels each pull; observers see every query (label, sql, frame or None, seconds, error).
label: contextvars.ContextVar[str] = contextvars.ContextVar("stargate_query_label", default="")
observers: list = []


def _notify(sql_text: str, df, seconds: float, error: str | None) -> None:
    for fn in list(observers):
        try:
            fn(label.get(), sql_text, df, seconds, error)
        except Exception:  # noqa: BLE001 - an observer must never break a query
            pass


def clear_cache() -> None:
    with _cache_lock:
        _cache.clear()


def _connection():
    from databricks import sql
    from databricks.sdk.core import Config, oauth_service_principal

    host = os.environ.get("DATABRICKS_HOST", "")
    if not host:
        raise RuntimeError("DATABRICKS_HOST is not set: run inside Databricks Apps or export it with a token.")
    if not config.WAREHOUSE_ID:
        raise RuntimeError("DATABRICKS_WAREHOUSE_ID is not set: add the sql-warehouse resource in app.yaml.")
    cfg = Config(host=f"https://{host}" if not host.startswith("http") else host)

    def cred():
        return oauth_service_principal(cfg)

    kw: dict[str, Any] = dict(server_hostname=cfg.host.replace("https://", ""), http_path=f"/sql/1.0/warehouses/{config.WAREHOUSE_ID}")
    if os.environ.get("DATABRICKS_TOKEN"):
        kw["access_token"] = os.environ["DATABRICKS_TOKEN"]      # local development with a PAT
    else:
        kw["credentials_provider"] = cred                        # Databricks Apps service principal
    return sql.connect(**kw)


def query(sql_text: str, cached: bool = True, params: dict | None = None) -> pd.DataFrame:
    """Run one SELECT and return a DataFrame (cached one hour unless cached=False; never cached with params).
    params are bound as named parameters (:name in the SQL), never pasted into the text."""
    now = time.time()
    cached = cached and not params
    if cached:
        with _cache_lock:
            hit = _cache.get(sql_text)
        if hit and now - hit[0] < CACHE_TTL:
            return hit[1]
    t0 = time.time()
    try:
        with _connection() as conn, conn.cursor() as cur:
            cur.execute(sql_text, params) if params else cur.execute(sql_text)
            cols = [c[0] for c in cur.description]
            df = pd.DataFrame(cur.fetchall_arrow().to_pandas() if hasattr(cur, "fetchall_arrow") else cur.fetchall(), columns=cols)
    except Exception as e:
        _notify(sql_text, None, time.time() - t0, str(e).splitlines()[0][:400] if str(e) else type(e).__name__)
        raise
    _notify(sql_text, df, time.time() - t0, None)
    if cached:
        with _cache_lock:
            _cache[sql_text] = (now, df)
    return df


def columns(table: str) -> set[str]:
    """Lower-cased column names of a table (empty set if it cannot be read).  Lets queries treat custom fields as optional."""
    with _cache_lock:
        hit = _cache.get("__columns__" + table)
    if hit and time.time() - hit[0] < CACHE_TTL:
        return set(hit[1].columns)
    with _connection() as conn, conn.cursor() as cur:
        cur.execute(f"SELECT * FROM {table} LIMIT 0")
        cols = [c[0].lower() for c in cur.description]
    with _cache_lock:
        _cache["__columns__" + table] = (time.time(), pd.DataFrame(columns=cols))
    return set(cols)


def table_exists(table: str) -> bool:
    """True if the table can be read.  Optional lookup tables (e.g. product group) are joined only when present."""
    try:
        return bool(columns(table))
    except Exception:  # noqa: BLE001 - missing table or no grant: treat as absent
        return False


def execute(sql_text: str, params: dict | None = None) -> None:
    """Run a statement with no result set (app-state tables only); params bind as :name."""
    with _connection() as conn, conn.cursor() as cur:
        cur.execute(sql_text, params) if params else cur.execute(sql_text)


def records(sql_text: str) -> list[dict]:
    """Rows as the nested dicts the legacy builders expect: a column aliased `KimbleOne__Resource__r.Name`
    becomes {'KimbleOne__Resource__r': {'Name': ...}} exactly as a Salesforce SOQL result would."""
    df = query(sql_text)
    out: list[dict] = []
    for row in df.to_dict("records"):
        rec: dict = {}
        for k, v in row.items():
            if pd.isna(v) if not isinstance(v, (list, dict)) else False:
                v = None
            elif isinstance(v, decimal.Decimal):
                v = float(v)
            elif hasattr(v, "isoformat"):
                v = v.isoformat()
            parts = k.split(".")
            d = rec
            for p in parts[:-1]:
                d = d.setdefault(p, {})
            d[parts[-1]] = v
        out.append(rec)
    return out
