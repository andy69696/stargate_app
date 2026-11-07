"""Gold engines: role cards and matcher, proposals with cross-checks, exceptions, variance, levers.

Inputs are the gold data parts already built (people, assignments, engagements, generics, capacity, deals) plus milestones;
outputs have exactly the shapes the workspace page reads.  Rules and thresholds live in rules.py.
"""
from __future__ import annotations

import datetime as dt
from collections import Counter, defaultdict

from . import rules as R


def _d(s: str | None) -> dt.date | None:
    return dt.date.fromisoformat(str(s)[:10]) if s else None


def _free(p: dict) -> float:
    """A person's free share at full precision (the output field is rounded to 2 places; scoring and ranking use the exact value)."""
    return p.get("_free", p["free"])


def _add(day: str, n: int) -> str:
    return (dt.date.fromisoformat(day) + dt.timedelta(days=n)).isoformat()


def bdays(a: str | None, b: str | None) -> int:
    da, db_ = _d(a), _d(b)
    if not da or not db_ or db_ < da:
        return 0
    n, t = 0, da
    while t <= db_:
        n += t.weekday() < 5
        t += dt.timedelta(days=1)
    return n


def card_months(start: str | None, end: str | None) -> int:
    """The page's month count: whole 30-day months between start and end, at least 1."""
    if not start or not end:
        return 0
    return max(1, round((_d(end) - _d(start)).days / 30))


# ---------------------------------------------------------------- role cards and matcher
def role_cards(deals: list[dict]) -> list[dict]:
    out = []
    for d in deals:
        fam = R.OFFERING_FAMILY.get(d.get("offering") or "")
        tmpl = R.ROLE_TEMPLATES.get(fam or "")
        amount = float(d.get("amount") or 0)
        if not tmpl or amount <= 0:
            continue
        total = amount / R.DEAL_DAY_RATE
        n = next((k for limit, k in R.CARD_COUNT_STEPS if total < limit), R.CARD_COUNT_MAX)
        n = min(n, len(tmpl))
        band = d.get("fstatus") or "Pipeline"
        for i, (role, sfia, must) in enumerate(tmpl[:n]):
            out.append({"deal_id": str(d["id"]), "deal": d.get("name"), "client": d.get("company") or "", "fstatus": band,
                        "prob": float(d["prob"]) if d.get("prob") not in (None, "") else None, "amount": amount, "family": fam,
                        "rss": i + 1, "role": role, "sfia": sfia, "must": list(must), "start": d.get("start"), "end": d.get("end"),
                        "months": card_months(d.get("start"), d.get("end")), "days": round(total / n), "fte": 1.0,
                        "sold": band in R.SOLD_BANDS, "strategic": amount >= R.STRATEGIC_AMOUNT})
    return out


def score_card(c: dict, p: dict) -> dict:
    """Exact port of the page's scoreCard (same scoring as engines/matcher.py)."""
    s, why = 0, []
    fam = R.ROLE_FAMILY.get(c["role"]) or c["family"]
    if p["family"] == fam:
        s += 40; why.append("family " + p["family"])
    d = (p["sfia"] or 0) - c["sfia"]
    if d == 0:
        s += 20; why.append(f"SFIA {p['sfia']}")
    elif d == 1:
        s += 12; why.append(f"SFIA {p['sfia']} (one above)")
    elif d == -1:
        s += 8; why.append(f"SFIA {p['sfia']} (stretch)")
    fs = _free(p)
    s += 20 if fs >= .8 else 10 if fs >= .5 else 3
    why.append(f"{round(fs * 100)}% free")
    sk = {x.lower() for x in p.get("skills") or []}
    hit = [m for m in c["must"] if str(m).lower() in sk]
    if c["must"]:
        s += round(25 * len(hit) / len(c["must"])); why.append(f"skills {len(hit)}/{len(c['must'])}")
    if p["type"] == "Employee":
        s += 3
    return {"score": s, "why": "; ".join(why), "missing": [m for m in c["must"] if str(m).lower() not in sk], "level_gap": d, "free": round(fs, 2)}


