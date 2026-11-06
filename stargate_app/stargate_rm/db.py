"""Databricks SQL warehouse access for the app.

Uses databricks-sql-connector with the app's own OAuth credentials (Databricks Apps sets DATABRICKS_HOST,
DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET for the service principal).  Every query is read-only except the
small app-state writes in state.py.  Results are cached in Streamlit for the lifetime of a daily refresh.
"""
from __future__ import annotations

import decimal
import os
from typing import Any

import pandas as pd
import streamlit as st

from . import config


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


@st.cache_data(ttl=3600, show_spinner=False)
def query(sql_text: str) -> pd.DataFrame:
    """Run one SELECT and return a DataFrame (cached one hour)."""
    with _connection() as conn, conn.cursor() as cur:
        cur.execute(sql_text)
        cols = [c[0] for c in cur.description]
        return pd.DataFrame(cur.fetchall_arrow().to_pandas() if hasattr(cur, "fetchall_arrow") else cur.fetchall(), columns=cols)


def execute(sql_text: str) -> None:
    """Run a statement with no result set (app-state tables only)."""
    with _connection() as conn, conn.cursor() as cur:
        cur.execute(sql_text)


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
