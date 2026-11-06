"""Pure-Python ports of the page logic (windows, pools, weekly availability per basis, Not detailed comparison, margin pools).

Each function mirrors a JavaScript function of the same name on the signed-off pages, so the figures match the artifact.
"""
from __future__ import annotations

import datetime as dt
from collections import defaultdict

from . import config

STATUS = ["Won Closed", "Commit", "Most Likely", "Best Case"]
ND_STATUS = ["Won Closed", "Contracted", "Commit", "Most Likely", "Best Case", "Forecast Placeholder"]   # Pipeline excluded (6 Nov)
CAPS = ["CLOUD", "CON", "DATA", "MAN", "P3M", "NET", "CYBER", "OTHER"]
CAPNAME = {"CLOUD": "Cloud", "CON": "Consulting", "DATA": "Data", "MAN": "Managed service", "P3M": "P3M", "NET": "Network", "CYBER": "Cyber", "OTHER": "Other"}
TYPES = ["Employee", "Supplier", "Partner"]
BASIS_LABEL = {"p1": "P1 sold", "p2": "P2 likely (sold + Commit + Most Likely)", "p3": "P3 full forecast"}
DWINS = [("n30", "Next 30 days"), ("n90", "Next 90 days"), ("n180", "Next 6 months"), ("fy27", "FY27"), ("fy28", "FY28"), ("fwd", "All forward")]
MWINS_F = [("live", "Live now"), ("n90", "Next 90 days"), ("n180", "Next 6 months"), ("fy27", "FY27"), ("fwd", "All forward")]
MWINS_H = [("l3", "Last 3 months"), ("l6", "Last 6 months"), ("l12", "Last 12 months"), ("fyp", "Previous FY"), ("all", "All history")]


# ---- dates ------------------------------------------------------------------------------------------------------------------
def add_days(iso: str, n: int) -> str:
    return (dt.date.fromisoformat(iso) + dt.timedelta(days=n)).isoformat()


def fy(iso: str) -> int:
    y, m = int(iso[:4]), int(iso[5:7])
    return y + 1 if m >= 12 else y


def fy_range(f: int) -> tuple[str, str]:
    return f"{f - 1}-12-01", f"{f}-11-30"


def last_day(ym: str) -> str:
    y, m = int(ym[:4]), int(ym[5:7])
    nxt = dt.date(y + (m == 12), 1 if m == 12 else m + 1, 1)
    return (nxt - dt.timedelta(days=1)).isoformat()


def months_between(a: str, b: str) -> list[str]:
    out = []; y, m = int(a[:4]), int(a[5:7]); ey, em = int(b[:4]), int(b[5:7])
    while (y, m) <= (ey, em):
        out.append(f"{y:04d}-{m:02d}"); m += 1
        if m > 12:
            m = 1; y += 1
    return out


def wd(a: str, b: str) -> int:
    if b < a:
        return 0
    d, e, n = dt.date.fromisoformat(a), dt.date.fromisoformat(b), 0
    while d <= e:
        if d.weekday() < 5:
            n += 1
        d += dt.timedelta(days=1)
    return n


def win_range(key: str, basis: str, today: str) -> tuple[str, str]:
    t = today; now = fy(t)
    m = {"live": (t, t), "n30": (t, add_days(t, 30)), "n90": (t, add_days(t, 90)), "n180": (t, add_days(t, 180)), "fwd": (t, "2099-12-31"),
         "fy27": fy_range(2027), "fy28": fy_range(2028), "fyc": fy_range(now),
         "l3": (add_days(t, -91), add_days(t, -1)), "l6": (add_days(t, -182), add_days(t, -1)), "l12": (add_days(t, -365), add_days(t, -1)),
         "fyp": fy_range(now - 1), "all": ("2025-12-01", "2099-12-31")}
    a, b = m.get(key, m["n90"])
    if basis == "fwd" and a < t:
        a = t
    return a, b


# ---- Demand overview ------------------------------------------------------------------------------------------------------------
def cap_of(r: dict) -> str:
    return r["cap"] if r.get("cap") in CAPS else "OTHER"