def match_card(c: dict, people: list[dict]) -> dict:
    """Exact port of the page's matchCard."""
    scored = [{"res": p["id"], "name": p["name"], "type": p["type"], "grade": p["grade"], "family": p["family"],
               "roll_off": p.get("p1_end"), "contract_end": p.get("contract_end"), **score_card(c, p)}
              for p in people if not (p["type"] == "Employee" and p.get("end") and c.get("start") and p["end"] < c["start"])]
    scored.sort(key=lambda x: -x["score"])          # stable, like Array.prototype.sort
    top = scored[:4]
    best = top[0] if top else None
    no_skills = bool(best) and len(c["must"]) > 0 and len(best["missing"]) == len(c["must"])
    if not best or best["score"] < 60 or (no_skills and best["score"] < 75):
        out = "Gap"
    elif best["type"] != "Employee":
        out = "Contingent"
    elif best["level_gap"] < 0 or best["missing"]:
        out = "Stretch"
    else:
        out = "Filled"
    route = "Resource internally" if out in ("Filled", "Stretch") else "Recruit FTE" if (c["sold"] and c["strategic"] and c["months"] >= 6) else "Partner / contractor"
    return {**c, "outcome": out, "route": route, "best": best, "options": top}


def matches(deals: list[dict], people: list[dict]) -> list[dict]:
    return [match_card(c, people) for c in role_cards(deals)]


# ---------------------------------------------------------------- exceptions
def _exc(code: str, subject: str, detail: str, key: str, today: str) -> dict:
    rule, sev, owner, days = R.EXCEPTION_RULES[code]
    return {"rule": rule, "severity": sev, "owner": owner, "subject": subject, "detail": detail, "due": _add(today, days), "key": key}


def _generic_hours(generics: list[dict], months: list[str]) -> dict:
    h: dict = defaultdict(float)
    ms = set(months)
    for g in generics:
        if g["month"] in ms:
            h[g["code"]] += g.get("p3u") or 0
    return h


