"""Shared UI helpers — theme detection, stat tiles, and the chart+table pairing."""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .. import db
from ..insights import duration, money  # re-exported for pages
from ..models import Debt, Profile
from ..theme import FONT, palette

__all__ = ["is_dark", "inject_css", "stat_row", "chart", "money", "duration",
           "current_user", "load_state", "persist", "banner"]


def is_dark() -> bool:
    try:
        return (st.context.theme.type or "light") == "dark"
    except Exception:
        return False


def inject_css() -> None:
    """One stylesheet, written against the palette roles so light/dark swap once."""
    p = palette(is_dark())
    border = "rgba(255,255,255,0.10)" if is_dark() else "rgba(11,11,11,0.10)"
    st.markdown(
        f"""
        <style>
        .stApp {{ font-family: {FONT}; }}
        .tile-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 4px 0 18px; }}
        .tile {{
            flex: 1 1 165px; min-width: 165px;
            background: {p['surface']}; border: 1px solid {border};
            border-radius: 10px; padding: 14px 16px;
        }}
        .tile-label {{
            font-size: 12px; font-weight: 500; letter-spacing: .01em;
            color: {p['muted']}; margin-bottom: 6px;
        }}
        /* Proportional figures on hero values — tabular-nums only in tables. */
        .tile-value {{ font-size: 27px; font-weight: 600; line-height: 1.15; color: {p['text']}; }}
        .tile-sub {{ font-size: 12px; color: {p['text_secondary']}; margin-top: 5px; }}
        .tile-value.good {{ color: {p['good']}; }}
        .tile-value.critical {{ color: {p['critical']}; }}
        .tile-value.warning {{ color: {p['serious']}; }}

        .ins {{
            background: {p['surface']}; border: 1px solid {border};
            border-left: 3px solid {p['muted']};
            border-radius: 10px; padding: 15px 18px; margin-bottom: 12px;
        }}
        .ins.critical {{ border-left-color: {p['critical']}; }}
        .ins.serious  {{ border-left-color: {p['serious']}; }}
        .ins.warning  {{ border-left-color: {p['warning']}; }}
        .ins.good     {{ border-left-color: {p['good']}; }}
        .ins.info     {{ border-left-color: {p['principal']}; }}
        .ins-head {{ display: flex; align-items: baseline; gap: 9px; margin-bottom: 7px; }}
        .ins-title {{ font-size: 16px; font-weight: 600; color: {p['text']}; }}
        .ins-metric {{
            margin-left: auto; font-size: 15px; font-weight: 600; color: {p['text']};
            white-space: nowrap;
        }}
        .ins-body {{ font-size: 14px; line-height: 1.55; color: {p['text_secondary']}; }}
        .ins-body p {{ margin: 0 0 8px; }}
        .ins-body strong {{ color: {p['text']}; font-weight: 600; }}
        .ins-action {{
            font-size: 13.5px; line-height: 1.5; color: {p['text']}; margin-top: 10px;
            padding: 9px 12px; border-radius: 7px;
            background: {'rgba(255,255,255,0.04)' if is_dark() else 'rgba(11,11,11,0.035)'};
        }}
        .ins-action b {{ color: {p['text']}; }}
        div[data-testid="stDataFrame"] {{ font-variant-numeric: tabular-nums; }}
        </style>
        """,
        unsafe_allow_html=True,
    )


# ------------------------------------------------------------------ stat tiles

def stat_row(tiles: Sequence[tuple]) -> None:
    """Render `(label, value, sub, tone)` tuples as a row of stat tiles.

    A number that *is* the story belongs in a tile, not a one-bar chart.
    """
    html = ['<div class="tile-row">']
    for t in tiles:
        label, value, sub = t[0], t[1], (t[2] if len(t) > 2 else "")
        tone = t[3] if len(t) > 3 else ""
        html.append(
            f'<div class="tile"><div class="tile-label">{label}</div>'
            f'<div class="tile-value {tone}">{value}</div>'
            + (f'<div class="tile-sub">{sub}</div>' if sub else "")
            + "</div>"
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


def banner(kind: str, text: str) -> None:
    {"error": st.error, "warning": st.warning, "success": st.success}.get(kind, st.info)(text)


# ------------------------------------------------------- chart + its table twin

def chart(fig: go.Figure, table: Optional[pd.DataFrame] = None, key: Optional[str] = None,
          table_label: str = "View as table") -> None:
    """Every chart ships with a WCAG-clean table equivalent — tooltips enhance,
    they never gate access to a value."""
    st.plotly_chart(fig, width="stretch", key=key,
                    config={"displayModeBar": False, "responsive": True})
    if table is not None and not table.empty:
        with st.expander(table_label):
            st.dataframe(table, width="stretch", hide_index=True)


# ------------------------------------------------------------------- app state

def current_user() -> Optional[int]:
    return st.session_state.get("user_id")


def active_debts() -> list[Debt]:
    return [d for d in st.session_state.get("debts", []) if d.balance > 0]


def effective_budget(debts: Sequence[Debt], profile: Profile) -> float:
    """The budget every projection runs at: what the user set, or their current
    payments, or the bare minimums — whichever is the first thing we actually know."""
    from .. import engine as E
    if profile.monthly_budget and profile.monthly_budget > 0:
        return profile.monthly_budget
    return max(E.current_budget(debts), E.minimum_budget(debts)) if debts else 0.0


def build_plan(debts: Sequence[Debt], profile: Profile, **kw):
    """The user's current plan, the single Schedule every page renders from."""
    from .. import engine as E
    return E.simulate(debts, effective_budget(debts, profile), strategy=profile.strategy,
                      custom_order=profile.custom_order, **kw)


def needs_debts() -> bool:
    """Guard for pages that can't render without data. Returns True if we bailed."""
    if not active_debts():
        st.info("Add your balances on the **My debts** page and this will fill in.")
        return True
    return False


def load_state(user_id: int, force: bool = False) -> tuple[list[Debt], Profile]:
    """Read the user's saved data into session state once per session."""
    if force or "debts" not in st.session_state:
        st.session_state.debts = db.load_debts(user_id)
        st.session_state.profile = db.load_profile(user_id)
    return st.session_state.debts, st.session_state.profile


def persist(user_id: int, debts: list[Debt], profile: Profile, snapshot: bool = True) -> None:
    """Write everything back. Called on every edit, so returning users pick up
    exactly where they left off."""
    db.save_debts(user_id, debts)
    db.save_profile(user_id, profile)
    if snapshot:
        db.record_snapshot(user_id, debts)
    st.session_state.debts = debts
    st.session_state.profile = profile
