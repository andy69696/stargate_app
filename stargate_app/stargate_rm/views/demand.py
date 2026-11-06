"""Demand tab: Demand overview, Allocation options (generic demand and actions), Not detailed (HubSpot vs Kantata)."""
from __future__ import annotations

import datetime as dt

import pandas as pd
import streamlit as st

from .. import ai, config, engines, state, ui

PURPOSE = "A view of unnamed demand on engagements, who could fill it, and the engagements HubSpot says are coming that have no plan in Kantata yet."
OBJECTIVE = "Put a name against every generic role in the window, choose the right route where no employee fits, and close the gaps between HubSpot and Kantata before work starts."

RULES_TEXT = [
    ("Scope.", "Kantata engagements that are not Lost. Managed-service product groups (IoC Managed Services, SoC Managed Services, Annuities) are hidden unless switched to Included; Pipeline engagements are excluded from Not detailed. Basis is the Kantata P3 forecast unless narrowed by the status filter."),
    ("Generic roles.", "A generic role is an unnamed resource line on an engagement (for example #Cloud SFIA5 - Employee). Days = Kantata forecast hours ÷ 8, phased evenly across the line's dates and counted inside the chosen window. Started = the start date has passed with nobody named. Recruit / recruit TBC is Kantata's own recruitment flag on the line."),
    ("Capacity pass (Allocation options).", "Employees first: generic roles are taken in start-date order and matched to employees before any supplier or partner is considered. Filled = an employee with the right role family at the same level or one above; Stretch = one level below or practice-only; Contingent = a supplier or partner where no employee could fill; Gap = nobody with the capacity and profile. Availability is working days less everything booked in Kantata. Stacking: someone on a T&M engagement can still take outcome-based work, capped at 110% total load. Recruit FTE is recommended only where no employee could fill, the work is long term (120+ days or a six-month engagement) and the role is strategic on Won or Commit work. The Action choice is recorded in the app's own table; it does not change Kantata."),
    ("Not detailed.", "Engagements with no resource assignments, matched to their HubSpot deal by engagement code. Red = the figure exists in one system only; amber = the two disagree (value more than 10% apart, start dates more than 14 days apart, or Kantata status ≠ HubSpot stage); plain = aligned. Variance £ = Kantata minus HubSpot. For a single financial year, values are pro-rated by calendar day. Rows are ordered by earliest start date."),
]

ALAB = {"filled": "Filled", "stretch": "Stretch", "contingent": "Contingent", "gap": "Gap"}
ACOL = {"filled": config.NAVY, "stretch": config.PERIWINKLE, "contingent": "#b45309", "gap": config.BAD}


def render(data: dict) -> None:
    ui.heading("Demand", PURPOSE, OBJECTIVE)
    res, match = data["res"], data["match"]
    ms_incl = st.radio("Managed services", ["Excluded", "Included"], horizontal=True, label_visibility="collapsed") == "Included"
    t1, t2, t3 = st.tabs(["Demand overview", "Allocation options", "Not detailed"])
    with t1:
        _overview(res, ms_incl)
    with t2:
        _alloc(res, match, data["today"])
    with t3:
        _not_detailed(res, data, ms_incl)
    ui.rules_panel("Rules applied · Demand", RULES_TEXT)


