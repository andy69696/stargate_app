"""Diagnostics for the Resource manager views: GET /api/diagnose.

Runs a fresh lake build with every query observed, then a set of small probe queries, and returns one JSON report:

  queries   every query the build ran: label, SQL, rows, seconds, error, and per column how many values are empty
  probes    the values the queries filter on (period types, statuses, resource types, absence activities, the billable flag,
            business units, grades, product groups), duplicate rows, week start days, usage units and data freshness
  outputs   what the Resource manager pages receive (people in scope, weeks, placeholders, matcher, exceptions, demand)

No personal data: counts, category names, engagement names (as examples) and SQL only; no people's names, costs or rates.
"""
from __future__ import annotations

import datetime as dt
import time
import traceback
from collections import Counter

from . import config, db, queries
from .config import kt

MAX_ROWS = 60


def _t(obj: str) -> str:
    return kt(obj)


def probes(today: str) -> list[tuple[str, str, str]]:
    """(key, what it checks, SQL).  Each runs on its own; one failing does not stop the rest."""
    t = dt.date.fromisoformat(today)
    q_from, q_to = (t - dt.timedelta(days=14)).isoformat(), (t + dt.timedelta(days=120)).isoformat()
    core = ["KimbleOne__Resource__c", "KimbleOne__ActivityAssignment__c", "KimbleOne__ActivityPeriodAssignment__c", "KimbleOne__DeliveryGroup__c",
            "KimbleOne__TimePeriod__c", "KimbleOne__ResourceCapability__c", "KimbleOne__Milestone__c", "KimbleOne__ForecastStatus__c",
            "KimbleOne__ResourceType__c", "KimbleOne__PeriodType__c", "KimbleOne__BusinessUnit__c", "KimbleOne__Grade__c"]
    dup = " UNION ALL ".join(
        f"SELECT '{o}' AS tbl, COUNT(*) AS row_count, COUNT(DISTINCT id) AS distinct_ids, "
        f"SUM(CASE WHEN COALESCE(CAST(isdeleted AS BOOLEAN), false) THEN 1 ELSE 0 END) AS deleted_rows FROM {_t(o)}" for o in core)
    apa = f"""FROM {_t('KimbleOne__ActivityPeriodAssignment__c')} apa
      JOIN {_t('KimbleOne__TimePeriod__c')} tp ON tp.id = apa.kimbleone__timeperiod__c
      LEFT JOIN {_t('KimbleOne__PeriodType__c')} pt ON pt.id = tp.kimbleone__periodtype__c
      LEFT JOIN {_t('KimbleOne__Resource__c')} r ON r.id = apa.kimbleone__resource__c
      LEFT JOIN {_t('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
      LEFT JOIN {_t('KimbleOne__ActivityAssignment__c')} aa ON aa.id = apa.kimbleone__activityassignment__c"""
    return [
        ("duplicate_rows", "Rows vs distinct Ids per table. More rows than Ids means bronze keeps several copies of a record and every total is multiplied.", dup),
        ("period_types", "Period types and how many time periods each has (queries need 'Week' and 'Month').",
         f"""SELECT pt.name AS period_type, COUNT(*) AS periods, MIN(tp.kimbleone__startdate__c) AS first_start, MAX(tp.kimbleone__startdate__c) AS last_start
               FROM {_t('KimbleOne__TimePeriod__c')} tp LEFT JOIN {_t('KimbleOne__PeriodType__c')} pt ON pt.id = tp.kimbleone__periodtype__c
              GROUP BY pt.name ORDER BY periods DESC"""),
        ("week_start_days", "Day of week on which 'Week' periods start (utilisation weeks are keyed on Mondays).",
         f"""SELECT date_format(tp.kimbleone__startdate__c, 'EEEE') AS start_day, COUNT(*) AS periods
               FROM {_t('KimbleOne__TimePeriod__c')} tp JOIN {_t('KimbleOne__PeriodType__c')} pt ON pt.id = tp.kimbleone__periodtype__c
              WHERE pt.name = 'Week' GROUP BY 1 ORDER BY periods DESC"""),
        ("forecast_rows_by_period_type", f"Forecast rows and usage in the next four months, by period type and resource type ({q_from} to {q_to}).",
         f"""SELECT pt.name AS period_type, rt.name AS resource_type, COUNT(*) AS apa_rows, ROUND(SUM(apa.kimbleone__forecastp1usage__c), 1) AS p1_usage,
                    ROUND(SUM(apa.kimbleone__forecastp3usage__c), 1) AS p3_usage, COUNT(DISTINCT apa.kimbleone__resource__c) AS resources
               {apa} WHERE tp.kimbleone__startdate__c BETWEEN DATE '{q_from}' AND DATE '{q_to}'
              GROUP BY pt.name, rt.name ORDER BY apa_rows DESC"""),
        ("usage_units", "P3 usage per resource per week (next 8 weeks): about 5 a week means days, about 37.5 means hours.",
         f"""SELECT COUNT(*) AS resource_weeks, ROUND(percentile(u, 0.5), 2) AS median, ROUND(percentile(u, 0.9), 2) AS p90, ROUND(MAX(u), 2) AS max
               FROM (SELECT apa.kimbleone__resource__c AS rid, tp.kimbleone__startdate__c AS wk, SUM(apa.kimbleone__forecastp3usage__c) AS u
                       {apa} WHERE pt.name = 'Week' AND rt.name = 'Employee'
                        AND tp.kimbleone__startdate__c BETWEEN DATE '{t.isoformat()}' AND DATE '{(t + dt.timedelta(days=56)).isoformat()}'
                      GROUP BY 1, 2) x WHERE u > 0"""),
        ("forecast_statuses", "Forecast status names on delivery groups (queries exclude 'Lost Closed').",
         f"""SELECT fs.name AS forecast_status, COUNT(*) AS delivery_groups FROM {_t('KimbleOne__DeliveryGroup__c')} dg
               LEFT JOIN {_t('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c GROUP BY 1 ORDER BY 2 DESC"""),
        ("resource_types", "Resource types of resources active from today (queries use Employee, Supplier, Partner, Generic Resource).",
         f"""SELECT rt.name AS resource_type, COUNT(*) AS resources FROM {_t('KimbleOne__Resource__c')} r
               LEFT JOIN {_t('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
              WHERE r.kimbleone__enddate__c IS NULL OR r.kimbleone__enddate__c >= DATE '{today}' GROUP BY 1 ORDER BY 2 DESC"""),
        ("billable_flag", "suppress_utilisation_reporting__c values by resource type (suppressed people are left out of Utilisation).",
         f"""SELECT rt.name AS resource_type, CAST(r.suppress_utilisation_reporting__c AS STRING) AS suppress_value,
                    typeof(r.suppress_utilisation_reporting__c) AS stored_as, COUNT(*) AS resources
               FROM {_t('KimbleOne__Resource__c')} r LEFT JOIN {_t('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
              WHERE r.kimbleone__enddate__c IS NULL OR r.kimbleone__enddate__c >= DATE '{today}' GROUP BY 1, 2, 3 ORDER BY 1, 4 DESC"""),
        ("business_units", "Business units of active employees (Service Delivery and Client Success are excluded; the unit sets the family).",
         f"""SELECT bu.name AS business_unit, COUNT(*) AS employees FROM {_t('KimbleOne__Resource__c')} r
               LEFT JOIN {_t('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
               LEFT JOIN {_t('KimbleOne__BusinessUnit__c')} bu ON bu.id = r.kimbleone__businessunit__c
              WHERE rt.name = 'Employee' AND (r.kimbleone__enddate__c IS NULL OR r.kimbleone__enddate__c >= DATE '{today}') GROUP BY 1 ORDER BY 2 DESC"""),
        ("grades", "Grade names of active people (SFIA level is read from 'SFIA n' in the name).",
         f"""SELECT gr.name AS grade, COUNT(*) AS resources FROM {_t('KimbleOne__Resource__c')} r
               LEFT JOIN {_t('KimbleOne__Grade__c')} gr ON gr.id = r.kimbleone__grade__c
               LEFT JOIN {_t('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
              WHERE rt.name IN ('Employee','Supplier','Partner') AND (r.kimbleone__enddate__c IS NULL OR r.kimbleone__enddate__c >= DATE '{today}')
              GROUP BY 1 ORDER BY 2 DESC LIMIT {MAX_ROWS}"""),
        ("non_engagement_activities", f"Activities with forecast time but no delivery group, next four months (absence is matched on: {', '.join(queries.ABSENCE_ACTIVITIES)}).",
         f"""SELECT ra.name AS activity, pt.name AS period_type, COUNT(*) AS apa_rows, ROUND(SUM(apa.kimbleone__forecastp3usage__c), 1) AS p3_usage
               {apa} LEFT JOIN {_t('KimbleOne__ResourcedActivity__c')} ra ON ra.id = aa.kimbleone__resourcedactivity__c
              WHERE aa.kimbleone__deliverygroup__c IS NULL AND tp.kimbleone__startdate__c BETWEEN DATE '{q_from}' AND DATE '{q_to}'
              GROUP BY 1, 2 ORDER BY apa_rows DESC LIMIT {MAX_ROWS}"""),
        ("engagement_names", "How delivery group names look: internal (UBDS-%) and with an (E1234) engagement code, which the exceptions need.",
         f"""SELECT CASE WHEN dg.name LIKE 'UBDS-%' THEN 'internal UBDS-%' WHEN dg.name RLIKE '\\\\((E[0-9]+)\\\\)' THEN 'has (Ennnn) code' ELSE 'no code' END AS kind,
                    COUNT(*) AS delivery_groups, slice(collect_list(dg.name), 1, 5) AS examples
               FROM {_t('KimbleOne__DeliveryGroup__c')} dg
               LEFT JOIN {_t('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
              WHERE COALESCE(fs.name, '') <> 'Lost Closed' AND (dg.kimbleone__expectedenddate__c >= DATE '{today}' OR dg.kimbleone__expectedenddate__c IS NULL)
              GROUP BY 1"""),
        ("product_groups", "Product group Ids on live engagements with examples (the product group table is not in bronze; name the managed-service ones in PRODUCT_GROUP_NAMES).",
         f"""SELECT dg.kimbleone__productgroup__c AS product_group_id, COUNT(*) AS delivery_groups, slice(collect_list(dg.name), 1, 4) AS examples
               FROM {_t('KimbleOne__DeliveryGroup__c')} dg
               LEFT JOIN {_t('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
              WHERE COALESCE(fs.name, '') <> 'Lost Closed' AND (dg.kimbleone__expectedenddate__c >= DATE '{today}' OR dg.kimbleone__expectedenddate__c IS NULL)
              GROUP BY 1 ORDER BY 2 DESC LIMIT {MAX_ROWS}"""),
        ("generic_placeholders", "Live generic placeholders by forecast status and whether the assignment has P3 remaining.",
         f"""SELECT fs.name AS forecast_status, COUNT(*) AS assignments, SUM(CASE WHEN aa.kimbleone__forecastremainingusage__c > 0 THEN 1 ELSE 0 END) AS with_remaining
               FROM {_t('KimbleOne__ActivityAssignment__c')} aa
               LEFT JOIN {_t('KimbleOne__Resource__c')} r ON r.id = aa.kimbleone__resource__c
               LEFT JOIN {_t('KimbleOne__ResourceType__c')} rt ON rt.id = r.kimbleone__resourcetype__c
               LEFT JOIN {_t('KimbleOne__DeliveryGroup__c')} dg ON dg.id = aa.kimbleone__deliverygroup__c
               LEFT JOIN {_t('KimbleOne__ForecastStatus__c')} fs ON fs.id = dg.kimbleone__forecaststatus__c
              WHERE rt.name = 'Generic Resource' AND aa.kimbleone__forecastp3enddate__c >= DATE '{today}' GROUP BY 1 ORDER BY 2 DESC"""),
        ("skills", "Skill ratings: how many people have any rated skill (the matcher scores on them).",
         f"""SELECT COUNT(DISTINCT rc.kimbleone__resource__c) AS people_with_skills, COUNT(*) AS ratings,
                    SUM(CASE WHEN rc.kimbleone__numericrating__c > 0 THEN 1 ELSE 0 END) AS numeric_rated,
                    SUM(CASE WHEN CAST(rc.kimbleone__booleanrating__c AS STRING) IN ('true', 'True', '1') THEN 1 ELSE 0 END) AS boolean_true
               FROM {_t('KimbleOne__ResourceCapability__c')} rc"""),
        ("milestone_values", "Milestone status and RAG values (the exceptions view uses them).",
         f"""SELECT CAST(m.kimbleone__milestonestatus__c AS STRING) AS status, CAST(m.ubds_rag_status__c AS STRING) AS rag, COUNT(*) AS milestones,
                    SUM(CASE WHEN m.ubds_milestone_delivered_date__c IS NOT NULL THEN 1 ELSE 0 END) AS delivered
               FROM {_t('KimbleOne__Milestone__c')} m GROUP BY 1, 2 ORDER BY 3 DESC LIMIT {MAX_ROWS}"""),
    ]


