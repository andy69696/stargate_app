"""Offline input for the gold build: the rows the lake queries would return, reconstructed from the published 4 Nov snapshot
embedded in web/index.html (its doubled figures halved back to Kantata's month values).

    rows = lake_rows()           # {query label: [records]}
    safe = fake_safe(rows)       # stands in for pipeline._safe_sql
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

WEB = Path(__file__).resolve().parent.parent / "web"


def snapshot() -> dict:
    html = (WEB / "index.html").read_text(encoding="utf-8")
    return json.loads(re.search(r'<script id="gold" type="application/json">(.*?)</script>', html, re.S).group(1))


def lake_rows() -> dict:
    g = snapshot()
    eng_by_code = {e["code"]: e for e in g["engagements"]}
    pid = {p["name"]: p for p in g["people"]}
    resources = []
    seen = set()
    for c in g["capacity"]:
        if c["res"] in seen:
            continue
        seen.add(c["res"])
        p = next((x for x in g["people"] if x["id"] == c["res"]), {})
        cost = next((b["unrecovered"] / b["free_days"] for b in g["levers"]["bench"] if b["name"] == c["name"] and b["free_days"]), None)
        cost = cost or next((a["std_cost"] for a in g["assignments"] if a["name"] == c["name"] and a.get("std_cost")), 400)
        resources.append({"Id": c["res"], "Name": c["name"], "KimbleOne__ResourceType__r": {"Name": c["type"]}, "KimbleOne__Grade__r": {"Name": c["grade"]},
                          "KimbleOne__BusinessUnit__r": {"Name": c["bu"]}, "KimbleOne__ActivityRole__r": {"Name": p.get("role")},
                          "KimbleOne__StartDate__c": None, "KimbleOne__EndDate__c": p.get("end"), "UBDS_Contract_end_date__c": p.get("contract_end"),
                          "KimbleOne__StandardCost__c": round(cost), "KimbleOne__LatestP1AssignmentEndDate__c": p.get("p1_end"),
                          "Suppress_Utilisation_reporting__c": False})
    skills = [{"KimbleOne__Resource__c": p["id"], "KimbleOne__CapabilityType__r": {"Name": s}, "KimbleOne__NumericRating__c": 3,
               "KimbleOne__BooleanRating__c": None} for p in g["people"] for s in p["skills"]]
    engs = [{"id": e["id"], "name": e["name"], "client": e["client"], "fstatus": e["fstatus"], "stage": e["stage"], "start": e["start"], "end": e["end"],
             "product_group": e["product_group"], "at_level": "Y" if e["at_level"] else "N", "rev": e["rev"], "cost": e["cost"], "brev": e["brev"] or None,
             "bcost": (e["brev"] * (1 - e["bmargin"] / 100)) if e["brev"] and e["bmargin"] is not None else None, "offering": e["offering"],
             "pre_mob": e["pre_mob"], "hs_deal": e["hs_deal"]} for e in g["engagements"]]
    monthly = []
    for c in g["capacity"]:   # named people's P1/P3 days by month, on a placeholder engagement row
        monthly.append({"aid": "cap-" + c["res"], "rid": c["res"], "rname": c["name"], "rtype": c["type"], "grade": c["grade"], "dg": None,
                        "month": c["month"], "u1": c["p1_hours"] / 2, "u2": c["p1_hours"] / 2, "u3": c["p3_hours"] / 2})
    for r in g["eng_month"]:  # engagement totals (halved) on an "Employee" row without a person, so capacity is not double counted
        monthly.append({"aid": "em-" + r["dg"], "rid": None, "rname": None, "rtype": "Employee", "grade": None, "dg": r["dg"], "month": r["month"],
                        "r1": r["p1r"] / 2, "r2": r["p2r"] / 2, "r3": r["p3r"] / 2, "c1": r["p1c"] / 2, "c2": r["p2c"] / 2, "c3": r["p3c"] / 2,
                        "u1": r["p1u"] / 2 / 8, "u3": r["p3u"] / 2 / 8, "act": 0})
    for i, x in enumerate(g["generics"]):
        monthly.append({"aid": f"gen-{x['dg']}-{x['generic']}", "rid": None, "rname": x["generic"], "rtype": "Generic Resource", "grade": x["grade"],
                        "dg": x["dg"], "client_role": x["role"], "start": x["start"], "month": x["month"], "u3": x["p3u"] / 2 / 8, "r3": x["p3r"] / 2})
    asg = [{"id": a["id"], "dg": eng_by_code[a["code"]]["id"], "rid": (pid.get(a["name"]) or {}).get("id"), "name": a["name"], "type": a["type"],
            "grade": a["grade"], "bu": a["bu"], "role": a["role"], "rev": a["rev"], "cost": a["cost"], "usage": a["usage"], "remaining": a["remaining"],
            "start": a["start"], "end": a["end"], "std_cost": a["std_cost"], "contract_end": a["contract_end"]} for a in g["assignments"]]
    deals = [dict(d, closed=False) for d in g["deals"]]
    return {"gold: resources": resources, "gold: skills": skills, "gold: engagements": engs, "gold: monthly forecast": monthly,
            "gold: assignments": asg, "gold: milestones": [], "gold: HubSpot deals": deals}


def fake_safe(rows: dict):
    calls = defaultdict(int)

    def safe(sql_text: str, what: str) -> list[dict]:
        calls[what] += 1
        return json.loads(json.dumps(rows.get(what, [])))
    safe.calls = calls
    return safe
