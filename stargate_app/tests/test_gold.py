"""Gold build: the engines reproduce the 4 Nov snapshot from its own inputs, and the full build runs offline on lake-shaped rows.
Run:  python -m pytest tests -q
"""
import json
import os

os.environ["STARGATE_SAMPLE"] = "1"

import gold_fixture as FX  # noqa: E402
from stargate_rm import db  # noqa: E402
from stargate_rm.gold import build as B, engines as E, fields as F  # noqa: E402

SNAP = FX.snapshot()
TODAY = SNAP["meta"]["built"]
MONTHS = SNAP["months_window"]
DUP_DEALS = {d for d in {x["id"] for x in SNAP["deals"]} if sum(1 for x in SNAP["deals"] if x["id"] == d) > 1}


def _unique_deals():
    seen = set()
    return [d for d in SNAP["deals"] if not (d["id"] in seen or seen.add(d["id"]))]


def test_role_cards_and_matcher_match_snapshot():
    mine = {(m["deal_id"], m["rss"]): m for m in E.matches(_unique_deals(), SNAP["people"])}
    snap = {}
    for m in SNAP["matches"]:
        snap.setdefault((m["deal_id"], m["rss"]), m)
    assert set(mine) == set(snap)
    for k, s in snap.items():
        m = mine[k]
        for f in ("role", "sfia", "days", "months", "sold", "strategic", "outcome", "route", "must"):
            assert m[f] == s[f], (k, f)
        assert [o["res"] for o in m["options"]] == [o["res"] for o in s["options"]], k


def test_exceptions_match_snapshot_without_milestones():
    by_name = {p["name"]: p["id"] for p in SNAP["people"]}
    asg = [dict(a, rid=by_name.get(a["name"])) for a in SNAP["assignments"]]
    mine = E.exceptions(SNAP["engagements"], SNAP["generics"], SNAP["utilisation"], SNAP["people"], asg, [], MONTHS, TODAY)
    for code in ("X01", "X02", "X03", "X27", "X29"):
        a = {(x["key"], x["detail"], x["due"]) for x in mine if x["rule"].startswith(code)}
        b = {(x["key"], x["detail"], x["due"]) for x in SNAP["exceptions"] if x["rule"].startswith(code)}
        assert a == b, code


def test_variance_selection_and_drivers_match_snapshot():
    ms = []
    for v in SNAP["variance"]:
        ms += [{"code": v["code"], "original_contracted": "2026-01-01", "contracted": "2026-02-01"}] * v["evidence"]["milestones_moved"]
        ms += [{"code": v["code"], "rag": "Amber"}] * v["evidence"]["amber_red_milestones"]
    months = sorted({r["month"] for r in SNAP["eng_month"]})
    mine = {v["code"]: v for v in E.variance(SNAP["engagements"], SNAP["generics"], ms, months)}
    snap = {v["code"]: v for v in SNAP["variance"]}
    assert set(mine) == set(snap)
    assert all(mine[k]["driver"] == snap[k]["driver"] and mine[k]["revenue_delta"] == snap[k]["revenue_delta"] for k in snap)


def test_levers_match_snapshot():
    people = [dict(p) for p in SNAP["people"]]
    by_name = {p["name"]: p for p in people}
    for b in SNAP["levers"]["bench"]:
        if b["free_days"] and b["name"] in by_name:
            by_name[b["name"]]["std_cost"] = b["unrecovered"] / b["free_days"]
    for a in SNAP["assignments"]:
        if a["name"] in by_name and a.get("std_cost"):
            by_name[a["name"]].setdefault("std_cost", a["std_cost"])
    lv = E.levers(people, SNAP["assignments"], SNAP["capacity"], MONTHS, TODAY)
    key = {"displace": lambda x: (x["code"], x["supplier"]), "over_graded": lambda x: (x["code"], x["name"]),
           "bench": lambda x: x["name"], "ending": lambda x: (x["code"], x["name"])}
    for k, f in key.items():
        assert {f(x) for x in lv[k]} == {f(x) for x in SNAP["levers"][k]}, k


def test_full_build_offline(monkeypatch):
    cols = {v.lower() for m in (F.DG, F.MILESTONE, F.HUBSPOT) for v in m.values() if v} | {F.ASSIGNMENT_CLIENT_ROLE}
    monkeypatch.setattr(db, "columns", lambda t: cols)
    gaps = []
    gold = B.build(TODAY, FX.fake_safe(FX.lake_rows()), gaps)
    assert gaps == []
    assert set(gold) == set(SNAP)
    for k in ("engagements", "demand", "capacity", "utilisation", "matches", "variance", "p123", "exceptions", "proposals", "eng_month", "deals", "people"):
        assert gold[k], k
        assert set(gold[k][0]) == set(SNAP[k][0]), k
    # Kantata's month values: the snapshot's doubled revenue is halved
    assert abs(gold["p123"][0]["p1"] - SNAP["p123"][0]["p1"] / 2) <= 2
    assert len({d["id"] for d in gold["deals"]}) == len(gold["deals"])
    json.dumps(gold)                                  # serialisable for the page
    assert B.rm_demand(gold)["rows"]


def test_missing_columns_become_gaps(monkeypatch):
    monkeypatch.setattr(db, "columns", lambda t: set())
    gaps = []
    gold = B.build(TODAY, FX.fake_safe({}), gaps)
    assert set(gold) == set(SNAP)
    assert any("milestones skipped" in g for g in gaps) and any("no column for" in g for g in gaps)