def freshness_probe() -> tuple[str, str, str] | None:
    """Latest ingest and latest Salesforce edit per core table (only for columns the tables have)."""
    parts = []
    for o in ("KimbleOne__Resource__c", "KimbleOne__ActivityAssignment__c", "KimbleOne__ActivityPeriodAssignment__c", "KimbleOne__DeliveryGroup__c"):
        try:
            cols = db.columns(kt(o))
        except Exception:  # noqa: BLE001
            continue
        ing = "CAST(MAX(_ingest_timestamp) AS STRING)" if "_ingest_timestamp" in cols else "CAST(NULL AS STRING)"
        mod = "CAST(MAX(systemmodstamp) AS STRING)" if "systemmodstamp" in cols else ("CAST(MAX(lastmodifieddate) AS STRING)" if "lastmodifieddate" in cols else "CAST(NULL AS STRING)")
        parts.append(f"SELECT '{o}' AS tbl, {ing} AS last_ingest, {mod} AS last_salesforce_edit FROM {kt(o)}")
    if not parts:
        return None
    return ("freshness", "When each core table was last loaded, and the newest Salesforce edit in it.", " UNION ALL ".join(parts))


def _jsonable(v):
    if v is None or isinstance(v, (str, int, bool)):
        return v
    if isinstance(v, float):
        return None if v != v else round(v, 4)
    if hasattr(v, "tolist"):
        return _jsonable(v.tolist())
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return str(v)


