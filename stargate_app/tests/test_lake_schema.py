"""Every SQL statement the live build generates must reference only tables and columns that exist in the lake.

tests/lake_schema.csv is the column export from Databricks (information_schema.columns for salesforce_dev.bronze kimbleone__*,
account, user and hubspot_dev.silver.deals).  Re-export it when the ingestion changes.
Run:  python -m pytest tests -q        (needs sqlglot: pip install -r requirements-dev.txt)
"""
import csv
import os
from collections import defaultdict
from pathlib import Path

import pandas as pd
import pytest

sqlglot = pytest.importorskip("sqlglot")
from sqlglot import exp  # noqa: E402

SCHEMA_CSV = Path(__file__).with_name("lake_schema.csv")


def _schema() -> dict:
    s = defaultdict(set)
    with open(SCHEMA_CSV) as f:
        for r in csv.DictReader(f):
            s[f"{r['table_catalog']}.{r['table_schema']}.{r['table_name']}".lower()].add(r["column_name"].lower())
    return s


def _captured_sql(monkeypatch, schema) -> list[str]:
    from stargate_rm import config, db, pipeline
    monkeypatch.setattr(config, "SAMPLE_MODE", False)
    sqls = []
    monkeypatch.setattr(db, "query", lambda sql_text, cached=True, params=None: sqls.append(sql_text) or pd.DataFrame())
    monkeypatch.setattr(db, "execute", lambda sql_text, params=None: sqls.append(sql_text))
    monkeypatch.setattr(db, "columns", lambda t: schema.get(t.lower(), set()))
    pipeline.build_data("2026-11-06")
    return sqls


def _problems(sql_text: str, schema: dict) -> list[str]:
    tree = sqlglot.parse_one(sql_text, read="databricks")
    alias, out = {}, []
    for t in tree.find_all(exp.Table):
        full = ".".join(p for p in [t.catalog, t.db, t.name] if p).lower()
        alias[(t.alias_or_name or t.name).lower()] = full
        if full.count(".") == 2 and full not in schema and config_app_state() not in full:
            out.append(f"missing table {full}")
    tables = set(alias.values())
    named = {a.alias.lower() for a in tree.find_all(exp.Alias)}
    for c in tree.find_all(exp.Column):
        tbl = (c.table or "").lower()
        full = alias.get(tbl) if tbl else (next(iter(tables)) if len(tables) == 1 and c.name.lower() not in named else None)
        if full in schema and c.name.lower() not in schema[full]:
            out.append(f"missing column {full.split('.')[-1]}.{c.name.lower()}")
    return out


def config_app_state() -> str:
    from stargate_rm import config
    return config.APP_STATE_SCHEMA.lower()


def test_every_query_matches_the_lake_schema(monkeypatch):
    schema = _schema()
    sqls = _captured_sql(monkeypatch, schema)
    assert len(sqls) >= 30
    bad = {p: s[:120] for s in sqls for p in _problems(s, schema)}
    assert not bad, bad


def test_optional_reference_tables_are_not_joined_when_absent(monkeypatch):
    schema = _schema()
    sqls = _captured_sql(monkeypatch, schema)
    joined = " ".join(sqls).lower()
    for t in ("kimbleone__productgroup__c", "kimbleone__location__c", "kimbleone__timepattern__c", "kimbleone__unittype__c",
              "kimbleone__resourceactivityrole__c"):
        assert f"bronze.{t}" not in joined, t
    os.environ.pop("STARGATE_SAMPLE", None)


def test_diagnostic_probes_match_the_lake_schema(monkeypatch):
    from stargate_rm import db, diagnose
    schema = _schema()
    monkeypatch.setattr(db, "columns", lambda t: schema.get(t.lower(), set()))
    plist = diagnose.probes("2026-11-07") + [diagnose.freshness_probe()]
    bad = {}
    for key, _what, sql_text in plist:
        for p in _problems(sql_text, schema):
            bad[f"{key}: {p}"] = sql_text[:100]
    assert not bad, bad


def test_diagnose_runs_end_to_end(monkeypatch):
    import json
    from stargate_rm import config, db, diagnose
    schema = _schema()
    monkeypatch.setattr(config, "SAMPLE_MODE", False)
    monkeypatch.setattr(db, "columns", lambda t: schema.get(t.lower(), set()))

    def fake_query(sql_text, cached=True, params=None):
        db._notify(sql_text, pd.DataFrame({"x": [1, None]}), 0.01, None)
        return pd.DataFrame({"x": [1, None]})
    monkeypatch.setattr(db, "query", fake_query)
    monkeypatch.setattr(db, "execute", lambda sql_text, params=None: None)
    monkeypatch.setattr(db, "records", lambda sql_text: [])
    report, _data = diagnose.run("2026-11-07")
    json.dumps(report, default=str)
    assert report["build"]["error"] is None
    assert {p["probe"] for p in report["probes"]} >= {"duplicate_rows", "week_start_days", "billable_flag", "usage_units", "freshness"}
    assert all("error" not in p for p in report["probes"])
    assert db.observers == []