def exceptions(engs: list[dict], generics: list[dict], utilisation: list[dict], people: list[dict], assignments: list[dict],
               milestones: list[dict], months: list[str], today: str) -> list[dict]:
    out = []
    won = [e for e in engs if e.get("fstatus") in R.MARGIN_RULE_STATUSES and e.get("rev") and e.get("margin") is not None]
    for e in won:
        if e.get("bmargin") is not None and e["margin"] < e["bmargin"] - R.X01_POINTS:
            out.append(_exc("X01", e["code"], f"{e['client']}: margin {e['margin']:.1f}% vs baseline {e['bmargin']:.1f}%", "eng:" + e["id"], today))
    for e in won:
        if e["margin"] < R.X02_FLOOR:
            out.append(_exc("X02", e["code"], f"{e['client']}: margin {e['margin']:.1f}%", "eng:" + e["id"], today))
    by_code = {e["code"]: e for e in engs}
    # milestones
    for m in milestones:
        e = by_code.get(m.get("code"))
        if not e:
            continue
        label = m["name"] + (" " + m["description"] if m.get("description") else "")
        if m.get("forecast") and m.get("contracted") and m["forecast"] > m["contracted"] and not m.get("complete"):
            out.append(_exc("X18", e["code"], f"{label}: forecast {m['forecast']} vs contracted {m['contracted']}", "ms:" + m["id"], today))
    for m in milestones:
        e = by_code.get(m.get("code"))
        if not e or m.get("complete"):
            continue
        if m.get("rag") in ("Amber", "Red") and m.get("rag_updated") and (_d(today) - _d(m["rag_updated"])).days >= R.X04_STALE_DAYS:
            out.append(_exc("X04", e["code"], f"{m['name']}: {m['rag']}, last update {m['rag_updated'][:10]}", "ms:" + m["id"], today))
    for m in milestones:
        e = by_code.get(m.get("code"))
        if not e or m.get("complete"):
            continue
        if m.get("original_contracted") and m.get("contracted") and m["original_contracted"] != m["contracted"]:
            out.append(_exc("X26", e["code"], f"{m['name']}: {m['original_contracted']} -> {m['contracted']}", "ms:" + m["id"], today))
    # unfilled generics on live engagements, next two months
    gh = _generic_hours(generics, months[: R.X27_MONTHS])
    for e in engs:
        h = gh.get(e["code"], 0)
        if e.get("stage") == "Live" and h > R.X27_MIN_HOURS:
            out.append(_exc("X27", e["code"], f"{e['client']}: {round(h)} generic hours in the next two months", "eng:" + e["id"], today))
    # employees rolling off with nothing next
    limit = _add(today, R.X29_DAYS)
    nxt = defaultdict(list)
    for a in assignments:
        nxt[a.get("rid")].append(a)
    for p in people:
        if p["type"] != "Employee" or not p.get("p1_end") or not (today <= p["p1_end"] <= limit):
            continue
        if any(a.get("start") and a["start"] > p["p1_end"] and a.get("fstatus") in R.P1_STATUSES for a in nxt.get(p["id"], [])):
            continue
        out.append(_exc("X29", p["name"], f"{p['family']} {p['grade']}: last P1 assignment ends {p['p1_end']}", "res:" + p["id"], today))
    # team utilisation this month
    this = months[0]
    for u in utilisation:
        if u["month"] == this and u["headcount"] >= R.X03_MIN_HEADCOUNT and u["utilisation"] < R.X03_TEAM_UTILISATION:
            out.append(_exc("X03", u["family"], f"{u['headcount']} people, {round(u['utilisation'] * 100)}% P1 utilisation, {u['under']} under-allocated",
                            "team:" + u["family"], today))
    return out


# ---------------------------------------------------------------- proposals and cross-checks
def proposals(mt: list[dict], people: list[dict], excs: list[dict], today: str) -> list[dict]:
    asg = [m for m in mt if m["outcome"] == "Filled" and m.get("best")]
    per_person = Counter(m["best"]["res"] for m in asg)
    exc_by_res = defaultdict(list)
    for x in excs:
        if x["key"].startswith("res:"):
            exc_by_res[x["key"][4:]].append(x)
    out = []
    for m in asg:
        b = m["best"]; checks = []
        if per_person[b["res"]] > 1:
            checks.append({"rule": "C6 same person proposed elsewhere", "finding": f"{b['name']} is the proposed match on {per_person[b['res']]} open proposals",
                           "action": "approve one, re-run the matcher for the rest"})
        for x in exc_by_res.get(b["res"], []):
            checks.append({"rule": "C4 open exception on person", "finding": x["rule"], "action": "note"})
        out.append({"id": f"P-ASG-{m['deal_id']}-{m['rss']}", "kind": "assignment", "engine": "Matcher", "target": "Kantata (sandbox)", "domain": "Resource manager",
                    "summary": f"Assign {b['name']} to {m['deal']} as {m['role']} (SFIA {m['sfia']}) from {m['start']}",
                    "evidence": {"score": b["score"], "why": b["why"], "deal": m["deal"], "client": m["client"]},
                    "payload": {"deal_id": m["deal_id"], "rss": m["rss"], "resource_id": b["res"], "role": m["role"], "start": m["start"], "end": m["end"], "days": m["days"]},
                    "crosscheck": {"verdict": "hold" if any(c["rule"][:2] in ("C6", "C1") for c in checks) else "clear", "checks": checks}})
    soon = _add(today, R.ROLL_OFF_WEEKS * 7)
    for m in mt:
        if m["outcome"] != "Gap":
            continue
        fam = R.ROLE_FAMILY.get(m["role"]) or m["family"]
        supply = R.C1_SUPPLY_FAMILY.get(fam, fam)
        rolling = [p for p in people if p["type"] == "Employee" and p["family"] == supply and p.get("p1_end") and today <= p["p1_end"] <= soon]
        checks = []
        if rolling:
            names = ", ".join(f"{p['name']} ({p['grade']}, {p['p1_end']})" for p in rolling[:3])
            checks.append({"rule": "C1 roll-off before hire", "finding": f"{len(rolling)} {fam} employee(s) roll off within {R.ROLL_OFF_WEEKS} weeks: {names}",
                           "action": "re-run matcher with upcoming availability before approving"})
        out.append({"id": f"P-REQ-{m['deal_id']}-{m['rss']}", "kind": "requisition", "engine": "Matcher", "target": "Workable", "domain": "Talent acquisition",
                    "summary": f"Raise requisition: {m['role']} SFIA {m['sfia']} for {m['deal']} ({m['client']}), route {m['route']}",
                    "evidence": {"best_score": m["best"]["score"] if m.get("best") else None, "route": m["route"], "sold": m["sold"], "strategic": m["strategic"], "months": m["months"]},
                    "payload": {"deal_id": m["deal_id"], "rss": m["rss"], "role": m["role"], "sfia": m["sfia"], "family": fam, "start": m["start"], "months": m["months"]},
                    "crosscheck": {"verdict": "hold" if checks else "clear", "checks": checks}})
    return out


