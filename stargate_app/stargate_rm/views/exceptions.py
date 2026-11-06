"""Resourcing exceptions tab."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import ai, exceptions, state, ui

PURPOSE = "One list of everything the rules have caught that needs a Resource manager's attention, from a live engagement still carrying unnamed roles to a person rolling off with nothing next."
OBJECTIVE = "Clear each finding before its due date by fixing the underlying data or the plan; a finding closes itself once the rule stops firing in the next build."

RULES_TEXT = [
    ("How a finding lives.", "Every rule is run against the latest build. A finding is Detected while the rule still fires and is Closed automatically, dated to the build in which it stopped firing, once the underlying data has changed. Nothing is closed by hand. Each finding carries an owner, a severity and a due date (detected date plus the rule's SLA)."),
    ("Resource manager rules.", "Unfilled generic resources on a live engagement: a Won Closed engagement that has started and still carries more than 40 generic (unnamed) placeholder hours in the next two months; high, 5 days; the row also shows the cumulative revenue loss to date and the weekly run-rate still slipping. Employee rolls off within 4 weeks with no next assignment: last P1 assignment ends within 28 days with nothing following; medium, 7 days. Over-utilised above 110% in the next two months: P1 demand above 110% of capacity in either of the next two months; medium, 7 days. Team P1 utilisation under 60% this month: a capability family of five or more people under 60% P1 in the current month; medium, 7 days."),
    ("Basis.", "Kantata bronze tables in the lake, refreshed by the daily build; P1 = sold, P2 = likely, P3 = full forecast; 8-hour working day; FY27 = December 2026 to November 2027. Explain narratives are generated from the finding's own rows only and name engagements, never codes or rule numbers."),
]


def render(data: dict) -> None:
    ui.heading("Resourcing exceptions", PURPOSE, OBJECTIVE)
    found = exceptions.run(data)
    led = state.ledger_sync(found, data["today"])
    rows = []
    for k, r in led.items():
        rows.append({**r, "state": "Closed" if r.get("closed_build") else "Detected", "first": r.get("first_build"), "closed": r.get("closed_build")})
    live = {x["id"]: x for x in found}
    for r in rows:
        r.update({k: v for k, v in live.get(r["id"], {}).items() if k not in ("id",)})
    n_det = sum(1 for r in rows if r["state"] == "Detected"); n_cls = len(rows) - n_det
    ui.tiles([("Detected", n_det, "open findings", "bad" if n_det else ""), ("Closed", n_cls, "no longer firing", "")])

    c1, c2, c3 = st.columns([1, 2, 2])
    sf = c1.radio("Show", ["Open", "Closed", "All"], horizontal=True, label_visibility="collapsed")
    rule_names = {k: v[0] for k, v in exceptions.RULES.items()}
    rf = c2.selectbox("Rule", ["All rules"] + [rule_names[k] for k in exceptions.RULES], label_visibility="collapsed")
    clients = sorted({r.get("client") or "—" for r in rows})
    cf = c3.selectbox("Client", ["All clients"] + clients, label_visibility="collapsed")
    shown = [r for r in rows if (sf == "All" or (sf == "Open") == (r["state"] == "Detected")) and (rf == "All rules" or rule_names.get(r["rule"]) == rf) and (cf == "All clients" or r.get("client") == cf)]
    shown.sort(key=lambda r: (r["state"] != "Detected", r.get("due") or ""))
    x27 = rf == rule_names["X27"]
    table = pd.DataFrame([{
        "Rule": rule_names.get(r["rule"], r["rule"]), "Subject": r.get("subject"), "Client": r.get("client"), "Detail": r.get("detail"),
        **({"Loss to date": ui.fmt_k(r.get("loss_to_date")), "Slipping per week": ui.fmt_k(r.get("weekly_run_rate"))} if x27 else {}),
        "Owner": r.get("owner"), "Severity": r.get("severity"), "Due": ui.fmt_date(r.get("due")), "Status": r["state"],
        "Detected": ui.fmt_date(r.get("first")), "Closed": ui.fmt_date(r.get("closed")) if r.get("closed") else "",
    } for r in shown])
    st.dataframe(table, use_container_width=True, hide_index=True)
    if x27 and shown:
        st.markdown(f'<div class="banner">Total loss to date across the findings shown: {ui.fmt_k(sum(r.get("loss_to_date") or 0 for r in shown))}, slipping {ui.fmt_k(sum(r.get("weekly_run_rate") or 0 for r in shown))} a week. '
                    'Loss to date is an estimate: each placeholder\'s planned revenue spread over its working days, counting the days already passed with nobody named.</div>', unsafe_allow_html=True)

    # ---- Explain one finding
    if shown:
        st.markdown("**Explain a finding**")
        pick = st.selectbox("Finding", [f"{rule_names.get(r['rule'], r['rule'])} · {r.get('subject')}" for r in shown], label_visibility="collapsed")
        r = shown[[f"{rule_names.get(x['rule'], x['rule'])} · {x.get('subject')}" for x in shown].index(pick)]
        facts = {k: v for k, v in r.items() if k in ("rule", "name", "subject", "client", "detail", "severity", "due", "state", "loss_to_date", "weekly_run_rate", "hours", "roles", "roll_off", "month", "pct", "people")}
        facts["rule"] = rule_names.get(r["rule"], r["rule"]); facts["today"] = data["today"]
        if r.get("person"):
            p = r["person"]; facts["person"] = dict(role=p.get("role"), sfia=p.get("lvl"), business_unit=p.get("bu"), current=p.get("current"), last_assignment=p.get("last_assignment"))
        ai.explain_button("exception", "exc_" + r["id"], facts)
    ui.rules_panel("Rules applied · Resourcing exceptions", RULES_TEXT)
