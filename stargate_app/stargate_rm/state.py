"""App state kept in Unity Catalog (the artifact kept these in its shared store).

Tables (all in config.APP_STATE_SCHEMA; created on first use if the service principal may CREATE TABLE):
  exception_ledger   one row per finding ever detected: id, rule, subject, detail, owner, severity, due, first_build, last_build, closed_build
  rm_actions         the Resource manager's choice per generic role on Demand › Allocation options (action, person, ignored)
  resourcing_history weekly placeholder snapshots (written by pipeline.py)

TODO(databricks): grant the app's service principal USE SCHEMA, CREATE TABLE, SELECT and MODIFY on <APP_STATE_SCHEMA>.
When the tables are missing or not writable the app falls back to session memory, so nothing breaks; it just forgets on restart.
"""
from __future__ import annotations

import json

import streamlit as st

from . import config, db

DDL = {
    "exception_ledger": "(id STRING, rule STRING, subject STRING, detail STRING, owner STRING, severity STRING, due DATE, first_build DATE, last_build DATE, closed_build DATE)",
    "rm_actions": "(generic_id STRING, action STRING, person_id STRING, person_name STRING, ignored BOOLEAN, updated TIMESTAMP)",
    "resourcing_history": "(asof TIMESTAMP, payload STRING)",
}


def _ensure(table: str) -> bool:
    key = f"_ensured_{table}"
    if st.session_state.get(key) is not None:
        return st.session_state[key]
    ok = False
    if not config.SAMPLE_MODE:
        try:
            db.execute(f"CREATE TABLE IF NOT EXISTS {config.APP_STATE_SCHEMA}.{table} {DDL[table]}")
            ok = True
        except Exception as e:  # noqa: BLE001
            st.session_state.setdefault("data_gaps", []).append(f"app state table {table}: {str(e).splitlines()[0][:160]}")
    st.session_state[key] = ok
    return ok


def _q(s) -> str:
    return "NULL" if s is None else "'" + str(s).replace("'", "''") + "'"


# ---- exception ledger ---------------------------------------------------------------------------------------------------
def ledger_load() -> dict[str, dict]:
    if _ensure("exception_ledger"):
        try:
            df = db.query(f"SELECT * FROM {config.APP_STATE_SCHEMA}.exception_ledger")
            return {r["id"]: {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in r.items()} for r in df.to_dict("records")}
        except Exception:  # noqa: BLE001
            pass
    return st.session_state.setdefault("ledger_mem", {})


def ledger_sync(current: list[dict], build: str) -> dict[str, dict]:
    """Merge today's findings into the ledger: new ones get first_build, live ones get last_build, missing ones get closed_build."""
    led = ledger_load()
    cur = {x["id"]: x for x in current}
    rows = []
    for x in current:
        old = led.get(x["id"])
        rows.append({**x, "first_build": (old or {}).get("first_build") or build, "last_build": build, "closed_build": None})
    for k, old in led.items():
        if k not in cur and not old.get("closed_build"):
            rows.append({**old, "closed_build": build})
        elif k not in cur:
            rows.append(old)
    led = {r["id"]: r for r in rows}
    if _ensure("exception_ledger") and rows:
        try:
            vals = ", ".join("(" + ", ".join(_q(r.get(c)) for c in ("id", "rule", "subject", "detail", "owner", "severity")) +
                             ", " + ", ".join(f"CAST({_q(r.get(c))} AS DATE)" for c in ("due", "first_build", "last_build", "closed_build")) + ")" for r in rows)
            db.execute(f"CREATE OR REPLACE TABLE {config.APP_STATE_SCHEMA}.exception_ledger {DDL['exception_ledger']}")
            db.execute(f"INSERT INTO {config.APP_STATE_SCHEMA}.exception_ledger VALUES {vals}")
        except Exception as e:  # noqa: BLE001
            st.session_state.setdefault("data_gaps", []).append(f"exception ledger write: {str(e).splitlines()[0][:160]}")
    st.session_state["ledger_mem"] = led
    return led


# ---- RM actions on generic roles -------------------------------------------------------------------------------------------
def actions_load() -> dict[str, dict]:
    if "rm_actions" in st.session_state:
        return st.session_state["rm_actions"]
    out: dict[str, dict] = {}
    if _ensure("rm_actions"):
        try:
            df = db.query(f"SELECT * FROM {config.APP_STATE_SCHEMA}.rm_actions QUALIFY ROW_NUMBER() OVER (PARTITION BY generic_id ORDER BY updated DESC) = 1")
            out = {r["generic_id"]: r for r in df.to_dict("records")}
        except Exception:  # noqa: BLE001
            pass
    st.session_state["rm_actions"] = out
    return out


def action_save(generic_id: str, action: str, person_id: str | None, person_name: str | None, ignored: bool) -> None:
    rec = dict(generic_id=generic_id, action=action, person_id=person_id, person_name=person_name, ignored=ignored)
    actions_load()[generic_id] = rec
    if _ensure("rm_actions"):
        try:
            db.execute(f"INSERT INTO {config.APP_STATE_SCHEMA}.rm_actions VALUES ({_q(generic_id)}, {_q(action)}, {_q(person_id)}, {_q(person_name)}, {'true' if ignored else 'false'}, current_timestamp())")
        except Exception as e:  # noqa: BLE001
            st.session_state.setdefault("data_gaps", []).append(f"rm_actions write: {str(e).splitlines()[0][:160]}")