def days_in_window(r: dict, a: str, b: str, today: str) -> float:
    tot = 0.0
    for m, v in (r.get("phase") or {}).items():
        ms, me = m + "-01", last_day(m)
        if me < a or ms > b:
            continue
        s, e = max(ms, a), min(me, b)
        if s == ms and e == me:
            tot += v; continue
        as_ = max(ms, r.get("start") or "", today); ae = min(me, r.get("end") or me)
        share = wd(max(s, as_), min(e, ae)) / max(wd(as_, ae), 1)
        tot += v * min(share, 1)
    return tot


def d_pool(res: dict, window: str, status: set[str], caps: set[str], client: str, ms_included: bool) -> list[dict]:
    today = res["today"]; a, b = win_range(window, "fwd", today)
    out = []
    for r in res["generic"]:
        if (r.get("ms") and not ms_included) or r["rem"] <= 0 or (r.get("end") or "") < today or r["st"] not in status or cap_of(r) not in caps or (client and r["acc"] != client):
            continue
        w = days_in_window(r, a, b, today)
        if w > 0.05:
            out.append({**r, "wdays": w})
    return out


# ---- Margin watch ---------------------------------------------------------------------------------------------------------------
def m_pool(res: dict, window: str, basis: str, types: set[str], client: str, eng: str, ms_included: bool) -> list[dict]:
    today = res["today"]; a, b = win_range(window, basis, today)
    out = []
    for r in res["named"]:
        if (r.get("ms") and not ms_included) or r["typ"] not in types or (client and r["acc"] != client) or (eng and r["key"] != eng) or not (r["start"] <= b and r["end"] >= a):
            continue
        if basis == "hist":
            if not r.get("arev"):
                continue
            out.append({**r, "mrev": r["arev"], "mcost": r["acost"], "mm": (r["arev"] - r["acost"]) / r["arev"] * 100})
        else:
            if r["end"] < today or r.get("m") is None:
                continue
            out.append({**r, "mrev": r["rev"], "mcost": r["cost"], "mm": r["m"]})
    return out


def trend(res: dict, types: set[str], client: str, eng: str, ms_included: bool) -> list[dict]:
    closed = res["today"][:7]
    em = [r for r in res["eng_months"] if (ms_included or not r.get("ms")) and r["rt"] in types and (not client or r["acc"] == client) and (not eng or r["key"] == eng)]
    out = []
    for m in months_between("2025-12", "2027-11"):
        rs = [r for r in em if r["m"] == m]; act = m < closed
        rv = sum((r["arev"] if act else r["rev"]) for r in rs); cs = sum((r["acost"] if act else r["cost"]) for r in rs)
        out.append(dict(m=m, act=act, rev=rv, cost=cs, mg=((rv - cs) / rv * 100) if rv else None))
    return out


# ---- Utilisation (RM page derive(): weekly availability on the chosen basis) ------------------------------------------------------
def derive(r: dict, basis: str) -> dict:
    weeks = []
    for w in r["weeks"]:
        if w.get("na"):
            weeks.append(w); continue
        dem = w.get(basis) if w.get(basis) is not None else w.get("p2", 0)
        avail = round((w["cap"] - w["absence"] - dem) / w["cap"], 3) if w["cap"] > 0 else None
        weeks.append({**w, "avail": avail, "dem": dem})
    fwd = [w for w in weeks[1:] if not w.get("na") and w.get("avail") is not None]
    mn = min((w["avail"] for w in fwd), default=None); mx = max((w["avail"] for w in fwd), default=None)
    return {**r, "weeks": weeks, "min_avail": mn, "max_avail": mx, "over": mn is not None and mn < config.OVER_THRESHOLD, "bench": mx is not None and mx >= config.BENCH_THRESHOLD}


def wk_text(w: dict) -> str:
    if w.get("na"):
        return "–"
    if w.get("full_absence"):
        return "A"
    return "–" if w.get("avail") is None else f"{round(w['avail'] * 100)}%"


# ---- Not detailed -------------------------------------------------------------------------------------------------------------
def nd_in_fy(r: dict, fy_key: str, value: float | None, start: str | None, end: str | None) -> float | None:
    if fy_key == "all":
        return value or 0
    if not start:
        return None
    d0, d1 = start, end or start
    fa, fb = fy_range(int(fy_key)); s, e = max(d0, fa), min(d1, fb)
    if e < s:
        return 0
    days = lambda a, b: max(0, (dt.date.fromisoformat(b) - dt.date.fromisoformat(a)).days + 1)
    return (value or 0) * days(s, e) / days(d0, d1)