# ---- Demand overview ------------------------------------------------------------------------------------------------------------
def _overview(res: dict, ms_incl: bool) -> None:
    today = res["today"]
    c1, c2 = st.columns([2, 1])
    win = c1.radio("Window", [k for k, _ in engines.DWINS], index=1, format_func=dict(engines.DWINS).get, horizontal=True)
    clients = sorted({r["acc"] for r in res["generic"]})
    client = c2.selectbox("Client", ["All clients"] + clients); client = "" if client == "All clients" else client
    c3, c4 = st.columns(2)
    status = set(c3.multiselect("Status", engines.STATUS, default=engines.STATUS))
    caps = set(c4.multiselect("Practice", engines.CAPS, default=engines.CAPS, format_func=engines.CAPNAME.get))
    pool = engines.d_pool(res, win, status, caps, client, ms_incl)
    a, b = engines.win_range(win, "fwd", today)
    days = sum(r["wdays"] for r in pool); won = [r for r in pool if r["st"] in ("Won Closed", "Commit")]
    started = [r for r in pool if r["start"] <= today]; soon = [r for r in pool if today < r["start"] <= engines.add_days(today, 30)]
    rev = sum(r["rev"] for r in pool)   # whole-assignment forecast revenue on the roles in the window, as on the page
    ui.tiles([("Roles", f"{len(pool):,}", f"{len({r['eng'] for r in pool})} engagements", "total"), ("Days unallocated", ui.fmt_n(days), "in window", ""),
              ("On sold work", ui.fmt_n(sum(r["wdays"] for r in won)) + " d", f"{len(won)} roles, Won or Commit", ""), ("Revenue on them", ui.fmt_k(rev), "assignment forecast, where rated", ""),
              ("Already started", f"{len(started)}", f"{ui.fmt_n(sum(r['wdays'] for r in started))} days with nobody named", "bad" if started else ""),
              ("Start ≤ 30 days", f"{len(soon)}", f"{ui.fmt_n(sum(r['wdays'] for r in soon))} days; {sum(1 for r in pool if r.get('recr') == 'Yes')} flagged for recruitment", "alert" if soon else "")])
    # unallocated days by month, stacked by status
    allg = [r for r in res["generic"] if (ms_incl or not r.get("ms")) and r["rem"] > 0 and r["end"] >= today and r["st"] in status and engines.cap_of(r) in caps and (not client or r["acc"] == client)]
    months = engines.months_between(today[:7], engines.add_days(today, 540)[:7])
    st.markdown("**Unallocated days by month** · role days phased evenly over each assignment's working days, stacked by engagement status.")
    series = {s: [sum(r["phase"].get(m, 0) for r in allg if r["st"] == s) for m in months] for s in engines.STATUS if any(r["st"] == s for r in allg)}
    st.plotly_chart(ui.column_chart([ui.month_label(m) for m in months], series, ui.status_colours()), use_container_width=True)
    # by engagement, stacked by status
    st.markdown("**By engagement** · days in window, stacked by status.")
    eg: dict[str, dict] = {}
    for r in pool:
        e = eg.setdefault(r["key"], {"total": 0.0, "sts": {}}); e["total"] += r["wdays"]; e["sts"][r["st"]] = e["sts"].get(r["st"], 0) + r["wdays"]
    top = sorted(eg.items(), key=lambda kv: -kv[1]["total"])[:24]
    series = {s: [e["sts"].get(s, 0) for _, e in top] for s in engines.STATUS if any(e["sts"].get(s) for _, e in top)}
    st.plotly_chart(ui.column_chart([k for k, _ in top], series, ui.status_colours()), use_container_width=True)
    # by capability, stacked by SFIA grade
    st.markdown("**By capability** · days in window by practice, stacked by SFIA grade.")
    cg: dict[str, dict] = {}
    for r in pool:
        c = cg.setdefault(engines.CAPNAME[engines.cap_of(r)], {"total": 0.0, "lvls": {}, "n": 0}); c["total"] += r["wdays"]; c["n"] += 1; c["lvls"][r["lvl"]] = c["lvls"].get(r["lvl"], 0) + r["wdays"]
    top = sorted(cg.items(), key=lambda kv: -kv[1]["total"])
    lvls = sorted({l for _, c in top for l in c["lvls"]})
    series = {f"SFIA {l}": [c["lvls"].get(l, 0) for _, c in top] for l in lvls}
    st.plotly_chart(ui.column_chart([k for k, _ in top], series, {f"SFIA {l}": config.SFIA_COLOURS.get(l, config.TEAL) for l in lvls}), use_container_width=True)


