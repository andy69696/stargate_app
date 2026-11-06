"""Shared UI: brand CSS, the navy banner with the UBDS Digital logo, Purpose/Objective boxes, tiles, charts, formatting."""
from __future__ import annotations

import base64
import datetime as dt
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from . import config

ASSETS = Path(__file__).resolve().parent.parent / "assets"
MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CSS = f"""
<style>
:root {{ --navy:{config.NAVY}; --teal:{config.TEAL}; --peri:{config.PERIWINKLE}; --ink:#1f2336; --ink2:#4b5563; --ink3:#8a929e; --grid:#e3e7ed; --bg:#f6f7f9; --good:{config.GOOD}; --bad:{config.BAD}; }}
html, body, [class*="css"] {{ font-family: "IBM Plex Sans", -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; }}
.stApp {{ background: var(--bg); }}
header[data-testid="stHeader"] {{ display: none; }}
.block-container {{ padding-top: 0 !important; max-width: 1280px; }}
.topbar {{ background: var(--navy); color: #fff; margin: 0 -5rem 18px; padding: 10px 2rem; display: flex; align-items: center; gap: 18px; flex-wrap: wrap; }}
.topbar img {{ height: 52px; width: auto; display: block; }}
.topbar .brand {{ font-weight: 600; letter-spacing: .01em; font-size: 15px; }}
.topbar .brand small {{ color: #aeb9c6; font-weight: 400; margin-left: 8px; font-size: 12px; }}
.topbar .role {{ margin-left: auto; color: #c9d3df; font-size: 13px; }}
div[data-testid="stTabs"] button[role="tab"] {{ font-size: 14px; padding: 8px 14px; color: var(--ink2); }}
div[data-testid="stTabs"] button[role="tab"][aria-selected="true"] {{ color: var(--navy); border-bottom: 3px solid var(--teal); }}
h1.pg {{ font-size: 22px; letter-spacing: .02em; margin: 6px 0 4px; color: var(--ink); text-transform: uppercase; }}
.po {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 10px 0 14px; }}
.pobox {{ background: #fff; border: 1px solid var(--grid); border-left: 4px solid var(--teal); border-radius: 8px; padding: 10px 14px; }}
.pol {{ font-size: 11px; letter-spacing: .06em; text-transform: uppercase; color: var(--ink3); margin-bottom: 4px; }}
.pobox p {{ margin: 0; font-size: 13.5px; line-height: 1.45; color: var(--ink); }}
.tiles {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(150px, 1fr)); gap: 10px; margin: 10px 0 14px; }}
.tile {{ background: #fff; border: 1px solid var(--grid); border-radius: 8px; padding: 10px 12px; }}
.tile .l {{ color: var(--ink3); font-size: 12px; letter-spacing: .02em; text-transform: uppercase; }}
.tile .v {{ font-size: 22px; font-weight: 600; font-variant-numeric: tabular-nums; color: var(--ink); }}
.tile .s {{ color: var(--ink2); font-size: 12px; }}
.tile.bad .v {{ color: var(--bad); }} .tile.alert .v {{ color: #b45309; }} .tile.total {{ border-color: var(--teal); }}
.ndx {{ background: #f3f5f8; border-radius: 8px; padding: 10px 12px; font-size: 13px; line-height: 1.45; margin: 4px 0 8px; }}
.banner {{ font-size: 12px; color: var(--ink3); margin: 6px 0; }}
.gap {{ background: #fde8c4; border-radius: 6px; padding: 6px 10px; font-size: 12.5px; margin: 6px 0; }}
.pill {{ display: inline-block; border-radius: 999px; padding: 1px 8px; font-size: 12px; border: 1px solid var(--grid); }}
.flag {{ display: inline-block; border-radius: 4px; padding: 1px 6px; font-size: 11px; background: #eef1f5; color: var(--ink2); margin-right: 4px; }}
.flag.bad {{ background: #fde2e1; color: #8c1d18; }}
.legend span {{ display: inline-flex; align-items: center; gap: 5px; margin-right: 12px; font-size: 12px; color: var(--ink2); }}
.legend i {{ width: 12px; height: 12px; border-radius: 2px; display: inline-block; }}
div[data-testid="stExpander"] details {{ background: #fff; border: 1px solid var(--grid); border-radius: 8px; }}
div[data-testid="stExpander"] summary {{ font-weight: 600; font-size: 15px; color: var(--ink); }}
</style>
"""


def inject_css() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def banner(role: str = "Resource manager") -> None:
    logo = base64.b64encode((ASSETS / "logo_white.png").read_bytes()).decode()
    st.markdown(f'<div class="topbar"><img src="data:image/png;base64,{logo}" alt="UBDS Digital"><div class="brand">Stargate Workspace'
                f'<small>Resource manager · data from the lake</small></div><div class="role">Viewing as {role}</div></div>', unsafe_allow_html=True)


def heading(title: str, purpose: str, objective: str) -> None:
    st.markdown(f'<h1 class="pg">{title}</h1>', unsafe_allow_html=True)
    st.markdown(f'<div class="po"><div class="pobox"><div class="pol">Purpose</div><p>{purpose}</p></div>'
                f'<div class="pobox"><div class="pol">Objective</div><p>{objective}</p></div></div>', unsafe_allow_html=True)