def _profile(df) -> dict:
    out = {}
    for c in df.columns:
        s = df[c]
        empty = int(s.isna().sum() + (s.astype(str).str.strip() == "").sum()) if len(s) else 0
        try:
            distinct = int(s.nunique(dropna=True))
        except TypeError:
            distinct = None
        out[c] = {"empty": empty, "distinct": distinct}
    return out


def _outputs(data: dict) -> dict:
    rm, res, mt, gold = data.get("rm") or {}, data.get("res") or {}, data.get("match") or {}, data.get("gold") or {}
    rows = rm.get("rows") or []
    out = {
        "rm_dashboard (dash/rm/data.json)": {
            "people": len(rows), "summary": rm.get("summary"), "weeks": rm.get("weeks") or [], "months": rm.get("months") or [],
            "people_by_type": dict(Counter(r.get("rt") for r in rows)),
            "people_with_no_booked_weeks": sum(1 for r in rows if not any((w or {}).get("p3") for w in (r.get("weeks") or []))),
            "people_with_no_capacity": sum(1 for r in rows if not any((w or {}).get("cap") for w in (r.get("weeks") or []))),
            "people_with_absence": sum(1 for r in rows if any((w or {}).get("absence") for w in (r.get("weeks") or []))),
            "people_with_margin_months": sum(1 for r in rows if r.get("tot_rev")),
        },
        "resourcing (dash/resourcing/data.json)": {k: (len(v) if isinstance(v, list) else v) for k, v in res.items() if k not in ("months",) and not isinstance(v, dict)},
        "matcher (dash/resourcing/match.json)": {k: len(v) for k, v in mt.items() if isinstance(v, list)},
        "demand (dash/rm/demand.json)": {"rows": len((data.get("rm_demand") or {}).get("rows") or [])},
        "exceptions view (gold)": {
            "built_from": "lake" if gold else f"snapshot ({data.get('gold_error') or 'gold not built'})",
            "exceptions_by_rule": dict(Counter(x["rule"][:3] for x in gold.get("exceptions", []))),
            "engagements": len(gold.get("engagements", [])), "people": len(gold.get("people", [])),
            "capacity_rows": len(gold.get("capacity", [])), "generics": len(gold.get("generics", [])),
        } if gold or data.get("gold_error") else {"built_from": "not built"},
    }
    return out


