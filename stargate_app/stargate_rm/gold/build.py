"""Build the workspace's gold data from the lake.

    gold = build(today, safe)

`safe(sql, what)` runs one query and returns its rows (nested like a SOQL result), or [] after recording the failure as a data
gap; the pipeline passes its own so gaps from both builds appear together.  The result has the exact keys and record shapes of
the JSON embedded in web/index.html; the server swaps it in when the page is served.

Units: Kantata holds forecast usage in days.  Booked hours = days x 8 (the UBDS day the page divides by); available hours =
working days x 7.5 (Kantata's day, which the page converts with availH).  Money is GBP as held in Kantata.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import defaultdict

from .. import config, db
from . import engines as E, fields as F, queries as Q, rules as R

USAGE_HOURS = 8.0
AVAIL_HOURS = R.HOURS_PER_DAY            # 7.5
GRADE_RE = re.compile(r"SFIA ?(\d)")
CODE_RE = re.compile(r"\((E\d+)\)")
UK_BANK_HOLIDAYS = {   # England and Wales; extend each year (also used by the matcher's capacity)
    "2026-01-01", "2026-04-03", "2026-04-06", "2026-05-04", "2026-05-25", "2026-08-31", "2026-12-25", "2026-12-28",
    "2027-01-01", "2027-03-26", "2027-03-29", "2027-05-03", "2027-05-31", "2027-08-30", "2027-12-27", "2027-12-28",
    "2028-01-03", "2028-04-14", "2028-04-17", "2028-05-01", "2028-05-29", "2028-08-28", "2028-12-25", "2028-12-26",
}


# ---------------------------------------------------------------- helpers
def _month_add(ym: str, k: int) -> str:
    y, m = int(ym[:4]), int(ym[5:7]) + k
    while m > 12:
        m -= 12; y += 1
    while m < 1:
        m += 12; y -= 1
    return f"{y:04d}-{m:02d}"


def _month_end(ym: str) -> str:
    return (dt.date.fromisoformat(_month_add(ym, 1) + "-01") - dt.timedelta(days=1)).isoformat()


def working_days(a: str, b: str) -> int:
    da, dbb = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    n, t = 0, da
    while t <= dbb:
        if t.weekday() < 5 and t.isoformat() not in UK_BANK_HOLIDAYS:
            n += 1
        t += dt.timedelta(days=1)
    return n


def _f(v) -> float:
    try:
        return float(v) if v is not None else 0.0
    except (TypeError, ValueError):
        return 0.0


def _day(v) -> str | None:
    return str(v)[:10] if v else None


def _sfia(grade: str | None) -> int | None:
    m = GRADE_RE.search(grade or "")
    return int(m.group(1)) if m else None


def _code(name: str | None) -> str | None:
    m = CODE_RE.search(name or "")
    return m.group(1) if m else None


def _generic_family(name: str | None) -> str:
    prefix = (name or "").split("SFIA")[0].strip()
    return R.GENERIC_PREFIX_FAMILY.get(prefix, "Unmapped")


def _flag(u: float) -> str:
    return "over" if u > R.OVER_UTILISED else "under" if u < R.UNDER_UTILISED else "ok"


_BANDS = {"commit": "Commit", "mostlikely": "Most Likely", "bestcase": "Best Case", "pipeline": "Pipeline", "omitted": "Omitted", "closedwon": "Closed Won"}


def _band(v) -> str | None:
    """HubSpot forecast status as the page labels it (commit -> Commit, most_likely -> Most Likely)."""
    if v is None:
        return None
    return _BANDS.get(str(v).lower().replace(" ", "").replace("_", ""), str(v))


def _yes(v) -> bool:
    return v is True or str(v).strip().lower() in ("true", "y", "yes", "1")


class Optional:
    """Turns field keys from fields.py into column expressions, or NULL where the table lacks the column (noted as a gap)."""

    def __init__(self, gaps: list):
        self.gaps = gaps
        self.tables: dict[str, tuple[str, dict]] = {}
        self.declared: dict[str, dict] = {}

    def table(self, alias: str, table: str, fieldmap: dict) -> None:
        try:
            cols = db.columns(table)
        except Exception as e:  # noqa: BLE001
            self.gaps.append(f"gold: cannot read {table}: {str(e).splitlines()[0][:160]}")
            cols = set()
        self.tables[alias] = (table, {k: (v if v and v.lower() in cols else None) for k, v in fieldmap.items()})
        self.declared[alias] = dict(fieldmap)

    def __call__(self, alias: str, key: str) -> str:
        table, fmap = self.tables[alias]
        col = fmap.get(key)
        if col is None and self.declared.get(alias, {}).get(key) is None:
            return "NULL"                       # documented as not in the lake (fields.py): not a gap
        if col is None:
            note = f"gold: {table}: no column for '{key}' (fields.py); it reads as empty"
            if note not in self.gaps:
                self.gaps.append(note)
            return "NULL"
        return f"{alias}.{col}"


# ---------------------------------------------------------------- build
def build(today: str, safe, gaps: list) -> dict:
    t = dt.date.fromisoformat(today)
    this_month = today[:7]
    monday = (t - dt.timedelta(days=t.weekday())).isoformat()
    prev_monday = (t - dt.timedelta(days=t.weekday() + 7)).isoformat()
    months_fwd = [_month_add(this_month, i) for i in range(R.FORWARD_MONTHS)]
    window = months_fwd[: R.MONTHS_WINDOW]
    eng_floor = _month_add(this_month, -1) + "-01"

    opt = Optional(gaps)
    opt.table("dg", config.kt("KimbleOne__DeliveryGroup__c"), F.DG)
    opt.table("aa", config.kt("KimbleOne__ActivityAssignment__c"), {"client_role": F.ASSIGNMENT_CLIENT_ROLE})
    opt.table("m", config.kt(F.MILESTONE_OBJECT), F.MILESTONE)
    opt.table("hs", F.HUBSPOT_DEALS_TABLE, F.HUBSPOT)

    from .. import queries as base
    res_recs = safe(base.resources(prev_monday), "gold: resources")
    cap_recs = safe(base.capabilities(today), "gold: skills")
    eng_recs = safe(Q.engagements(eng_floor, opt), "gold: engagements")
    mon_recs = safe(Q.monthly(months_fwd[0] + "-01", _month_end(months_fwd[-1]), opt), "gold: monthly forecast")
    asg_recs = safe(Q.assignments(monday, opt), "gold: assignments")
    de_col = opt.tables["m"][1].get("delivery_element")
    ms_recs = safe(Q.milestones(F.MILESTONE_OBJECT, de_col, opt), "gold: milestones") if de_col else []
    if not de_col:
        gaps.append("gold: milestones skipped (no delivery element column; see fields.py); X04, X18, X26 and milestone variance are empty")
    deal_recs = safe(Q.deals(F.HUBSPOT_DEALS_TABLE, opt, F.HUBSPOT_OPEN_BANDS), "gold: HubSpot deals") \
        if opt.tables["hs"][1].get("id") and opt.tables["hs"][1].get("fstatus") else []

    # ---- engagements
    engs, eng_by_id = [], {}
    for r in eng_recs:
        code = _code(r.get("name"))
        if not code:
            continue
        rev, cost, brev, bcost = _f(r.get("rev")), _f(r.get("cost")), _f(r.get("brev")), r.get("bcost")
        e = {"id": r["id"], "code": code, "name": r["name"], "client": r.get("client") or "", "fstatus": r.get("fstatus"), "stage": r.get("stage"),
             "start": _day(r.get("start")), "end": _day(r.get("end")), "rev": round(rev, 2), "cost": round(cost, 2),
             "margin": round((rev - cost) / rev * 100, 2) if rev else None, "brev": round(brev, 2),
             "bmargin": round((brev - _f(bcost)) / brev * 100, 2) if brev and bcost is not None else None,
             "family": R.OFFERING_FAMILY.get(r.get("offering") or "") or R.BU_FAMILY.get(r.get("product_bu") or "", "Unmapped"),
             "pre_mob": r.get("pre_mob"), "offering": r.get("offering") or r.get("product_bu"),
             "product_group": r.get("product_group"), "at_level": _yes(r.get("at_level")), "ms": r.get("product_group") in R.MS_PRODUCT_GROUPS,
             "hs_deal": str(r["hs_deal"]) if r.get("hs_deal") else None, "_bcost": _f(bcost) if bcost is not None else None}
        engs.append(e); eng_by_id[e["id"]] = e

    # ---- resources -> people (the RM scope) and capacity (that scope plus the excluded business units)
    skills = defaultdict(set)
    for c in cap_recs:
        rating = c.get("KimbleOne__NumericRating__c")
        if (rating is not None and _f(rating) > 0) or _yes(c.get("KimbleOne__BooleanRating__c")):
            name = (c.get("KimbleOne__CapabilityType__r") or {}).get("Name")
            if name:
                skills[c.get("KimbleOne__Resource__c")].add(name)
    by_month = defaultdict(lambda: defaultdict(lambda: [0.0, 0.0]))    # rid -> month -> [p1 days, p3 days]
    for r in mon_recs:
        if r.get("rtype") in ("Employee", "Supplier", "Partner") and r.get("rid"):
            x = by_month[r["rid"]][r["month"]]; x[0] += _f(r.get("u1")); x[1] += _f(r.get("u3"))
    capacity, people = [], []
    for r in res_recs:
        rtype = (r.get("KimbleOne__ResourceType__r") or {}).get("Name")
        bu = (r.get("KimbleOne__BusinessUnit__r") or {}).get("Name") or "—"
        end, start = _day(r.get("KimbleOne__EndDate__c")), _day(r.get("KimbleOne__StartDate__c"))
        if r.get("Suppress_Utilisation_reporting__c") in (True, "true", "True", 1, "1") or (end and end < prev_monday):
            continue
        grade = (r.get("KimbleOne__Grade__r") or {}).get("Name") or ""
        fam = R.BU_FAMILY.get(bu, "Unmapped")
        p1d = wd = 0.0
        for ym in window:
            a, b = max(ym + "-01", start or "0000"), min(_month_end(ym), end or "9999")
            if a > b:
                continue
            days = working_days(a, b)
            avail = days * AVAIL_HOURS
            u1, u3 = by_month[r["Id"]].get(ym, [0.0, 0.0])
            util = (u1 / days) if days else 0.0
            capacity.append({"res": r["Id"], "name": r["Name"], "family": fam, "bu": bu, "grade": grade, "type": rtype, "month": ym,
                             "p1_hours": round(u1 * USAGE_HOURS, 1), "p3_hours": round(u3 * USAGE_HOURS, 1), "available_hours": avail,
                             "utilisation": round(util, 2), "flag": _flag(util)})
            p1d += u1; wd += days
        if bu in R.EXCLUDE_BU:
            continue
        free = max(0.0, 1 - p1d / wd) if wd else 0.0
        role = (r.get("KimbleOne__ActivityRole__r") or {}).get("Name") or r.get("Budget_role__c") or "—"
        people.append({"id": r["Id"], "name": r["Name"], "type": rtype, "grade": grade, "sfia": _sfia(grade), "family": fam, "bu": bu, "role": role,
                       "skills": sorted(skills.get(r["Id"], [])), "free": round(free, 2), "end": end,
                       "p1_end": _day(r.get("KimbleOne__LatestP1AssignmentEndDate__c")), "contract_end": _day(r.get("UBDS_Contract_end_date__c")),
                       "_free": free, "std_cost": _f(r.get("KimbleOne__StandardCost__c")) or None})
    fam_of = {p["id"]: p["family"] for p in people}

    # ---- team utilisation (family x month; the snapshot's rule, reproduced 32 of 32)
    agg = defaultdict(lambda: [0, 0.0, 0.0, 0, 0])
    for c in capacity:
        a = agg[(c["family"], c["month"])]
        a[0] += 1; a[1] += c["p1_hours"]; a[2] += c["available_hours"]; a[3] += c["flag"] == "over"; a[4] += c["flag"] == "under"
    utilisation = [{"family": f, "month": m, "headcount": a[0], "p1_hours": round(a[1]), "available_hours": round(a[2]),
                    "utilisation": round(a[1] / (a[2] / AVAIL_HOURS * USAGE_HOURS), 2) if a[2] else 0.0, "over": a[3], "under": a[4]}
                   for (f, m), a in sorted(agg.items())]

    # ---- engagement x month, generics, demand, p123
    em = defaultdict(lambda: defaultdict(float))
    gen = {}
    gen_months = set(months_fwd[: R.GENERIC_MONTHS])
    for r in mon_recs:
        e = eng_by_id.get(r.get("dg"))
        if not e:
            continue
        x = em[(e["id"], r["month"])]
        for k in ("r1", "r2", "r3", "c1", "c2", "c3", "u1", "u3", "act"):
            x[k] += _f(r.get(k))
        if r.get("rtype") == "Generic Resource" and r["month"] in gen_months and _f(r.get("u3")) > 0:
            g = gen.setdefault((r["aid"], r["month"]), {"dg": e["id"], "code": e["code"], "client": e["client"], "fstatus": e["fstatus"],
                                                        "start": _day(r.get("start")), "ms": e["ms"], "month": r["month"], "generic": r.get("rname"),
                                                        "grade": r.get("grade"), "family": _generic_family(r.get("rname")),
                                                        "role": r.get("client_role") or None, "p3u": 0.0, "p3r": 0.0})
            g["p3u"] += _f(r.get("u3")) * USAGE_HOURS; g["p3r"] += _f(r.get("r3"))
    generics = [dict(g, p3u=round(g["p3u"], 2), p3r=round(g["p3r"], 2)) for g in gen.values()]
    eng_month = []
    for (eid, ym), x in sorted(em.items(), key=lambda kv: (kv[0][1], eng_by_id[kv[0][0]]["code"])):
        if not any(abs(v) > 0.005 for v in x.values()):
            continue
        e = eng_by_id[eid]
        eng_month.append({"code": e["code"], "client": e["client"], "fstatus": e["fstatus"], "family": e["family"], "ms": e["ms"], "at_level": e["at_level"],
                          "product_group": e["product_group"], "dg": eid, "month": ym,
                          "p1r": round(x["r1"], 2), "p2r": round(x["r2"], 2), "p3r": round(x["r3"], 2),
                          "p1c": round(x["c1"], 2), "p2c": round(x["c2"], 2), "p3c": round(x["c3"], 2),
                          "p1u": round(x["u1"] * USAGE_HOURS, 2), "p3u": round(x["u3"] * USAGE_HOURS, 2), "act": round(x["act"] * USAGE_HOURS, 2)})
    unf = defaultdict(float)
    eng_code_family = {e["code"]: e["family"] for e in engs}
    for g in generics:
        unf[(eng_code_family.get(g["code"], "Unmapped"), g["month"])] += g["p3u"]
    dagg = defaultdict(lambda: defaultdict(float))
    for r in eng_month:
        d = dagg[(r["family"], r["month"])]
        for k in ("p1r", "p2r", "p3r", "p1u", "p3u"):
            d[k] += r[k]
        if r["fstatus"] == "Pipeline":
            d["pipe"] += r["p3r"]
    demand = [{"month": m, "family": f, "p1": round(d["p1r"]), "p2": round(d["p2r"]), "p3": round(d["p3r"]), "p3u": round(d["p3u"]),
               "p1u": round(d["p1u"]), "unfilled_u": round(unf[(f, m)]), "p3_unweighted_pipeline": round(d["pipe"])}
              for (f, m), d in sorted(dagg.items(), key=lambda kv: (kv[0][1], kv[0][0]))]
    p123 = []
    for ym in months_fwd:
        rows = [r for r in eng_month if r["month"] == ym]
        p1, p2, p3 = (round(sum(r[k] for r in rows)) for k in ("p1r", "p2r", "p3r"))
        p123.append({"month": ym, "p1": p1, "p2": p2, "p3": p3, "weighted": round(p1 + R.P2_WEIGHT * (p2 - p1) + R.P3_WEIGHT * (p3 - p2))})

    # ---- assignments
    assignments = []
    for r in asg_recs:
        e = eng_by_id.get(r.get("dg"))
        if not e:
            continue
        rev, cost, usage = _f(r.get("rev")), _f(r.get("cost")), _f(r.get("usage"))
        typ = r.get("type")
        assignments.append({"id": r["id"], "code": e["code"], "client": e["client"], "fstatus": e["fstatus"], "ms": e["ms"], "product": e["product_group"],
                            "name": r.get("name"), "type": typ, "grade": r.get("grade"), "sfia": _sfia(r.get("grade")), "bu": r.get("bu"),
                            "family": _generic_family(r.get("name")) if typ == "Generic Resource" else fam_of.get(r.get("rid"), R.BU_FAMILY.get(r.get("bu") or "", "Unmapped")),
                            "role": r.get("client_role") or r.get("role"), "rev": round(rev), "cost": round(cost),
                            "margin": round((rev - cost) / rev * 100, 1) if rev else None,
                            "rev_rate": round(rev / usage / R.RATE_HOURS_PER_DAY, 4) if usage else None,
                            "cost_rate": round(cost / usage / R.RATE_HOURS_PER_DAY, 4) if usage else None,
                            "usage": round(usage, 4), "remaining": round(_f(r.get("remaining")), 4), "start": _day(r.get("start")), "end": _day(r.get("end")),
                            "pct": None, "std_cost": _f(r.get("std_cost")) or None, "target_rate": None,
                            "contract_end": _day(r.get("contract_end")) if typ in ("Supplier", "Partner") else None, "rid": r.get("rid")})

    # ---- deals (HubSpot), milestones
    seen, deals = set(), []
    for r in deal_recs:
        did = str(r.get("id") or "")
        if not did or did in seen:
            continue
        seen.add(did)
        deals.append({"id": did, "name": r.get("name"), "pipeline": r.get("pipeline"), "stage": r.get("stage"),
                      "prob": None if r.get("prob") is None else str(r["prob"]), "amount": None if r.get("amount") is None else str(r["amount"]),
                      "close": _day(r.get("close")), "start": _day(r.get("start")), "end": _day(r.get("end")), "bu": r.get("bu"),
                      "offering": r.get("offering"), "fstatus": _band(r.get("fstatus")), "margin": r.get("margin"), "owner": r.get("owner"),
                      "sf_account": r.get("sf_account"), "company": r.get("company")})
    milestones = []
    for r in ms_recs:
        e = eng_by_id.get(r.get("dg"))
        if e:
            milestones.append({"id": r["id"], "name": r.get("name"), "code": e["code"], "description": r.get("description"),
                               "forecast": _day(r.get("forecast")), "contracted": _day(r.get("contracted")),
                               "original_contracted": _day(r.get("original_contracted")), "rag": r.get("rag"),
                               "rag_updated": _day(r.get("rag_updated")),
                               "complete": bool(r.get("delivered")) or str(r.get("status") or "").strip().lower() in F.MILESTONE_DONE_STATUSES})

    # ---- engines
    mt = E.matches(deals, people)
    excs = E.exceptions(engs, generics, utilisation, people, assignments, milestones, window, today)
    props = E.proposals(mt, people, excs, today)
    var = E.variance(engs, generics, milestones, months_fwd)
    lev = E.levers(people, assignments, capacity, window, today)

    strip = lambda rows, extra=(): [{k: v for k, v in x.items() if not k.startswith("_") and k not in extra} for x in rows]   # noqa: E731
    return {
        "meta": {"built": today,
                 "sources": f"Databricks lake: Kantata {config.KANTATA_CATALOG}.{config.KANTATA_SCHEMA}, HubSpot {F.HUBSPOT_DEALS_TABLE} "
                            f"(read-only, built by the app each hour); role cards derived from open deals",
                 "units": "forecast usage in hours (Kantata days x 8; availability at 7.5 h per working day); money GBP; P2 at 80%, P3 at 50% for the weighted view",
                 "months": months_fwd},
        "engagements": strip(engs), "demand": demand, "generics": generics, "capacity": capacity, "utilisation": utilisation, "matches": mt,
        "variance": var, "drivers": R.VARIANCE_DRIVERS, "p123": p123, "exceptions": excs, "proposals": props, "assignments": assignments,
        "levers": lev, "eng_month": eng_month, "deals": deals, "people": strip(people, ("std_cost",)), "role_family": R.ROLE_FAMILY,
        "exclude_bu": R.EXCLUDE_BU, "months_window": window,
    }


def rm_demand(gold: dict) -> dict:
    """dash/rm/demand.json (the Utilisation page's demand by family) from the same build."""
    return {"asof": gold["meta"]["built"], "source": "Databricks lake (stargate_rm.gold): demand by capability family and month",
            "months": gold["meta"]["months"], "hours_per_day": int(USAGE_HOURS),
            "rows": [{"family": d["family"], "month": d["month"], "p1": d["p1"], "p2": d["p2"], "p3": d["p3"], "unfilled_u": d["unfilled_u"], "p3u": d["p3u"]}
                     for d in sorted(gold["demand"], key=lambda d: (d["month"], d["family"]))]}