def nd_pool(res: dict, fy_key: str, status: set[str], client: str, hs_only: bool, ms_included: bool) -> list[dict]:
    out = []
    for r in res.get("not_detailed", []):
        if r["st"] == "Pipeline" or (r.get("ms") and not ms_included) or r["st"] not in status or (client and r["acc"] != client) or (hs_only and not r.get("hs_amount")):
            continue
        d0, d1 = (r.get("hs_start"), r.get("hs_end") or r.get("hs_start")) if r.get("hs_start") else (r.get("start"), r.get("end") or r.get("start"))
        fyv = nd_in_fy(r, fy_key, r.get("hs_amount"), d0, d1)
        kfyv = nd_in_fy(r, fy_key, r.get("rev"), r.get("start"), r.get("end"))
        out.append({**r, "fyv": fyv, "kfyv": kfyv, **nd_compare(r)})
    out.sort(key=lambda x: (x.get("hs_start") or x.get("start") or "9999", -(x.get("fyv") or x.get("hs_amount") or x.get("rev") or 0)))
    return out


def nd_compare(r: dict) -> dict:
    """Gap / variance classification per cell (red = one system only, amber = disagree, plain = aligned)."""
    norm = lambda x: "".join(ch for ch in str(x or "").lower() if ch.isalpha())
    has_hs = bool(r.get("hs_amount") or r.get("hs_start")); has_k = bool(r.get("rev") or r.get("start"))
    vdiff = (r["rev"] - r["hs_amount"]) if r.get("hs_amount") and r.get("rev") else None
    vpct = vdiff / r["hs_amount"] if vdiff is not None else None
    ddiff = (dt.date.fromisoformat(r["start"]) - dt.date.fromisoformat(r["hs_start"])).days if r.get("hs_start") and r.get("start") else None
    both = ("var" if abs(vpct) > config.ND_VALUE_TOLERANCE else "") if vdiff is not None else ""
    hs_val = both or ("" if r.get("hs_amount") else "gap"); kt_val = both or ("" if r.get("rev") else "gap")
    both_d = ("var" if abs(ddiff) > config.ND_DATE_TOLERANCE_DAYS else "") if ddiff is not None else ""
    hs_date = both_d or ("" if r.get("hs_start") else "gap"); kt_date = both_d or ("" if r.get("start") else "gap")
    st_cls = "var" if r.get("hs_band") and r.get("st") and norm(r["hs_band"]) != norm(r["st"]) and not (norm(r["hs_band"]) == "closedwon" and norm(r["st"]) == "wonclosed") else ""
    issue = bool(hs_val or kt_val or hs_date or kt_date or st_cls or not has_hs or not has_k)
    return dict(has_hs=has_hs, has_k=has_k, vdiff=vdiff, vpct=vpct, ddiff=ddiff, hs_val=hs_val, kt_val=kt_val, hs_date=hs_date, kt_date=kt_date, st_cls=st_cls, issue=issue)


# ---- people list for the "someone else" picker ---------------------------------------------------------------------------------
def people_all(res: dict, match: dict) -> list[dict]:
    seen: dict[str, dict] = {}
    for o in match.get("people", []):
        seen.setdefault(o["name"].lower() + "|" + o["typ"], dict(id=o["id"], name=o["name"], typ=o["typ"], role=o.get("role"), bu=o.get("bu"), lvl=o.get("lvl"), free=o.get("free")))
    for r in res.get("named", []):
        seen.setdefault(r["res"].lower() + "|" + r["typ"], dict(id="name:" + r["res"], name=r["res"], typ=r["typ"], role=None, bu=None, lvl=None, free=None))
    return sorted(seen.values(), key=lambda o: o["name"])


def short(acc: str) -> str:
    return {"Department for Environment, Food & Rural Affairs": "Defra", "Department for Work and Pensions": "DWP", "Department for Education": "DfE",
            "Ministry of Justice": "MoJ", "National Crime Agency": "NCA", "Ministry of Housing, Communities and Local Government": "MHCLG"}.get(acc, acc or "—")
