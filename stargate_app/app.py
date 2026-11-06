"""Stargate Workspace — Resource manager app (Streamlit, Databricks Apps).

The Resource manager's four tabs from the Stargate Workspace artifact, built from the Kantata bronze tables in
Unity Catalog: Resourcing exceptions · Utilisation · Demand · Margin.  See README.md for deployment and data notes.
"""
from __future__ import annotations

import streamlit as st

from stargate_rm import config, pipeline, ui
from stargate_rm.views import demand, exceptions, margin, utilisation

st.set_page_config(page_title="Stargate · Resource manager", page_icon="📊", layout="wide", initial_sidebar_state="collapsed")
ui.inject_css()
ui.banner("Resource manager")

with st.sidebar:
    st.markdown("**Data**")
    st.caption(f"Kantata: `{config.KANTATA_CATALOG}.{config.KANTATA_SCHEMA}`" + ("  \n**Sample mode** (bundled JSON)" if config.SAMPLE_MODE else ""))
    if st.button("Rebuild from the lake"):
        st.cache_data.clear(); st.rerun()

data = pipeline.build()
ui.gaps_banner(data.get("gaps", []))
st.markdown(f'<div class="banner">Built {ui.fmt_date(data["today"])} · Kantata as of {ui.fmt_date(data["res"].get("asof"))}</div>', unsafe_allow_html=True)

tab_exc, tab_util, tab_dem, tab_mar = st.tabs(["Resourcing exceptions", "Utilisation", "Demand", "Margin"])
with tab_exc:
    exceptions.render(data)
with tab_util:
    utilisation.render(data)
with tab_dem:
    demand.render(data)
with tab_mar:
    margin.render(data)
