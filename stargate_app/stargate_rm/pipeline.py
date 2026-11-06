"""Daily data build, in-process.

Runs the signed-off builders (stargate_rm/legacy: the RM dashboard build, the Resourcing & margin refresh and the matcher)
on rows pulled from the lake instead of on JSON dumps from the Kantata connector.  The builders are untouched: the SQL in
queries.py reproduces each pull, db.records() nests the columns like a SOQL result, and the records are written to a temporary
"kimble" folder with the file names the builders expect.  Result: three dicts with exactly the shapes the pages consumed
(rm data.json, resourcing data.json, match.json) plus the extra lake objects used for enrichment.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import tempfile
from pathlib import Path

import streamlit as st

from . import config, db, queries

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent / "sample"


def _dump(folder: str, name: str, recs: list[dict]) -> None:
    with open(os.path.join(folder, name), "w") as f:
        json.dump({"records": recs}, f)


def _run_main(module, argv: list[str]) -> None:
    old = sys.argv
    sys.argv = [module.__name__] + argv
    try:
        module.main()
    finally:
        sys.argv = old


def _month_add(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) + k
    while m > 12:
        m -= 12; y += 1
    return f"{y:04d}-{m:02d}"


def _last_day(ym: str) -> str:
    nxt = _month_add(ym, 1)
    return (dt.date.fromisoformat(nxt + "-01") - dt.timedelta(days=1)).isoformat()


def _safe(fn, *args, what: str = "") -> list[dict]:
    """Run a lake query; on failure (table missing, column renamed) return no rows and remember the gap for the banner."""
    try:
        return db.records(fn(*args))
    except Exception as e:  # noqa: BLE001 - we want the page to render with a visible gap, not crash
        st.session_state.setdefault("data_gaps", []).append(f"{what or fn.__name__}: {str(e).splitlines()[0][:200]}")
        return []


@st.cache_data(ttl=3600, show_spinner="Building the Resource manager views from the lake…")
def build(today: str | None = None) -> dict:
    """Return {'rm': ..., 'res': ..., 'match': ..., 'extras': ..., 'today': ..., 'gaps': [...]}."""
    if config.SAMPLE_MODE:
        return _sample()
    from .legacy import rm_build, resourcing_refresh, match as matcher

    st.session_state["data_gaps"] = []
    today = today or dt.date.today().isoformat()
    t = dt.date.fromisoformat(today)
    this_monday = t - dt.timedelta(days=t.weekday()); prev_monday = this_monday - dt.timedelta(days=7)
    window_end = prev_monday + dt.timedelta(days=13 * 7 - 1)
    this_month = today[:7]; month_start = this_month + "-01"; month_end = _last_day(_month_add(this_month, config.MONTHS))
    match_from = month_start; match_to = _last_day(_month_add(this_month, 5))
    fy_floor = "2025-12-01"   # history floor used by the Resourcing & margin page
    fy_end = _last_day(_month_add(this_month, 13))

    with tempfile.TemporaryDirectory() as tmp:
        kimble = os.path.join(tmp, "kimble"); match_dir = os.path.join(tmp, "match"); out = os.path.join(tmp, "out")
        for d in (kimble, match_dir, out):
            os.makedirs(d)
        # ---- RM dashboard inputs (runbook 1a-1h)
        res_recs = _safe(queries.resources, prev_monday.isoformat(), what="resources")
        _dump(kimble, "resources.json", res_recs)
        _dump(kimble, "wk_billable_a.json", _safe(queries.wk_billable, prev_monday.isoformat(), window_end.isoformat(), what="weekly demand"))
        _dump(kimble, "wk_absence.json", _safe(queries.wk_absence, prev_monday.isoformat(), window_end.isoformat(), what="weekly absence"))
        _dump(kimble, "wk_eng_a.json", _safe(queries.wk_eng, prev_monday.isoformat(), window_end.isoformat(), what="weekly demand by engagement"))
        _dump(kimble, "mo_margin_a.json", _safe(queries.mo_margin, month_start, month_end, what="monthly margin"))
        _dump(kimble, "mo_ledger_rev.json", _safe(queries.mo_ledger_rev, month_start, month_end, what="ledger revenue (PerformanceAnalysis)"))
        _dump(kimble, "mo_ledger_fixed.json", _safe(queries.mo_ledger_fixed, month_start, month_end, what="ledger fixed revenue"))
        _dump(kimble, "assignments.json", _safe(queries.assignments, prev_monday.isoformat(), what="assignments"))
        _run_main(rm_build, ["--kimble-dir", kimble, "--out", os.path.join(out, "rm.json"), "--today", today, "--weeks", str(config.WEEKS), "--months", str(config.MONTHS)])
        # ---- Resourcing & margin inputs
        _dump(kimble, "generic.json", _safe(queries.generic, fy_floor, what="generic placeholders"))
        _dump(kimble, "named.json", _safe(queries.named, fy_floor, what="named assignments"))
        _dump(kimble, "months_fy26.json", _safe(queries.months, fy_floor, fy_end, what="monthly billable series"))
        _dump(kimble, "ms_engagements.json", _safe(queries.ms_engagements, today, what="managed-service engagements"))
        _dump(kimble, "not_detailed.json", _safe(queries.not_detailed, today, what="not detailed engagements"))
        hs_path = os.path.join(tmp, "hubspot.json")
        with open(hs_path, "w") as f:
            json.dump({"deals": _safe(queries.hubspot_deals, config.HUBSPOT_DEALS_TABLE, what="HubSpot deals")}, f)
        hist_path = os.path.join(tmp, "history.json")
        with open(hist_path, "w") as f:
            json.dump(_history_load(), f)
        _run_main(resourcing_refresh, ["--kimble-dir", kimble, "--history", hist_path, "--out-dir", out, "--label", "app", "--hubspot-data", hs_path])
        # ---- matcher inputs
        _dump(match_dir, "resources.json", res_recs)
        _dump(match_dir, "res_roles.json", _safe(queries.res_roles, today, what="resource roles"))
        _dump(match_dir, "booked.json", _safe(queries.booked, match_from, match_to, what="booked days"))
        _dump(match_dir, "booked_type_a.json", _safe(queries.booked_type, match_from, match_to, what="booked days by product group"))
        _dump(match_dir, "eng_pg.json", _safe(queries.eng_pg, today, what="engagement product groups"))
        _dump(match_dir, "ms_engagements.json", _safe(queries.ms_engagements, today, what="managed-service engagements"))
        _dump(match_dir, "cap_1.json", _safe(queries.capabilities, today, what="skills ratings"))
        _run_main(matcher, ["--match-dir", match_dir, "--data", os.path.join(out, "data.json"), "--out", os.path.join(out, "match.json")])
        with open(os.path.join(out, "rm.json")) as f:
            rm = json.load(f)
        with open(os.path.join(out, "data.json")) as f:
            res = json.load(f)
        with open(os.path.join(out, "match.json")) as f:
            mt = json.load(f)
        with open(os.path.join(out, "history.json")) as f:
            _history_save(json.load(f))
    extras = {
        "actors": _safe(queries.dg_actors, what="delivery group actors"),
        "status": _safe(queries.status_summaries, what="status summaries"),
        "po": _safe(queries.purchase_orders, what="purchase orders"),
        "invoices": _safe(queries.invoices, what="invoices"),
        "time": _safe(queries.time_entries, (t - dt.timedelta(days=120)).isoformat(), what="time entries"),
        "required": _safe(queries.required_capabilities, what="required capabilities"),
    }
    return {"rm": rm, "res": res, "match": mt, "extras": extras, "today": today, "gaps": list(st.session_state.get("data_gaps", []))}


def _sample() -> dict:
    with open(SAMPLE / "rm_data.json") as f:
        rm = json.load(f)
    with open(SAMPLE / "resourcing_data.json") as f:
        res = json.load(f)
    with open(SAMPLE / "match.json") as f:
        mt = json.load(f)
    return {"rm": rm, "res": res, "match": mt, "extras": {"actors": [], "status": [], "po": [], "invoices": [], "time": [], "required": []},
            "today": res.get("today"), "gaps": ["Sample mode: bundled JSON, not the lake"]}


# ---- resourcing history (weekly placeholder snapshots) lives in the app-state schema -----------------------------------------
def _history_load() -> dict:
    """TODO(databricks): create <APP_STATE_SCHEMA>.resourcing_history (asof TIMESTAMP, payload STRING) for the weekly snapshots.
    Until it exists the history restarts on every build (the change log on the former History panel needs it)."""
    try:
        df = db.query(f"SELECT payload FROM {config.APP_STATE_SCHEMA}.resourcing_history ORDER BY asof DESC LIMIT 1")
        if len(df):
            return json.loads(df.iloc[0]["payload"])
    except Exception:  # noqa: BLE001
        pass
    return {"runs": [], "changes": [], "snapshots": []}


def _history_save(hist: dict) -> None:
    try:
        payload = json.dumps(hist).replace("'", "''")
        db.execute(f"INSERT INTO {config.APP_STATE_SCHEMA}.resourcing_history VALUES (current_timestamp(), '{payload}')")
    except Exception:  # noqa: BLE001 - optional
        pass
