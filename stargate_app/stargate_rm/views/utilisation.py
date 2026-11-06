"""Utilisation tab: Over utilisation and Under utilisation reports, week by week, on the P1 / P2 / P3 basis."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import ai, config, engines, ui

PURPOSE = "A week-by-week view of every billable person over the next twelve weeks, showing who is booked beyond capacity and who has time unfilled."
OBJECTIVE = "Smooth the load on the over-utilised and find work for the under-utilised before the week arrives, reading the figures on the P1, P2 or P3 basis you choose."

RULES_TEXT = [
    ("Scope.", "Employees, suppliers and partners flagged as billable in Kantata (utilisation reporting not suppressed), excluding the Service Delivery and Client Success business units and anyone offboarded. Suppliers and partners are shown up to their contract end date and employees up to their leaving date; later weeks are greyed and never counted as capacity."),
    ("Weekly availability.", "Capacity = 5 days per week less UK bank holidays. Planned and unplanned absence, non-working days and shift rest days reduce capacity. Demand = forecast usage on client engagements at the chosen basis (P2, the default, already contains P1); non-billable allocations are never demand. Availability % = (capacity − absence − demand) ÷ capacity. Kantata splits weeks at month ends; those partial periods are merged back into Monday-start weeks. A week in which absence covers 80% or more of capacity is marked A."),
    ("Over utilisation.", "Included when availability is below 0% in any of the next 12 weeks (W1 is the current week; Prev wk is shown for trend). Red below 0%, amber 0–10%, green above 10%."),
    ("Under utilisation.", "Included when availability is 25% or more in any of the next 12 weeks. Green below 25% (fully utilised), amber 25–49%, red 50% and above. The engine view lists the same people on a monthly basis: at least 25% free over the next three months, with the unbooked cost."),
    ("Basis.", "P1 sold (contracted work only), P2 likely (P1 plus Commit and Most Likely, the RM team's default) or P3 full forecast (P2 plus Best Case and Pipeline)."),
]

BAND_OVER = {"red": "#fde2e1", "amber": "#fdf0c7", "green": "#d7f2e2", "grey": "#e6e9ee"}


def _cls(w: dict, tab: str) -> str:
    if w.get("na") or w.get("avail") is None:
        return "grey"
    v = w["avail"]
    if tab == "over":
        return "red" if v < 0 else "amber" if v <= 0.1 else "green"
    return "green" if v < 0.25 else "amber" if v < 0.5 else "red"


def _weekly_table(rows: list[dict], weeks: list[str], tab: str) -> None:
    labels = ["Prev wk"] + [f"W{i}" for i in range(1, len(weeks))]
    recs, styles = [], []
    for r in rows:
        rec = {"Business unit": r["bu"], "Resource": r["name"], "Type": r["rt"], "Role": r["role"], "SFIA": r.get("lvl"), "Contract end": ui.fmt_date(r.get("cend") or r.get("end"))}
        sty = {}
        for lab, w in zip(labels, r["weeks"]):
            rec[lab] = engines.wk_text(w); sty[lab] = BAND_OVER[_cls(w, tab)]
        v = r["min_avail"] if tab == "over" else r["max_avail"]
        rec["Worst 12 wks" if tab == "over" else "Peak 12 wks"] = ui.pct(v)
        recs.append(rec); styles.append(sty)
    df = pd.DataFrame(recs)

    def colour(_):
        out = pd.DataFrame("", index=df.index, columns=df.columns)
        for i, sty in enumerate(styles):
            for c, bg in sty.items():
                out.at[i, c] = f"background-color:{bg};text-align:center"
        return out

    st.dataframe(df.style.apply(colour, axis=None), use_container_width=True, hide_index=True, height=min(600, 38 * (len(df) + 1)))


def _week_detail(r: dict, basis: str) -> None:
    bi = {"p1": 1, "p2": 2, "p3": 3}[basis]
    recs = []
    for w in r["weeks"]:
        if w.get("na"):
            recs.append({"Week": ui.fmt_date(w["ws"]), "Capacity": "—", "Absence": "—", "Demand": "—", "Availability": "after contract end", "Booked on": ""}); continue
        eng = sorted([e for e in (w.get("eng") or []) if e[bi] > 0], key=lambda e: -e[bi])
        recs.append({"Week": ui.fmt_date(w["ws"]), "Capacity": f"{w['cap']:.1f} d", "Absence": f"{w['absence']:.1f} d", "Demand": f"{w.get('dem', 0):.1f} d", "Availability": engines.wk_text(w),
                     "Booked on": ", ".join(f"{e[0]} {e[bi]:.1f} d" for e in eng) or "nothing on client engagements"})
    st.dataframe(pd.DataFrame(recs), use_container_width=True, hide_index=True)


def _facts(r: dict, basis: str, tab: str, today: str) -> dict:
    bi = {"p1": 1, "p2": 2, "p3": 3}[basis]
    fwd = [w for w in r["weeks"][1:] if not w.get("na") and w.get("avail") is not None]
    free = round(sum(max(0, w["cap"] - w["absence"] - w.get("dem", 0)) for w in fwd))
    bookings: dict[str, float] = {}
    for w in fwd:
        for e in (w.get("eng") or []):
            if e[bi] > 0:
                bookings[e[0]] = bookings.get(e[0], 0) + e[bi]
    f = dict(person=r["name"], resource_type=r["rt"], role=r["role"], sfia_level=r.get("lvl"), business_unit=r["bu"], basis=engines.BASIS_LABEL[basis], window="next 12 weeks",
             free_days_in_window=free, weekly_availability_pct=[round((w["avail"] or 0) * 100) for w in fwd],
             booked_on_days_in_window=[dict(engagement=k, days=round(v)) for k, v in sorted(bookings.items(), key=lambda kv: -kv[1])[:6]],
             contract_end=r.get("cend"), leaving_date=r.get("end"), today=today)
    if tab == "over":
        worst = min(fwd, key=lambda w: w["avail"]) if fwd else None
        f.update(days_over_capacity_in_window=round(sum(max(0, w.get("dem", 0) + w["absence"] - w["cap"]) for w in fwd)), weeks_over_capacity=sum(1 for w in fwd if w["avail"] < 0),
                 first_week_over_from=next((w["ws"] for w in fwd if w["avail"] < 0), None), worst_availability_pct=round((worst["avail"] if worst else 0) * 100), worst_week=worst["ws"] if worst else None)
    else:
        first = next((w for w in fwd if w["avail"] >= 0.25), None); peak = max(fwd, key=lambda w: w["avail"]) if fwd else None
        f.update(weeks_at_or_above_25pct_free=sum(1 for w in fwd if w["avail"] >= 0.25), first_week_free_from=first["ws"] if first else None,
                 peak_availability_pct=round((peak["avail"] if peak else 0) * 100), peak_week=peak["ws"] if peak else None)
    return f


def render(data: dict) -> None:
    ui.heading("Utilisation", PURPOSE, OBJECTIVE)
    rm = data["rm"]
    c1, c2, c3, c4 = st.columns([1.2, 1.2, 1.2, 2])
    basis = c1.radio("Basis", ["p1", "p2", "p3"], index=1, format_func=lambda b: {"p1": "P1 sold", "p2": "P2 likely", "p3": "P3 full"}[b], horizontal=True)
    scope = c2.radio("Show", ["Exceptions only", "All billable resources"], horizontal=True)
    types = c3.multiselect("Type", rm["types"], default=rm["types"])
    bu = c4.selectbox("Business unit", ["All business units"] + rm["bus"])
    rows = [engines.derive(r, basis) for r in rm["rows"] if r["rt"] in types and (bu == "All business units" or r["bu"] == bu)]
    st.markdown(f'<div class="banner">Kantata data as of {ui.fmt_date(rm["asof"])} · window {ui.fmt_date(rm["weeks"][1])} to {ui.fmt_date(rm["weeks"][-1])} (plus the previous week) · '
                f'{rm["summary"]["resources"]} billable people in scope; {rm["summary"]["excluded_bu"]} in Service Delivery / Client Success and {rm["summary"]["non_billable"]} non-billable excluded</div>', unsafe_allow_html=True)

    t_over, t_under = st.tabs([f"Over utilisation  ({sum(1 for r in rows if r['over'])})", f"Under utilisation  ({sum(1 for r in rows if r['bench'])})"])
    for tab, holder, key in (("over", t_over, "over"), ("bench", t_under, "bench")):
        with holder:
            sel = [r for r in rows if (scope.startswith("All") or r[key])]
            sel.sort(key=lambda r: (r["bu"], r["name"]))
            title = "Over Utilisation Report" if tab == "over" else "Under Utilisation Report"
            desc = ("Resources forecast to be over utilised (availability < 0%) in the next 12 weeks" if tab == "over" else "Resources with forecast availability ≥ 25% in any week within the next 12 weeks") + f" on the {engines.BASIS_LABEL[basis]} basis."
            st.markdown(f"**{title}** · {desc} {len(sel)} people.")
            st.markdown(ui.legend_html([("Red: below 0%" if tab == "over" else "Red: 50%+ free", BAND_OVER["red"]), ("Amber: 0–10%" if tab == "over" else "Amber: 25–49% free", BAND_OVER["amber"]),
                                        ("Green: above 10%" if tab == "over" else "Green: below 25% free", BAND_OVER["green"]), ("After contract end / not counted", BAND_OVER["grey"])]), unsafe_allow_html=True)
            if tab == "bench":
                mode = st.radio("View", ["Weekly (12 weeks)", "Bench engine (next 3 months)"], horizontal=True, key="bench_mode", label_visibility="collapsed")
                if mode.startswith("Bench engine"):
                    _engine_bench(data); continue
            if not sel:
                st.info("No resources match."); continue
            _weekly_table(sel, rm["weeks"], tab)
            with st.expander("Week detail and Explain for one person", expanded=False):
                pick = st.selectbox("Person", [r["name"] for r in sel], key=f"pick_{tab}")
                r = next(x for x in sel if x["name"] == pick)
                _week_detail(r, basis)
                ai.explain_button("over" if tab == "over" else "under", f"ux_{tab}_{r['rid']}", _facts(r, basis, tab, data["today"]))
    ui.rules_panel("Rules applied · Utilisation", RULES_TEXT)


def _engine_bench(data: dict) -> None:
    """Monthly bench from the matcher's people list: at least 25% free across the availability horizon (three months)."""
    people = [p for p in data["match"].get("people", []) if p.get("avail", 0) >= config.BENCH_THRESHOLD * 100]
    people.sort(key=lambda p: (-p["avail"], -p["free"]))
    st.markdown(f"**Under Utilisation Report (engine view)** · {len(people)} people at least {int(config.BENCH_THRESHOLD * 100)}% free over the next three months; free days are capacity minus everything booked in Kantata; cost is the person's standard cost rate.")
    df = pd.DataFrame([{"Person": p["name"], "Type": p["typ"], "SFIA": p.get("lvl"), "Business unit": p.get("bu"), "Role": p.get("role"), "Free days": p["free"], "Capacity": p.get("cap"), "Free %": f"{p['avail']}%",
                        "Unbooked cost": ui.fmt_k((p.get("cost") or 0) * p["free"]), "Assigned until": ui.fmt_date(p.get("assigned_until")), "Could fill": ", ".join(f"{m['key']} {m['role']} L{m['lvl']}" for m in p.get("matches", [])[:4]) or "no live generic role matches"} for p in people])
    st.dataframe(df, use_container_width=True, hide_index=True)