# ---------------------------------------------------------------- variance
def variance(engs: list[dict], generics: list[dict], milestones: list[dict], months: list[str]) -> list[dict]:
    moved, amber = Counter(), Counter()
    for m in milestones:
        if m.get("original_contracted") and m.get("contracted") and m["original_contracted"] != m["contracted"]:
            moved[m.get("code")] += 1
        if m.get("rag") in ("Amber", "Red") and not m.get("complete"):
            amber[m.get("code")] += 1
    gh = _generic_hours(generics, months[: R.VARIANCE_UNFILLED_MONTHS])
    out = []
    for e in engs:
        if not e.get("brev") or e.get("fstatus") not in R.MARGIN_RULE_STATUSES:
            continue
        bcost = e.get("_bcost")
        if bcost is None:
            bcost = e["brev"] * (1 - (e["bmargin"] or 0) / 100)
        rd, cd = round(e["rev"] - e["brev"]), round(e["cost"] - bcost)
        mdp = round((e["margin"] or 0) - (e["bmargin"] or 0), 1)
        if abs(rd) < R.VARIANCE_MIN_DELTA and abs(cd) < R.VARIANCE_MIN_DELTA:
            continue
        mv, un = moved[e["code"]], round(gh.get(e["code"], 0))
        if mv:
            drv = "D1" if rd < 0 else "D5"
        elif rd < 0 and un > 0:
            drv = "D3"
        elif abs(rd) >= R.VARIANCE_D2_SHARE * e["brev"]:
            drv = "D2"
        else:
            drv = "D0"
        out.append({"code": e["code"], "client": e["client"], "name": e["name"], "driver": drv, "revenue_delta": rd, "cost_delta": cd,
                    "margin_delta_pts": mdp, "baseline_revenue": e["brev"], "contract_revenue": e["rev"],
                    "evidence": {"milestones_moved": mv, "amber_red_milestones": amber[e["code"]], "unfilled_hours_next_3m": un,
                                 "baseline_margin": e["bmargin"], "margin": e["margin"]}})
    return out


