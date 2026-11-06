"""Margin tab: Margin watch and Margin improvement actions."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import ai, config, engines, ui

PURPOSE = "A view of where named assignments earn below the target margin and the levers that would lift it."
OBJECTIVE = "Raise the margin on the engagements that fall short by re-rating, swapping to a lower-cost person of the right grade, or replacing a supplier or partner with an employee."

RULES_TEXT = [
    ("Margin watch.", "Margin = (revenue − cost) ÷ revenue on named assignments in the chosen window, where revenue is the billed day rate and cost is the employee's internal cost rate or the supplier/partner rate; the Actual / Forecast switch picks the months. The threshold defaults to 40% (the target GPM) and can be moved with the slider: red below the threshold, green at or above it. By engagement shows the top 18 engagements by revenue with their blended margin; Individuals below threshold lists people whose blended margin across their assignments is under the threshold, with their lowest single assignment."),
    ("Margin improvement actions.", "Displace suppliers and partners: live supplier or partner assignments where an employee with the same role family, within one SFIA level, has capacity left after the generic-role pass; saving = (supplier cost rate − employee cost rate) × days left, with the new margin recomputed; sorted lowest margin first. Supplier and partner contracts ending: contracts ending within 120 days while still assigned. Colour bands on this tab are fixed: 40% and above green, below 40% red."),
    ("Explain.", "Each Explain narrative is generated from that row's own figures only (rates, costs, days, dates, the threshold) and is written as a So what and a Recommended action for an executive reader."),
]


def render(data: dict) -> None:
    ui.heading("Margin", PURPOSE, OBJECTIVE)
    res, match = data["res"], data["match"]
    ms_incl = st.radio("Managed services", ["Excluded", "Included"], horizontal=True, label_visibility="collapsed", key="m_ms") == "Included"
    t1, t2 = st.tabs(["Margin watch", "Margin improvement actions"])
    with t1:
        _watch(res, data, ms_incl)
    with t2:
        _actions(match, data)
    ui.rules_panel("Rules applied · Margin", RULES_TEXT)


def _band(m: float, thr: float) -> str:
    return f"color:{config.BAD};font-weight:600" if m < thr else f"color:{config.GOOD};font-weight:600"


def _watch(res: dict, data: dict, ms_incl: bool) -> None:
    today = res["today"]
    c1, c2, c3 = st.columns([1.2, 2, 1])
    basis = c1.radio("Months", ["fwd", "hist"], format_func={"fwd": "Forecast", "hist": "Actual"}.get, horizontal=True)
    wins = engines.MWINS_F if basis == "fwd" else engines.MWINS_H
    win = c2.radio("Window", [k for k, _ in wins], index=1 if basis == "fwd" else 0, format_func=dict(wins).get, horizontal=True)
    thr = c3.slider("Margin threshold %", 10, 60, config.MARGIN_TARGET, 5)
    c4, c5, c6 = st.columns(3)
    types = set(c4.multiselect("Type", engines.TYPES, default=engines.TYPES, key="mtypes"))
    clients = sorted({r["acc"] for r in res["named"]})
    client = c5.selectbox("Client", ["All clients"] + clients, key="mclient"); client = "" if client == "All clients" else client
    engs = sorted({r["key"] for r in res["named"] if not client or r["acc"] == client})
    eng = c6.selectbox("Engagement", ["All engagements"] + engs, key="meng"); eng = "" if eng == "All engagements" else eng
    pool = engines.m_pool(res, win, basis, types, client, eng, ms_incl)
    a, b = engines.win_range(win, basis, today)
    under = [r for r in pool if r["mm"] < thr]
    rev = sum(r["mrev"] for r in pool); cost = sum(r["mcost"] for r in pool); urev = sum(r["mrev"] for r in under); ucost = sum(r["mcost"] for r in under)
    word = "forecast" if basis == "fwd" else "actual"
    ui.tiles([(f"Assignments below {thr}%", len(under), f"of {len(pool)} {word} assignments", "bad" if under else ""),
              ("People", len({r["res"] for r in under}), f"{len({r['key'] for r in under})} engagements", ""),
              ("Revenue below threshold", ui.fmt_k(urev), f"{urev / rev * 100:.0f}% of {ui.fmt_k(rev)}" if rev else "", ""),
              ("Margin on that slice", f"{(urev - ucost) / urev * 100:.1f}%" if urev else "—", f"cost {ui.fmt_k(ucost)}", "alert"),
              ("Blended margin, all in window", f"{(rev - cost) / rev * 100:.1f}%" if rev else "—", f"{ui.fmt_k(rev)} revenue, {ui.fmt_k(cost)} cost", "total")])
    # trend
    st.markdown("**Blended margin by month** · actual months to the left of today, forecast to the right; bars are revenue.")
    tr = engines.trend(res, types, client, eng, ms_incl)
    st.plotly_chart(ui.trend_chart([ui.month_label(t["m"]) for t in tr], [t["rev"] for t in tr], [t["mg"] for t in tr], [t["act"] for t in tr], thr), use_container_width=True)
    # by engagement
    st.markdown("**By engagement** · column is revenue, label is blended margin; red below the threshold.")
    eg: dict[str, dict] = {}
    for r in pool:
        e = eg.setdefault(r["key"], {"rev": 0.0, "cost": 0.0, "n": 0, "acc": r["acc"]}); e["rev"] += r["mrev"]; e["cost"] += r["mcost"]; e["n"] += 1
    top = sorted(eg.items(), key=lambda kv: -kv[1]["rev"])[:18]
    if top:
        import plotly.graph_objects as go
        mg = [((e["rev"] - e["cost"]) / e["rev"] * 100) if e["rev"] else 0 for _, e in top]
        fig = go.Figure(go.Bar(x=[k for k, _ in top], y=[e["rev"] for _, e in top], marker_color=[config.BAD if m < thr else config.NAVY for m in mg],
                               text=[f"{m:.0f}%<br>{ui.fmt_k(e['rev'])}" for m, (_, e) in zip(mg, top)], textposition="outside", hovertemplate="%{x}: £%{y:,.0f}"))
        st.plotly_chart(ui._layout(fig, 340, legend=False), use_container_width=True)
    # individuals
    pg: dict[str, dict] = {}
    for r in under:
        p = pg.setdefault(r["res"], {"res": r["res"], "typ": r["typ"], "n": 0, "rev": 0.0, "cost": 0.0, "lo": 999, "items": []})
        p["n"] += 1; p["rev"] += r["mrev"]; p["cost"] += r["mcost"]; p["lo"] = min(p["lo"], r["mm"]); p["items"].append(r)
    people = sorted(({**p, "mg": (p["rev"] - p["cost"]) / p["rev"] * 100 if p["rev"] else 0} for p in pg.values()), key=lambda p: p["mg"])
    st.markdown(f"**Individuals below threshold** · {len(people)} people with at least one {word} assignment below {thr}% in the window ({ui.fmt_date(a)} to {'end of plan' if b > '2090' else ui.fmt_date(b)}).")
    df = pd.DataFrame([{"Person": p["res"], "Type": p["typ"], "Assignments": p["n"], "Revenue": ui.fmt_k(p["rev"]), "Cost": ui.fmt_k(p["cost"]), "Margin %": round(p["mg"], 1), "Lowest %": round(p["lo"], 1),
                        "Engagements": ", ".join(sorted({x["key"] for x in p["items"]}))} for p in people])
    if len(df):
        st.dataframe(df.style.map(lambda v: _band(v, thr), subset=["Margin %", "Lowest %"]), use_container_width=True, hide_index=True, height=min(600, 38 * (len(df) + 1)))
        with st.expander("Assignments and Explain for one person", expanded=False):
            pick = st.selectbox("Person", [p["res"] for p in people], key="mpick"); p = next(x for x in people if x["res"] == pick)
            st.dataframe(pd.DataFrame([{"Engagement": x["key"], "Client": engines.short(x["acc"]), "Status": x["st"], "Role": x.get("role") or "—", "Revenue": ui.fmt_k(x["mrev"]), "Cost": ui.fmt_k(x["mcost"]),
                                        "Margin %": round(x["mm"], 1), "Dates": f"{ui.fmt_date(x['start'])} – {ui.fmt_date(x['end'])}"} for x in sorted(p["items"], key=lambda x: x["mm"])]), use_container_width=True, hide_index=True)
            # TODO(databricks): time entries (extras['time']) give the actual days booked so far per person x engagement; shown here when present
            actual = [t for t in data["extras"].get("time", []) if t.get("rid") and t.get("dg") and any(t["dg"].startswith(x["key"]) for x in p["items"])]
            facts = dict(person=p["res"], resource_type=p["typ"], target_margin_pct=thr, blended_margin_pct=round(p["mg"], 1), revenue_gbp=round(p["rev"]), cost_gbp=round(p["cost"]),
                         margin_gbp=round(p["rev"] - p["cost"]), gbp_short_of_target=round(p["rev"] * thr / 100 - (p["rev"] - p["cost"])), lowest_assignment_margin_pct=round(p["lo"], 1), basis=word + " months",
                         assignments=[dict(engagement=x["key"], client=engines.short(x["acc"]), status=x["st"], role=x.get("role"), revenue_gbp=round(x["mrev"]), cost_gbp=round(x["mcost"]), margin_pct=round(x["mm"], 1), start=x["start"], end=x["end"]) for x in sorted(p["items"], key=lambda x: x["mm"])[:6]],
                         actual_days_booked_recently=[dict(engagement=t["dg"], days=t.get("units")) for t in actual][:6])
            ai.explain_button("margin", "mx_" + p["res"], facts)
    else:
        st.info("Nobody below the threshold in this window.")


def _actions(match: dict, data: dict) -> None:
    init = match.get("initiatives")
    if not init:
        st.info("No initiatives in the matcher output yet."); return
    band = lambda m: ("—" if m is None else f"{m:.1f}%")
    ui.tiles([("Displacement saving", ui.fmt_k(init["displacement"]["total"]), f"{init['displacement']['n']} of {init['displacement']['of']} supplier/partner assignments have an employee who could take over", "total"),
              ("Contracts ending", init["contracts_ending"]["n"], "supplier/partner contracts ending within 120 days while still assigned", "")])
    # 1 · displace
    items = sorted(init["displacement"]["items"], key=lambda d: (d.get("margin") if d.get("margin") is not None else 999))
    with st.expander(f"1 · Displace suppliers and partners ({len(items)})", expanded=True):
        st.caption("Live supplier and partner assignments where an employee with the same role family, within one SFIA level, has the capacity left after the generic-role pass. Saving is the cost-rate difference over the days left. Sorted lowest margin first; 40% and above green, below 40% red.")
        df = pd.DataFrame([{"Supplier / partner": f"{d['res']} · {d['typ']} L{d.get('lvl') or '?'}", "Engagement": d["key"], "Client": engines.short(d["acc"]), "Role": d.get("role") or "—", "Days left": round(d["remu"]),
                            "Rate": f"£{d['rev_rate']:,.0f}", "Cost": f"£{d['cost_rate']:,.0f}", "Margin %": round(d["margin"], 1) if d.get("margin") is not None else None,
                            "Employee who could take over": f"{d['candidate']['name']} · L{d['candidate']['lvl']} · {d['candidate']['bu']} · £{d['candidate']['cost']:,.0f}/d · {d['candidate']['can']} d free" if d.get("candidate") else (d.get("note") or "—"),
                            "Saving": ui.fmt_k(d["saving"]) if d.get("saving") else "—", "New margin %": round(d["candidate"]["new_margin"], 1) if d.get("candidate") and d["candidate"].get("new_margin") is not None else None} for d in items])
        if len(df):
            st.dataframe(df.style.map(lambda v: _band(v, config.MARGIN_TARGET) if isinstance(v, (int, float)) and v == v else "", subset=["Margin %", "New margin %"]), use_container_width=True, hide_index=True, height=min(600, 38 * (len(df) + 1)))
            pick = st.selectbox("Explain a row", [f"{d['res']} · {d['key']}" for d in items], key="dpick"); d = items[[f"{x['res']} · {x['key']}" for x in items].index(pick)]
            actors = [a for a in data["extras"].get("actors", []) if a.get("dg", "").startswith(d["key"])]   # DeliveryGroupActor: who to agree the swap with
            facts = dict(contingent_worker=d["res"], worker_type=d["typ"], worker_level=d.get("lvl"), engagement=d["key"], client=engines.short(d["acc"]), role=d.get("role"), days_left_on_assignment=d["remu"],
                         billed_day_rate_gbp=d["rev_rate"], worker_cost_day_rate_gbp=d["cost_rate"], current_margin_pct=d.get("margin"), target_margin_pct=config.MARGIN_TARGET,
                         employee_who_could_take_over=(dict(name=d["candidate"]["name"], level=d["candidate"]["lvl"], business_unit=d["candidate"]["bu"], cost_day_rate_gbp=d["candidate"]["cost"], free_days_next_3_months=d["candidate"]["can"], margin_after_swap_pct=d["candidate"].get("new_margin")) if d.get("candidate") else None),
                         saving_over_remaining_days_gbp=d.get("saving") or 0, note=d.get("note") or "", engagement_actors=[dict(role=a.get("actor_role"), name=a.get("actor")) for a in actors][:5])
            ai.explain_button("displace", "dx_" + d["res"] + d["key"], facts)
    # 2 · contracts ending
    ce = init["contracts_ending"]["items"]
    with st.expander(f"2 · Supplier and partner contracts ending ({len(ce)})", expanded=True):
        st.caption("Within 120 days and still assigned: the natural moment to exit, replace with an employee, or renegotiate the rate.")
        st.dataframe(pd.DataFrame([{"Person": c.get("name"), "Type": c.get("typ"), "SFIA": c.get("lvl"), "Business unit": c.get("bu"), "Contract end": ui.fmt_date(c.get("contract_end")),
                                    "Engagements": ", ".join(c.get("engagements") or []), "Days still assigned": c.get("remu"), "Cost rate": f"£{c['cost_rate']:,.0f}" if c.get("cost_rate") else "—"} for c in ce]), use_container_width=True, hide_index=True)