# ---- Allocation options ------------------------------------------------------------------------------------------------------------
def _alloc(res: dict, match: dict, today: str) -> None:
    if not match.get("placeholders"):
        st.info("The capacity pass has not run (no matcher output)."); return
    sm = match["summary"]; months = sm["months"]; horizon_end = months[-1] + "-31"
    c1, c2, c3 = st.columns(3)
    statuses = set(c1.multiselect("Outcome", list(ALAB), default=list(ALAB), format_func=ALAB.get))
    caps = set(c2.multiselect("Practice", engines.CAPS, default=engines.CAPS, format_func=engines.CAPNAME.get, key="acaps"))
    clients = sorted({r["acc"] for r in match["placeholders"]})
    client = c3.selectbox("Client", ["All clients"] + clients, key="aclient"); client = "" if client == "All clients" else client
    prov = st.checkbox("Include starts after the availability horizon", value=False)
    pool = [r for r in match["placeholders"] if r["rem"] > 0 and r["alloc_status"] in statuses and (r["cap"] if r["cap"] in engines.CAPS else "OTHER") in caps and (not client or r["acc"] == client) and (prov or r["start"] <= horizon_end)]
    st.markdown(f'<div class="banner">Availability horizon {ui.month_label(months[0])} to {ui.month_label(months[-1])}. {sm["people"]} named delivery people profiled (Service Delivery and Client Success excluded); {len(match["placeholders"])} live generic roles.'
                + (f' Managed-services engagements are out of scope: {sm["excluded_ms_n"]} roles, {ui.fmt_n(sm["excluded_ms_days"])} days.' if sm.get("excluded_ms_n") else "") + "</div>", unsafe_allow_html=True)
    cnt = lambda k: [r for r in pool if r["alloc_status"] == k]
    days = lambda rs: sum(r["rem"] for r in rs)
    ui.tiles([("Filled by employee", len(cnt("filled")), f"{ui.fmt_n(days(cnt('filled')))} days · right role, same or one level up", "total"),
              ("Employee stretch", len(cnt("stretch")), f"{ui.fmt_n(days(cnt('stretch')))} days · level below or practice only", ""),
              ("Contingent", len(cnt("contingent")), f"{ui.fmt_n(days(cnt('contingent')))} days · supplier or partner, no employee could fill", "alert" if cnt("contingent") else ""),
              ("Gap", len(cnt("gap")), f"{ui.fmt_n(days(cnt('gap')))} days · nobody with capacity and profile", "bad" if cnt("gap") else "")])

    # ---- Generic demand and actions
    acts = state.actions_load()
    show_ign = st.checkbox(f"Show ignored ({sum(1 for r in pool if acts.get(r['id'], {}).get('ignored'))})", value=False)
    vis = [r for r in pool if show_ign or not acts.get(r["id"], {}).get("ignored")]
    vis.sort(key=lambda r: (r["start"], -r["rem"]))

    def default_action(r):
        a = r.get("alloc")
        if a and a["typ"] == "Employee":
            return "Internal: " + a["name"]
        if r.get("recommend") == "recruit FTE":
            return "Recruit FTE"
        return ("Partner" if a["typ"] == "Partner" else "Contractor") if a else "Recruit FTC"

    def current_action(r):
        x = acts.get(r["id"])
        if not x:
            return default_action(r)
        if x.get("ignored"):
            return "Ignore"
        return x.get("action") + (f": {x['person_name']}" if x.get("person_name") else "")

    rows = []
    for r in vis:
        a = r.get("alloc"); alts = [o for o in (r["primary"] + r["secondary"]) if not a or o["id"] != a["id"]][:3]
        rows.append({"Engagement": r["key"], "Client": engines.short(r["acc"]), "Role": f"{r.get('role') or r['famlabel']} L{r['lvl']}", "Start": ui.fmt_date(r["start"]), "Days": round(r["rem"]),
                     "Outcome": ALAB.get(r["alloc_status"], r["alloc_status"]), "Proposed": f"{a['name']} · {a['typ']} L{a['lvl']} · {round(a.get('covered') or 0)}/{round(r['need'])} d" if a else "—",
                     "Alternatives": "; ".join(f"{o['name']} {o['avail']}%" for o in alts) or "none", "Action": current_action(r),
                     "Flags": " ".join(f for f in ["started" if r["start"] <= today else "", "recruit" if r.get("recr") == "Yes" else "", "stacked" if a and a.get("stacked") else ""] if f)})
    st.markdown(f"**Generic demand and actions** · {len(vis)} generic roles. Proposed name is the capacity-pass pick; alternatives are the next best options.")
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True, height=min(640, 38 * (len(rows) + 1)))

    # ---- act on one role (Streamlit equivalent of the per-row dropdown)
    with st.expander("Choose the action for a generic role", expanded=False):
        if not vis:
            st.write("Nothing to act on."); return
        label = lambda r: f"{r['key']} · {r.get('role') or r['famlabel']} L{r['lvl']} · {ui.fmt_date(r['start'])}"
        pick = st.selectbox("Generic role", [label(r) for r in vis]); r = vis[[label(x) for x in vis].index(pick)]
        a = r.get("alloc"); internal = [o for o in [a] + r["primary"] + r["secondary"] if o and o["typ"] == "Employee"]
        seen = set(); internal = [o for o in internal if not (o["id"] in seen or seen.add(o["id"]))]
        options = [("internal|" + o["id"], ("Internal: " if a and o["id"] == a["id"] else "Internal alt: ") + o["name"]) for o in internal] + \
                  [("other", "Someone else…"), ("Recruit FTE", "Recruit FTE"), ("Recruit FTC", "Recruit FTC"), ("Partner", "Partner"), ("Contractor", "Contractor"), ("ignore", "Ignore")]
        choice = st.selectbox("Action", [v for v, _ in options], format_func=dict(options).get, key="act_" + r["id"])
        person = None
        if choice == "other":
            people = engines.people_all(res, match)
            name = st.selectbox("Person (every resource known to the page: employees, partners and suppliers, with or without free days)",
                                [f"{o['name']} · {'Internal' if o['typ'] == 'Employee' else o['typ']}" + (f" · {o['role']}" if o.get("role") else "") + (f" · {o['free']} free days" if o.get("free") is not None else "") for o in people])
            person = people[[f"{o['name']} · {'Internal' if o['typ'] == 'Employee' else o['typ']}" + (f" · {o['role']}" if o.get("role") else "") + (f" · {o['free']} free days" if o.get("free") is not None else "") for o in people].index(name)]
        if st.button("Save action", key="save_" + r["id"]):
            if choice == "ignore":
                state.action_save(r["id"], "ignore", None, None, True)
            elif choice.startswith("internal|"):
                o = next(x for x in internal if x["id"] == choice[9:]); state.action_save(r["id"], "Internal", o["id"], o["name"], False)
            elif choice == "other" and person:
                state.action_save(r["id"], "Internal" if person["typ"] == "Employee" else person["typ"], person["id"], person["name"], False)
            else:
                state.action_save(r["id"], choice, None, None, False)
            st.rerun()
        if r.get("recommend"):
            st.caption(f"Capacity pass: {r['recommend']}" + (f" · {r['recommend_why']}" if r.get("recommend_why") else ""))