def tiles(items: list[tuple]) -> None:
    """items: (label, value, sub, cls) with cls in '', 'total', 'alert', 'bad'."""
    html = "".join(f'<div class="tile {c}"><div class="l">{l}</div><div class="v">{v}</div><div class="s">{s}</div></div>' for l, v, s, c in items)
    st.markdown(f'<div class="tiles">{html}</div>', unsafe_allow_html=True)


def rules_panel(title: str, paragraphs: list[tuple[str, str]]) -> None:
    with st.expander(title, expanded=False):
        for head, body in paragraphs:
            st.markdown(f"**{head}** {body}")


def gaps_banner(gaps: list[str]) -> None:
    if gaps:
        st.markdown('<div class="gap"><b>Data gaps in this build</b> (see the TODO(databricks) comments in the code): ' + " · ".join(gaps) + "</div>", unsafe_allow_html=True)


# ---- formatting -----------------------------------------------------------------------------------------------------------
def fmt_date(d) -> str:
    """dd/Mmm/yyyy everywhere, as agreed."""
    if not d:
        return "—"
    s = str(d)[:10]
    try:
        x = dt.date.fromisoformat(s)
    except ValueError:
        return s
    return f"{x.day:02d}/{MON[x.month - 1]}/{x.year}"


def fmt_k(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return "—"
    v = float(v)
    a = abs(v)
    s = "−" if v < 0 else ""
    if a >= 1e6:
        return f"{s}£{a / 1e6:.2f}m"
    if a >= 1e3:
        return f"{s}£{a / 1e3:.0f}k"
    return f"{s}£{a:.0f}"


def fmt_n(v) -> str:
    return "—" if v is None else f"{v:,.0f}"


def pct(v) -> str:
    return "—" if v is None else f"{v * 100:.0f}%"


# ---- charts (Plotly, brand palette) ------------------------------------------------------------------------------------------
def _layout(fig: go.Figure, height=320, legend=True) -> go.Figure:
    fig.update_layout(height=height, margin=dict(l=10, r=10, t=10, b=10), paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                      font=dict(family="IBM Plex Sans, sans-serif", size=12, color=config.NAVY), barmode="stack",
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1) if legend else None, showlegend=legend,
                      xaxis=dict(showgrid=False, tickangle=-40), yaxis=dict(gridcolor="#e3e7ed", zeroline=False))
    return fig


def column_chart(cats: list[str], series: dict[str, list[float]], colours: dict[str, str], totals_fmt=fmt_n, height=320, hover: list[str] | None = None) -> go.Figure:
    """Landscape stacked columns with a total label above each column (the signed-off chart style)."""
    fig = go.Figure()
    for name, vals in series.items():
        fig.add_bar(name=name, x=cats, y=vals, marker_color=colours.get(name, config.TEAL), hovertext=hover, hoverinfo="text+y+name")
    totals = [sum(series[s][i] for s in series) for i in range(len(cats))]
    fig.add_scatter(x=cats, y=totals, mode="text", text=[totals_fmt(t) for t in totals], textposition="top center", showlegend=False, hoverinfo="skip",
                    textfont=dict(size=11, family="IBM Plex Mono, monospace"))
    return _layout(fig, height, legend=len(series) > 1)


def trend_chart(months: list[str], rev: list[float], margin: list[float | None], actual: list[bool], thr: float) -> go.Figure:
    fig = go.Figure()
    fig.add_bar(x=months, y=rev, name="Revenue", marker_color="#eef1f5", yaxis="y2", hovertemplate="%{x}: £%{y:,.0f}")
    xa = [m for m, a in zip(months, actual) if a]; ya = [v for v, a in zip(margin, actual) if a]
    xf = [m for m, a in zip(months, actual) if not a]; yf = [v for v, a in zip(margin, actual) if not a]
    if xa:
        fig.add_scatter(x=xa, y=ya, mode="lines+markers", name="Actual margin %", line=dict(color=config.NAVY, width=2))
    if xf:
        fig.add_scatter(x=(xa[-1:] + xf) if xa else xf, y=(ya[-1:] + yf) if xa else yf, mode="lines+markers", name="Forecast margin %", line=dict(color=config.TEAL, width=2, dash="dash"))
    fig.add_hline(y=thr, line=dict(color=config.BAD, dash="dot"), annotation_text=f"{thr:.0f}% threshold", annotation_position="top left",
                  annotation_font=dict(color=config.BAD, size=12))
    fig.update_layout(yaxis=dict(title="Margin %", gridcolor="#e3e7ed"), yaxis2=dict(title="Revenue", overlaying="y", side="right", showgrid=False))
    return _layout(fig, 300)


def status_colours() -> dict[str, str]:
    return dict(config.STATUS_COLOURS)


def legend_html(items: list[tuple[str, str]]) -> str:
    return '<div class="legend">' + "".join(f'<span><i style="background:{c}"></i>{l}</span>' for l, c in items) + "</div>"


def month_label(ym: str) -> str:
    return f"{MON[int(ym[5:7]) - 1]} {ym[2:4]}"
