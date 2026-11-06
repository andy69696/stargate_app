"""Resource manager exception rules, run on the built data (same rules as the workspace's exception register).

  X27  Unfilled generic resources on a live engagement   Won Closed engagement that has started and still carries > 40 generic hours
                                                         in the next two months; high, 5-day SLA; cumulative revenue loss to date
  X29  Employee rolls off within 4 weeks, nothing next   last P1 assignment ends within 28 days with nothing following; medium, 7 days
  X30  Over-utilised above 110% in the next two months   P1 demand above 110% of capacity in either of the next two months; medium, 7 days
  X03  Team P1 utilisation under 60% this month          capability family (BU group) of 5+ people under 60% P1 in the current month; medium, 7 days

A finding is Detected while the rule fires in the latest build and Closed automatically once it stops (state.ledger_sync).
"""
from __future__ import annotations

import datetime as dt

from . import config

RULES = {
    "X27": ("Unfilled generic resources on a live engagement", "high", 5),
    "X29": ("Employee rolls off within 4 weeks with no next assignment", "medium", 7),
    "X30": ("Over-utilised above 110% in the next two months", "medium", 7),
    "X03": ("Team P1 utilisation under 60% this month", "medium", 7),
}


def _bdays(a: dt.date, b: dt.date) -> int:
    if b < a:
        return 0
    n, d = 0, a
    while d <= b:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


def _due(today: dt.date, sla: int) -> str:
    return (today + dt.timedelta(days=sla)).isoformat()


def run(data: dict) -> list[dict]:
    """Return today's findings: id, rule, name, subject, detail, owner, severity, due, client, plus rule-specific fields."""
    today = dt.date.fromisoformat(data["today"])
    res, rm = data["res"], data["rm"]
    out: list[dict] = []
    m0 = today.strftime("%Y-%m"); m1 = (today.replace(day=1) + dt.timedelta(days=32)).strftime("%Y-%m")

    # ---- X27: unfilled generics on live Won Closed engagements
    by_eng: dict[str, list[dict]] = {}
    for g in res.get("generic", []):
        if g.get("ms") or g.get("st") != "Won Closed" or not g.get("start") or g["start"] > today.isoformat():
            continue
        by_eng.setdefault(g["key"], []).append(g)
    for key, gens in by_eng.items():
        hours = sum((g["phase"].get(m0, 0) + g["phase"].get(m1, 0)) for g in gens) * config.HOURS_PER_DAY
        if hours <= config.X27_MIN_HOURS:
            continue
        # cumulative revenue loss to date: each placeholder's planned revenue spread over its working days, counting the days already passed with nobody named
        loss = 0.0; run_rate = 0.0
        for g in gens:
            s, e = dt.date.fromisoformat(g["start"]), dt.date.fromisoformat(g["end"]) if g.get("end") else today
            total = _bdays(s, e) or 1; per_day = (g.get("rev") or 0) / total
            loss += per_day * _bdays(s, min(today, e)); run_rate += per_day * 5
        name, sev, sla = RULES["X27"]
        out.append(dict(id=f"X27|{key}", rule="X27", name=name, subject=key, client=gens[0]["acc"], owner="Resource manager", severity=sev, due=_due(today, sla),
                        detail=f"{len(gens)} unfilled generic roles, {round(hours):,} hours in the next two months; loss to date £{round(loss):,}, slipping £{round(run_rate):,} a week",
                        loss_to_date=round(loss), weekly_run_rate=round(run_rate), hours=round(hours), roles=[g["res"] + " · " + (g["role"] or "") for g in gens]))

    # ---- X29: employee rolls off within 28 days with nothing following
    horizon = (today + dt.timedelta(days=28)).isoformat()
    for r in rm.get("rows", []):
        if r.get("rt") != "Employee" or not r.get("last_assignment"):
            continue
        if r["last_assignment"] <= horizon and r["last_assignment"] >= today.isoformat():
            name, sev, sla = RULES["X29"]
            out.append(dict(id=f"X29|{r['rid']}", rule="X29", name=name, subject=r["name"], client=", ".join(r.get("current") or []) or "No client", owner="Resource manager", severity=sev,
                            due=_due(today, sla), detail=f"Last assignment ends {r['last_assignment']} on {', '.join(r.get('current') or ['—'])}; nothing booked after", roll_off=r["last_assignment"], person=r))

    # ---- X30: P1 demand above 110% of capacity in either of the next two months (weekly rows summed per month)
    for r in rm.get("rows", []):
        for ym in (m0, m1):
            wk = [w for w in r.get("weeks", [])[1:] if not w.get("na") and (w.get("ws") or "").startswith(ym)]
            cap = sum(w.get("cap", 0) - w.get("absence", 0) for w in wk); dem = sum(w.get("p1", 0) for w in wk)
            if cap > 0 and dem / cap > 1.10:
                name, sev, sla = RULES["X30"]
                out.append(dict(id=f"X30|{r['rid']}|{ym}", rule="X30", name=name, subject=r["name"], client=", ".join(r.get("current") or []) or "No client", owner="Resource manager", severity=sev,
                                due=_due(today, sla), detail=f"P1 demand {round(dem / cap * 100)}% of capacity in {ym} ({dem:.0f} of {cap:.0f} days)", person=r, month=ym, pct=dem / cap))
                break

    # ---- X03: capability family (business unit group) of 5+ people under 60% P1 this month
    fam: dict[str, list[float]] = {}
    for r in rm.get("rows", []):
        wk = [w for w in r.get("weeks", [])[1:] if not w.get("na") and (w.get("ws") or "").startswith(m0)]
        cap = sum(w.get("cap", 0) - w.get("absence", 0) for w in wk); dem = sum(w.get("p1", 0) for w in wk)
        if cap > 0:
            fam.setdefault(r.get("grp") or r.get("bu") or "—", []).append((dem, cap))
    for f, pairs in fam.items():
        if len(pairs) < 5:
            continue
        dem = sum(p[0] for p in pairs); cap = sum(p[1] for p in pairs)
        if cap and dem / cap < 0.60:
            name, sev, sla = RULES["X03"]
            out.append(dict(id=f"X03|{f}|{m0}", rule="X03", name=name, subject=f, client="No client (team)", owner="Resource manager", severity=sev, due=_due(today, sla),
                            detail=f"{len(pairs)} people at {round(dem / cap * 100)}% P1 utilisation in {m0}", pct=dem / cap, people=len(pairs)))
    return out