# ---- Not detailed ------------------------------------------------------------------------------------------------------------------
def _not_detailed(res: dict, data: dict, ms_incl: bool) -> None:
    today = res["today"]
    fys = [("all", "All years"), ("2027", "FY27"), ("2028", "FY28"), ("2029", "FY29"), ("2030", "FY30")]
    c1, c2 = st.columns([2, 1])
    fy_key = c1.radio("Financial year", [k for k, _ in fys], format_func=dict(fys).get, horizontal=True)
    clients = sorted({r["acc"] for r in res.get("not_detailed", [])})
    client = c2.selectbox("Client", ["All clients"] + clients, key="ndclient"); client = "" if client == "All clients" else client
    c3, c4 = st.columns([3, 1])
    status = set(c3.multiselect("Status", engines.ND_STATUS, default=engines.ND_STATUS, key="ndstatus"))
    hs_only = c4.checkbox("HubSpot-matched only", value=False)
    pool = engines.nd_pool(res, fy_key, status, client, hs_only, ms_incl)
    fy_label = "" if fy_key == "all" else f" in FY{fy_key[2:]}"
    hsv = sum(r["fyv"] or 0 for r in pool); krev = sum(r["rev"] or 0 for r in pool); empty = [r for r in pool if not r["rev"] and not r["start"]]
    soon = [r for r in pool if r.get("hs_start") and r["hs_start"] <= engines.add_days(today, 90)]
    likely = [r for r in pool if r["st"] in ("Won Closed", "Contracted", "Commit", "Most Likely")]
    ui.tiles([("Engagements not detailed", len(pool), f"{len({r['acc'] for r in pool})} clients", "total"),
              ("HubSpot value" + fy_label, ui.fmt_k(hsv), f"{sum(1 for r in pool if r.get('hs_amount'))} have a matching HubSpot deal", ""),
              ("Kantata contract revenue", ui.fmt_k(krev), f"{len(empty)} have no revenue and no dates in Kantata", ""),
              ("Due to start ≤ 90 days", len(soon), f"{ui.fmt_k(sum(r['fyv'] or 0 for r in soon))} HubSpot value{fy_label}", "bad" if soon else ""),
              ("Most Likely or better", len(likely), f"{ui.fmt_k(sum(r['fyv'] or 0 for r in likely))} HubSpot value{fy_label} · Won, Contracted, Commit, Most Likely", "alert")])
    # charts
    st.markdown("**Client engagements not at detailed forecast** · HubSpot value by client, stacked by Kantata status.")
    cg: dict[str, dict] = {}
    for r in pool:
        c = cg.setdefault(engines.short(r["acc"]), {"total": 0.0, "sts": {}, "n": 0}); c["total"] += r["fyv"] or 0; c["n"] += 1; c["sts"][r["st"]] = c["sts"].get(r["st"], 0) + (r["fyv"] or 0)
    top = [kv for kv in sorted(cg.items(), key=lambda kv: -kv[1]["total"]) if kv[1]["total"] > 0][:18]
    series = {s: [c["sts"].get(s, 0) for _, c in top] for s in engines.ND_STATUS if any(c["sts"].get(s) for _, c in top)}
    if top:
        st.plotly_chart(ui.column_chart([k for k, _ in top], series, ui.status_colours(), totals_fmt=ui.fmt_k), use_container_width=True)
    st.markdown("**By client engagement start month** · when the work is due to begin according to HubSpot.")
    months = engines.months_between(today[:7], engines.add_days(today, 540)[:7])
    series = {s: [sum((r["fyv"] or 0) for r in pool if r["st"] == s and (r.get("hs_start") or r.get("start") or "")[:7] == m) for m in months] for s in engines.ND_STATUS}
    series = {s: v for s, v in series.items() if any(v)}
    if series:
        st.plotly_chart(ui.column_chart([ui.month_label(m) for m in months], series, ui.status_colours(), totals_fmt=ui.fmt_k), use_container_width=True)

    # comparison table
    st.markdown("**Engagements not yet detailed** · HubSpot on the left, Kantata on the right. " + ui.legend_html([("Gap: in one system only", "#f8d7d3"), ("Variance: value >10%, start >14 days, or stage ≠ status", "#fde8c4")]) + " Uncoloured = aligned. Sorted by earliest start date.", unsafe_allow_html=True)
    po = {r["dg"]: r for r in data["extras"].get("po", [])}   # purchase-order cover, the third system check (new lake object)
    recs, styles = [], []
    for r in pool:
        d2 = lambda d: ui.fmt_date(d)
        vt = "—" if r["vdiff"] is None else f"{'−' if r['vdiff'] < 0 else '+'}{ui.fmt_k(abs(r['vdiff']))[1:] if ui.fmt_k(abs(r['vdiff'])).startswith('£') else ui.fmt_k(abs(r['vdiff']))} ({r['vpct'] * 100:+.0f}%)"
        rec = {"Engagement": r["key"], "Client": engines.short(r["acc"]), "HS stage": r.get("hs_band") or ("—" if r["has_hs"] else "no HubSpot deal"), "HS value £": ui.fmt_k(r.get("hs_amount")) if r.get("hs_amount") else "—"}
        if fy_key != "all":
            rec[f"HS £ FY{fy_key[2:]}"] = ui.fmt_k(r["fyv"]) if r["fyv"] else "—"
        rec.update({"HS dates": f"{d2(r['hs_start'])} – {d2(r['hs_end'])}" if r.get("hs_start") else "—", "KT status": r["st"], "KT value £": ui.fmt_k(r["rev"]) if r["rev"] else "—"})
        if fy_key != "all":
            rec[f"KT £ FY{fy_key[2:]}"] = ui.fmt_k(r["kfyv"]) if r["kfyv"] else "—"
        rec.update({"KT dates": f"{d2(r['start'])} – {d2(r['end'])}" if r.get("start") else "—", "Product group": r.get("pg") or "—",
                    "PO value £": ui.fmt_k(po[r["eng"]]["po_value"]) if r["eng"] in po else "—",   # TODO(databricks): purchase orders join on engagement name
                    "Variance £": vt, "Flags": " ".join(f for f in ["starts ≤ 90d" if r.get("hs_start") and r["hs_start"] <= engines.add_days(today, 90) else "", "managed service" if r.get("ms") else ""] if f)})
        recs.append(rec)
        styles.append({"HS stage": "" if r["has_hs"] else "gap", "HS value £": r["hs_val"], "HS dates": r["hs_date"], "KT status": r["st_cls"], "KT value £": r["kt_val"], "KT dates": r["kt_date"],
                       **({f"HS £ FY{fy_key[2:]}": r["hs_val"], f"KT £ FY{fy_key[2:]}": r["kt_val"]} if fy_key != "all" else {})})
    df = pd.DataFrame(recs)
    if len(df):
        def colour(_):
            out = pd.DataFrame("", index=df.index, columns=df.columns)
            for i, sty in enumerate(styles):
                for c, cls in sty.items():
                    if cls == "gap":
                        out.at[i, c] = "background-color:#f8d7d3"
                    elif cls == "var":
                        out.at[i, c] = "background-color:#fde8c4"
            return out
        st.dataframe(df.style.apply(colour, axis=None), use_container_width=True, hide_index=True, height=min(640, 38 * (len(df) + 1)))
        with st.expander("Explain a gap or mismatch", expanded=False):
            issues = [r for r in pool if r["issue"]]
            if not issues:
                st.write("Every row is aligned.")
            else:
                pick = st.selectbox("Engagement", [r["key"] for r in issues]); r = next(x for x in issues if x["key"] == pick)
                ai.explain_button("nd", "nd_" + r["key"], _nd_facts(r, today))
    else:
        st.info("Nothing in this view with the current filters.")