def run(today: str | None = None) -> tuple[dict, dict]:
    """Fresh build with every query observed, then the probes.  Holds no lock: the caller serialises builds."""
    from . import pipeline

    today = today or dt.date.today().isoformat()
    t0 = time.time()
    seen: list[dict] = []

    def observe(label, sql_text, df, seconds, error):
        if sql_text.startswith("SELECT * FROM") and sql_text.endswith("LIMIT 0"):
            return                                   # column lookups
        seen.append({"label": label or "(unlabelled)", "rows": None if df is None else len(df), "seconds": round(seconds, 2), "error": error,
                     "columns": None if df is None else _profile(df), "sql": " ".join(sql_text.split())})

    report: dict = {"generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"), "today": today,
                    "lake": f"{config.KANTATA_CATALOG}.{config.KANTATA_SCHEMA}", "hubspot": config.HUBSPOT_DEALS_TABLE,
                    "sample_mode": config.SAMPLE_MODE, "product_group_names_configured": len(config.PRODUCT_GROUP_NAMES),
                    "optional_tables_present": {o: queries.has(o) for o in queries.OPTIONAL_REFERENCE_OBJECTS} if not config.SAMPLE_MODE else None}
    db.clear_cache()
    db.observers.append(observe)
    try:
        data = pipeline.build_data(today)
        report["build"] = {"seconds": round(time.time() - t0, 1), "gaps": data.get("gaps", []), "error": None}
    except Exception as e:  # noqa: BLE001
        data = {}
        report["build"] = {"seconds": round(time.time() - t0, 1), "gaps": [], "error": f"{type(e).__name__}: {e}", "trace": traceback.format_exc()[-2000:]}
    finally:
        db.observers.remove(observe)
    report["queries"] = seen
    report["queries_summary"] = {"total": len(seen), "failed": [q["label"] for q in seen if q["error"]],
                                 "returned_no_rows": [q["label"] for q in seen if q["rows"] == 0]}
    report["outputs"] = _outputs(data) if data else None

    probe_out = []
    if not config.SAMPLE_MODE:
        plist = probes(today)
        fp = freshness_probe()
        if fp:
            plist.insert(0, fp)
        for key, what, sql_text in plist:
            p0 = time.time()
            try:
                df = db.query(sql_text, cached=False)
                probe_out.append({"probe": key, "checks": what, "rows": [{k: _jsonable(v) for k, v in r.items()} for r in df.head(MAX_ROWS).to_dict("records")],
                                  "seconds": round(time.time() - p0, 2)})
            except Exception as e:  # noqa: BLE001
                probe_out.append({"probe": key, "checks": what, "error": str(e).splitlines()[0][:400] if str(e) else type(e).__name__, "sql": " ".join(sql_text.split())})
    report["probes"] = probe_out
    report["seconds"] = round(time.time() - t0, 1)
    return report, data