# ---------------------------------------------------------------- levers
def bench(capacity: list[dict], people: list[dict], assignments: list[dict], months: list[str], thr: float) -> list[dict]:
    """The page's benchAt (same rule as build_gold.py): employees, next three months, excluded BUs out, free = available - P1."""
    m3 = set(months[:3]); f: dict = {}
    for c in capacity:
        if c["type"] == "Employee" and c["month"] in m3 and c["bu"] not in R.EXCLUDE_BU:
            x = f.setdefault(c["res"], {"res": c["res"], "name": c["name"], "family": c["family"], "bu": c["bu"], "grade": c["grade"], "free": 0.0, "avail": 0.0})
            x["free"] += max(0.0, c["available_hours"] - c["p1_hours"]); x["avail"] += c["available_hours"]
    cost = {p["id"]: p.get("std_cost") for p in people}
    p1 = {p["id"]: p.get("p1_end") for p in people}
    out = []
    for x in f.values():
        if not x["avail"] or x["free"] / x["avail"] < thr:
            continue
        days = round(x["free"] / R.HOURS_PER_DAY); c = cost.get(x["res"])
        out.append({"name": x["name"], "family": x["family"], "bu": x["bu"], "grade": x["grade"], "free_pct": round(x["free"] / x["avail"], 2),
                    "free_days": days, "unrecovered": round(days * c) if c else 0, "roll_off": p1.get(x["res"])})
    return out


def levers(people: list[dict], assignments: list[dict], capacity: list[dict], months: list[str], today: str) -> dict:
    nxt = dt.date.fromisoformat(months[-1] + "-01").replace(day=28) + dt.timedelta(days=4)
    window_end = (nxt - dt.timedelta(days=nxt.day)).isoformat()       # last day of the window's last month
    emp = [p for p in people if p["type"] == "Employee" and p["bu"] not in R.EXCLUDE_BU]
    displace = []
    for a in assignments:
        if a["type"] not in ("Supplier", "Partner") or not a.get("remaining") or not a.get("cost_rate"):
            continue
        # the most available same-family employee within one SFIA level either way; ties go to the first in the people list (inferred)
        best = None
        for p in emp:
            if p["family"] != a["family"] or not p.get("sfia") or not a.get("sfia") or not p.get("std_cost") or abs(p["sfia"] - a["sfia"]) > 1:
                continue
            if _free(p) < R.DISPLACE_MIN_FREE:
                continue
            if best is None or _free(p) > _free(best):
                best = p
        if not best:
            continue
        saving = max(0, round(a["remaining"] * (a["cost_rate"] * R.RATE_HOURS_PER_DAY - best["std_cost"])))
        displace.append({"code": a["code"], "client": a["client"], "supplier": a["name"], "type": a["type"], "grade": a["grade"], "remaining": a["remaining"],
                         "cost_rate": a["cost_rate"], "candidate": best["name"], "candidate_grade": best["grade"], "candidate_cost": best["std_cost"],
                         "free_pct": round(_free(best), 2), "saving": saving})
    displace.sort(key=lambda x: -x["saving"])
    over = [{"code": a["code"], "client": a["client"], "name": a["name"], "grade": a["grade"], "margin": a["margin"], "rev_rate": a["rev_rate"],
             "cost_rate": a["cost_rate"], "remaining": a["remaining"]}
            for a in assignments if a["type"] == "Employee" and (a.get("sfia") or 0) >= R.OVER_GRADED_SFIA and a.get("margin") is not None
            and a["margin"] < R.OVER_GRADED_MARGIN and a.get("remaining")]
    ending = [{"code": a["code"], "client": a["client"], "name": a["name"], "type": a["type"], "grade": a["grade"], "contract_end": a["contract_end"],
               "assignment_end": a["end"], "remaining": a["remaining"], "cost_rate": a["cost_rate"]}
              for a in assignments if a["type"] in ("Supplier", "Partner") and a.get("contract_end") and a["contract_end"] <= window_end and a.get("remaining")]
    return {"displace": displace, "below_rate": [], "over_graded": over, "bench": bench(capacity, people, assignments, months, R.BENCH_THRESHOLD), "ending": ending}
