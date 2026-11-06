"""Explain: executive-level So what / Recommended action narratives from a Databricks model serving endpoint.

The prompts are the ones signed off in the workspace (two labelled lines, hard word limits, no ids or field names).
Facts are built from the row only — the model never sees anything the page does not show.
"""
from __future__ import annotations

import json

import streamlit as st

from . import config

_HEAD = "You are briefing an executive at UBDS Digital (UK IT consultancy, GBP). Using ONLY the facts below, write exactly two labelled lines and nothing else, no preamble. "
_TAIL = " Short plain sentences, no lists, no field names or ids, no hedging."

PROMPTS = {
    "exception": _HEAD + "The subject is one resourcing exception caught by a rule. Line 1 starts 'So what: ' and is at most 35 words: lead with the business consequence (revenue, margin, cost, client delivery or people risk in pounds, days or dates from the facts), who carries it and the likely cause in a few words. Line 2 starts 'Recommended action: ' and is at most 40 words: what the Resource manager does, with whom and by the due date." + _TAIL,
    "under": _HEAD + "The subject is one person who is under-utilised: forecast availability of 25% or more in at least one of the next 12 weeks. Line 1 starts 'So what: ' and is at most 35 words: lead with the free days in the window and when the gap starts, what the person is booked on and how much, and the cause in a few words. Line 2 starts 'Recommended action: ' and is at most 40 words: match them to unfilled demand in their role family and level, extend or top up a booking, or plan a bench task, with who to raise it with and by when relative to the first free week." + _TAIL,
    "over": _HEAD + "The subject is one person who is over-utilised: booked work plus absence exceeds capacity in at least one of the next 12 weeks. Line 1 starts 'So what: ' and is at most 35 words: lead with the days over capacity and when it starts, the engagements that clash and the worst week, and the cause in a few words. Line 2 starts 'Recommended action: ' and is at most 40 words: which engagement to move days from or re-phase, who could pick up the overflow, who to agree it with and by when." + _TAIL,
    "nd": _HEAD + "The subject is one data gap between HubSpot (the deal) and Kantata (the engagement and its resource forecast); 'not detailed' means the Kantata engagement has no resource assignments, so nobody can be scheduled. Line 1 starts 'So what: ' and is at most 35 words: lead with the most material item in issues_largest_first, always naming a value variance in pounds and percent or a start variance in days when one is listed, and its consequence. Line 2 starts 'Recommended action: ' and is at most 40 words: one instruction for the Resource manager that resolves it (which system to correct and to what, then the assignments to build), who to confirm with and by when." + _TAIL,
    "margin": _HEAD + "The subject is one person whose assignments earn below the target gross margin (margin = (revenue - cost) / revenue). Line 1 starts 'So what: ' and is at most 35 words: lead with the pounds short of target and the gap in points, name the engagement that drives it, then the likely cause. Line 2 starts 'Recommended action: ' and is at most 40 words: one action to raise the margin on that engagement (re-rate at the next change request, swap to a lower-cost person of the right grade, renegotiate the supplier rate, reduce the allocation), who owns it and by when." + _TAIL,
    "displace": _HEAD + "The subject is one assignment where a supplier or partner does work an employee could take over (employee-first policy). Line 1 starts 'So what: ' and is at most 35 words: lead with the saving in pounds over the remaining days and the margin move in points, name the engagement and the employee, and the cause in a few words; if no employee is available say so. Line 2 starts 'Recommended action: ' and is at most 40 words: who to swap in, the handover date relative to days left, who to agree it with and the notice to give the supplier." + _TAIL,
}


def explain(kind: str, facts: dict) -> str:
    """Return the two-line narrative (or an explanatory error string)."""
    prompt = PROMPTS[kind] + "\n\nFACTS: " + json.dumps(facts, default=str)
    if config.SAMPLE_MODE:
        return "So what: (sample mode) the model is not called; this is where the executive summary of the row appears.\nRecommended action: (sample mode) the one concrete instruction for the Resource manager appears here."
    try:
        from databricks.sdk import WorkspaceClient
        from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

        w = WorkspaceClient()
        r = w.serving_endpoints.query(name=config.LLM_ENDPOINT, messages=[ChatMessage(role=ChatMessageRole.USER, content=prompt)], max_tokens=300, temperature=0.2)
        return (r.choices[0].message.content or "").strip()
    except Exception as e:  # noqa: BLE001
        return f"Explain is not available: {str(e).splitlines()[0][:200]}  (check LLM_ENDPOINT in app.yaml and the app's CAN_QUERY grant)"


def render(text: str) -> str:
    """So what in bold red, Recommended action in bold green, as on the workspace."""
    import html
    t = html.escape(text)
    t = t.replace("So what:", f'<b style="color:{config.BAD}">So what:</b>').replace("Recommended action:", f'<b style="color:{config.GOOD}">Recommended action:</b>')
    return f'<div class="ndx">{t.replace(chr(10), "<br>")}</div>'


def explain_button(kind: str, key: str, facts: dict, label: str = "Explain") -> None:
    """A per-row Explain control: button, then the narrative in place (kept in session state so it survives reruns)."""
    store = st.session_state.setdefault("explain", {})
    if key in store:
        st.markdown(render(store[key]), unsafe_allow_html=True)
        if st.button("Close", key="close_" + key):
            del store[key]; st.rerun()
    elif st.button(label, key="btn_" + key):
        with st.spinner("Thinking…"):
            store[key] = explain(kind, facts)
        st.rerun()