def _nd_facts(r: dict, today: str) -> dict:
    issues = []
    if not r.get("hs_amount"): issues.append("no HubSpot deal value")
    if not r.get("rev"): issues.append("no Kantata contract value")
    if not r.get("start"): issues.append("no Kantata dates")
    if r["vdiff"] is not None and abs(r["vpct"]) > config.ND_VALUE_TOLERANCE:
        issues.append(f"value variance: Kantata is £{abs(r['vdiff']):,.0f} {'higher' if r['vdiff'] > 0 else 'lower'} than HubSpot ({r['vpct'] * 100:+.0f}%)")
    if r["ddiff"] is not None and abs(r["ddiff"]) > config.ND_DATE_TOLERANCE_DAYS:
        issues.append(f"start variance: Kantata starts {abs(r['ddiff'])} days {'later' if r['ddiff'] > 0 else 'earlier'} than HubSpot")
    if r["st_cls"]:
        issues.append(f"stage mismatch: HubSpot says {r.get('hs_band')}, Kantata says {r['st']}")
    issues.append("no resource assignments in Kantata (not detailed)")
    return dict(engagement=r["key"], client=r["acc"], issues_largest_first=issues,
                hubspot=dict(stage=r.get("hs_band") or "no deal", value_gbp=r.get("hs_amount") or 0, start=r.get("hs_start"), end=r.get("hs_end")),
                kantata=dict(status=r["st"], product_group=r.get("pg"), contract_value_gbp=r.get("rev") or 0, start=r.get("start"), end=r.get("end"), has_assignments=False), today=today)
