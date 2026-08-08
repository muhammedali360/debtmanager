"""Shared UI: the stylesheet, the layout primitives, and app state.

Two things in here are load-bearing beyond their size.

``esc`` — Streamlit's markdown renders ``$…$`` as LaTeX, so any sentence with two
money figures in it ("**$75.07** comes off what you owe; **$174.93** goes to the
lender") silently collapses into a run of maths glyphs. Every string that reaches
markdown goes through :func:`esc` first, which is why the wrappers below exist
rather than calling ``st.markdown`` directly.

``card`` — Streamlit elements are DOM siblings, so an opening ``<div>`` written
with ``st.markdown`` cannot wrap the element after it. Backgrounds therefore come
from a keyed ``st.container``, whose ``st-key-…`` class the CSS hangs off.
"""

from __future__ import annotations

import html as _html
import re
from contextlib import contextmanager
from typing import Optional, Sequence

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from .. import db
from ..insights import duration, money  # re-exported for pages
from ..models import Debt, Payment, Profile
from ..theme import FONT, palette

__all__ = ["is_dark", "inject_css", "stat_row", "chart", "money", "duration", "esc",
           "text", "caption", "banner", "page_header", "section", "card", "table",
           "current_user", "load_state", "persist", "user_payments", "refresh_payments",
           "active_debts", "effective_budget", "build_plan", "needs_debts"]


def is_dark() -> bool:
    try:
        return (st.context.theme.type or "light") == "dark"
    except Exception:
        return False


# ------------------------------------------------------------------ markdown

def esc(body: str) -> str:
    """Escape dollar signs so Streamlit stops reading money as LaTeX."""
    return body.replace("$", r"\$")


def esc_html(body: str) -> str:
    """Escape for HTML, then re-allow **bold** and *italic*.

    Bold is converted first so its pass claims both of its own asterisks; run
    them the other way round and `**x**` comes out as `<em></em>x<em></em>`.
    Italics were missing here entirely, which is why copy written as
    "a *percentage of the balance*" rendered with the asterisks showing.
    """
    safe = _html.escape(body)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    return re.sub(r"(?<!\*)\*([^*\n]+?)\*(?!\*)", r"<em>\1</em>", safe)


def text(body: str, **kw) -> None:
    st.markdown(esc(body), **kw)


def caption(body: str, **kw) -> None:
    st.caption(esc(body), **kw)


def banner(kind: str, body: str) -> None:
    fn = {"error": st.error, "warning": st.warning, "success": st.success}.get(kind, st.info)
    fn(esc(body))


def toast(body: str) -> None:
    st.toast(esc(body))


# ---------------------------------------------------------------- stylesheet

def inject_css() -> None:
    """One stylesheet, written against the palette roles so light/dark swap once."""
    p = palette(is_dark())
    st.markdown(f"""<style>
    /* ---------------------------------------------------------- foundation */
    .stApp {{ font-family: {FONT}; background: {p['canvas']}; }}
    [data-testid="stMain"] {{ background: {p['canvas']}; }}
    [data-testid="stDecoration"], [data-testid="stAppDeployButton"] {{ display: none; }}
    [data-testid="stHeader"] {{ background: transparent; }}
    .stMain .block-container {{ padding-top: 3.2rem; padding-bottom: 5rem; max-width: 1240px; }}
    [data-testid="stMain"] [data-testid="stVerticalBlock"] {{ gap: 0.85rem; }}
    [data-testid="stMarkdownContainer"] > p,
    [data-testid="stMarkdownContainer"] li {{ font-size: 14.5px; line-height: 1.62; }}
    [data-testid="stCaptionContainer"] p {{ font-size: 13px; line-height: 1.55; }}

    /* Streamlit's own headings, for the few places that still use them. */
    .stApp h1, .stApp h2, .stApp h3, .stApp h4 {{
        font-family: {FONT}; font-weight: 600; letter-spacing: -0.014em;
        color: {p['text']};
    }}

    /* ------------------------------------------------------- page + section */
    /* Selectors are deliberately over-qualified: Streamlit's emotion rules for
       h1/h2 outrank a bare class, so a single class here silently loses and the
       page title comes out at 48px. */
    .stApp .page-head {{ margin: 0 0 22px; padding: 0; }}
    .stApp .page-head h1.page-title {{
        font-size: 27px; font-weight: 600; letter-spacing: -0.022em;
        line-height: 1.2; color: {p['text']}; margin: 0; padding: 0;
    }}
    .stApp .page-head p.page-sub {{
        font-size: 14px; line-height: 1.55; color: {p['text_secondary']};
        margin: 7px 0 0; max-width: 74ch;
    }}
    .stApp .sec-head {{ margin: 26px 0 10px; padding: 0; }}
    .stApp .sec-head h2.sec-title {{
        font-size: 17px; font-weight: 600; letter-spacing: -0.014em;
        color: {p['text']}; margin: 0; padding: 0; line-height: 1.3;
    }}
    .stApp .sec-head p.sec-sub {{
        font-size: 13.5px; line-height: 1.55; color: {p['text_secondary']};
        margin: 5px 0 0; max-width: 74ch;
    }}
    .stApp p.sub-label {{
        font-size: 11px; font-weight: 600; letter-spacing: .05em; text-transform: uppercase;
        color: {p['muted']}; margin: 10px 0 2px;
    }}
    .stApp hr {{ border-color: {p['border']}; margin: 26px 0 6px; }}

    /* -------------------------------------------------------------- tiles */
    .tile-row {{ display: flex; gap: 12px; flex-wrap: wrap; margin: 2px 0 8px; }}
    .tile {{
        flex: 1 1 168px; min-width: 168px;
        background: {p['surface']}; border: 1px solid {p['border']};
        border-radius: 12px; padding: 15px 17px 16px;
    }}
    .tile-label {{
        font-size: 11px; font-weight: 600; letter-spacing: .05em;
        text-transform: uppercase; color: {p['muted']}; margin-bottom: 9px;
    }}
    /* Proportional figures on hero values — tabular-nums only in tables. */
    .tile-value {{
        font-size: 26px; font-weight: 600; letter-spacing: -0.022em;
        line-height: 1.12; color: {p['text']};
    }}
    .tile-sub {{
        font-size: 12.5px; line-height: 1.45; color: {p['text_secondary']};
        margin-top: 7px;
    }}
    .tile-value.good {{ color: {p['good']}; }}
    .tile-value.critical {{ color: {p['critical']}; }}
    .tile-value.warning {{ color: {p['serious']}; }}

    /* --------------------------------------------------------------- cards */
    /* Charts and tables sit on their own plane. Keyed containers only — a
       markdown <div> would be a sibling of the chart, not its parent. */
    [class*="st-key-card-"] {{
        background: {p['surface']}; border: 1px solid {p['border']};
        border-radius: 14px; padding: 17px 19px 13px; margin-bottom: 2px;
    }}
    [class*="st-key-card-"] [data-testid="stVerticalBlock"] {{ gap: 0.4rem; }}
    .stApp p.card-title {{
        font-size: 15px; font-weight: 600; letter-spacing: -0.008em;
        color: {p['text']}; margin: 0 0 2px;
    }}
    .stApp p.card-sub {{
        font-size: 12.5px; line-height: 1.5; color: {p['text_secondary']};
        margin: 0 0 4px; max-width: 78ch;
    }}
    /* The plot's own top margin holds the legend; the header owns the space
       above it, so the two can never be laid out into the same strip. */
    [class*="st-key-card-"] .stPlotlyChart {{ margin-top: 6px; }}

    /* -------------------------------------------------------------- tables */
    [data-testid="stDataFrame"], [data-testid="stDataFrameResizable"] {{
        font-variant-numeric: tabular-nums;
        border-radius: 10px; overflow: hidden;
    }}
    [data-testid="stDataFrame"] {{ border: 1px solid {p['border']}; }}
    [class*="st-key-card-"] [data-testid="stDataFrame"] {{ border-color: {p['border']}; }}

    /* ---------------------------------------------------------- expanders */
    [data-testid="stExpander"] details {{
        border: 1px solid {p['border']}; border-radius: 10px;
        background: {p['surface']};
    }}
    [class*="st-key-card-"] [data-testid="stExpander"] details {{
        border-color: transparent; background: transparent;
    }}
    [data-testid="stExpander"] summary {{ font-size: 13px; color: {p['text_secondary']}; }}
    [data-testid="stExpander"] summary:hover {{ color: {p['principal']}; }}

    /* ------------------------------------------------------------ insights */
    .ins {{
        background: {p['surface']}; border: 1px solid {p['border']};
        border-left: 3px solid {p['muted']};
        border-radius: 12px; padding: 16px 19px 17px; margin-bottom: 11px;
    }}
    .ins.critical {{ border-left-color: {p['critical']}; }}
    .ins.serious  {{ border-left-color: {p['serious']}; }}
    .ins.warning  {{ border-left-color: {p['warning']}; }}
    .ins.good     {{ border-left-color: {p['good']}; }}
    .ins.info     {{ border-left-color: {p['principal']}; }}
    .ins-head {{ display: flex; align-items: baseline; gap: 9px; margin-bottom: 8px; }}
    .ins-title {{
        font-size: 15.5px; font-weight: 600; letter-spacing: -0.008em; color: {p['text']};
    }}
    .ins-metric {{
        margin-left: auto; font-size: 13px; font-weight: 600; color: {p['text']};
        white-space: nowrap; background: {p['raised']}; border: 1px solid {p['border']};
        border-radius: 999px; padding: 3px 10px;
    }}
    .ins-body {{ font-size: 14px; line-height: 1.6; color: {p['text_secondary']}; }}
    .ins-body p {{ margin: 0 0 9px; font-size: 14px; }}
    .ins-body p:last-child {{ margin-bottom: 0; }}
    .ins-body strong {{ color: {p['text']}; font-weight: 620; }}
    .ins-action {{
        font-size: 13.5px; line-height: 1.55; color: {p['text_secondary']}; margin-top: 12px;
        padding: 10px 13px; border-radius: 8px; background: {p['raised']};
        border: 1px solid {p['border']};
    }}
    .ins-action b {{ color: {p['text']}; font-weight: 620; }}

    /* ------------------------------------------------------------- widgets */
    .stButton button, .stDownloadButton button, .stFormSubmitButton button {{
        font-weight: 550; letter-spacing: -0.003em;
    }}
    /* Inputs take Streamlit's secondaryBackgroundColor, which is the canvas
       grey — on a grey page that makes the field invisible, and on a white card
       it makes it look sunken. Lift them all onto the surface.
       Streamlit mixes two widget stacks: text and number inputs are react-aria
       with their own testids, selects are still BaseWeb, so both are listed. */
    [data-testid="stTextInputRootElement"],
    [data-testid="stNumberInputContainer"],
    .stTextArea [data-baseweb="textarea"],
    .stDateInput [data-baseweb="input"],
    .stSelectbox [data-baseweb="select"] > div,
    .stMultiSelect [data-baseweb="select"] > div {{
        background-color: {p['surface']};
    }}
    .stSlider, .stRadio {{ padding-top: 2px; }}
    [data-testid="stAlert"] {{ border-radius: 10px; }}
    [data-testid="stAlert"] p {{ font-size: 14px; line-height: 1.58; }}

    /* ------------------------------------------------------------- sidebar */
    [data-testid="stSidebar"] {{ border-right: 1px solid {p['border']}; }}
    [data-testid="stSidebarNav"] {{ padding-top: 0.4rem; }}
    .side-brand {{
        display: flex; align-items: center; gap: 9px;
        font-size: 15.5px; font-weight: 640; letter-spacing: -0.015em;
        color: {p['text']}; margin: 2px 0 14px;
    }}
    .side-brand .mark {{
        width: 25px; height: 25px; border-radius: 7px; flex: 0 0 25px;
        background: {p['principal']}; color: #fff; font-size: 13px; font-weight: 700;
        display: flex; align-items: center; justify-content: center; letter-spacing: 0;
    }}
    [data-testid="stSidebar"] [data-testid="stMetricLabel"] p {{
        font-size: 11px; font-weight: 600; letter-spacing: .05em;
        text-transform: uppercase; color: {p['muted']};
    }}
    [data-testid="stSidebar"] [data-testid="stMetricValue"] {{
        font-size: 22px; font-weight: 600; letter-spacing: -0.02em;
    }}
    [data-testid="stSidebar"] [data-testid="stVerticalBlock"] {{ gap: 0.5rem; }}
    .side-note {{
        font-size: 12.5px; line-height: 1.5; color: {p['text_secondary']};
        background: {p['raised']}; border: 1px solid {p['border']};
        border-radius: 9px; padding: 10px 12px; margin: 4px 0 2px;
    }}
    .side-note b {{ color: {p['text']}; font-weight: 620; }}

    /* ---------------------------------------------------------------- auth */
    .auth-wrap {{ margin: 4px 0 20px; }}
    .auth-lockup {{
        display: flex; align-items: center; gap: 11px; margin-bottom: 26px;
        font-size: 17px; font-weight: 600; letter-spacing: -0.018em; color: {p['text']};
    }}
    .auth-mark {{
        width: 34px; height: 34px; border-radius: 10px; background: {p['principal']};
        color: #fff; font-size: 14px; font-weight: 700; letter-spacing: 0; flex: 0 0 34px;
        display: flex; align-items: center; justify-content: center;
    }}
    .stApp .auth-wrap h1.auth-title {{
        font-size: 33px; font-weight: 600; letter-spacing: -0.03em; line-height: 1.18;
        color: {p['text']}; margin: 0; padding: 0;
    }}
    .stApp .auth-wrap p.auth-sub {{
        font-size: 15px; line-height: 1.6; color: {p['text_secondary']};
        margin: 14px 0 0; max-width: 48ch;
    }}
    [class*="st-key-authcard"] {{
        background: {p['surface']}; border: 1px solid {p['border']};
        border-radius: 16px; padding: 20px 28px 26px;
    }}
    /* The tab panel already sits on the card; a form border inside it reads as
       a box in a box. */
    [class*="st-key-authcard"] [data-testid="stForm"] {{
        border: none; padding: 0;
    }}
    [class*="st-key-authcard"] [data-testid="stVerticalBlock"] {{ gap: 0.55rem; }}
    .auth-points {{ list-style: none; margin: 26px 0 0; padding: 0; }}
    .auth-points li {{
        display: flex; gap: 11px; align-items: flex-start; margin-bottom: 15px;
        font-size: 13.5px; line-height: 1.5; color: {p['text_secondary']};
    }}
    .auth-points b {{ color: {p['text']}; font-weight: 620; display: block; font-size: 14px; }}
    .auth-points .dot {{
        width: 7px; height: 7px; border-radius: 50%; flex: 0 0 7px; margin-top: 7px;
    }}
    .stApp p.auth-foot {{
        font-size: 12.5px; line-height: 1.55; color: {p['muted']};
        margin: 14px 2px 0;
    }}
    /* Tabs read as a segmented control rather than three loose words. */
    [class*="st-key-authcard"] [data-baseweb="tab-list"] {{ gap: 4px; }}
    [class*="st-key-authcard"] [data-baseweb="tab"] {{
        font-size: 14px; font-weight: 550;
    }}
    </style>""", unsafe_allow_html=True)


# ------------------------------------------------------------ layout pieces

def page_header(title: str, subtitle: str = "") -> None:
    """The one h1 on a page. Titles are HTML so their spacing is ours, not
    Streamlit's default heading rhythm."""
    sub = f'<p class="page-sub">{esc_html(subtitle)}</p>' if subtitle else ""
    st.markdown(f'<div class="page-head"><h1 class="page-title">{esc_html(title)}</h1>'
                f'{sub}</div>', unsafe_allow_html=True)


def section(title: str, subtitle: str = "") -> None:
    sub = f'<p class="sec-sub">{esc_html(subtitle)}</p>' if subtitle else ""
    st.markdown(f'<div class="sec-head"><h2 class="sec-title">{esc_html(title)}</h2>'
                f'{sub}</div>', unsafe_allow_html=True)


def subhead(title: str) -> None:
    """A label above a small group of controls — one step below `section`."""
    st.markdown(f'<p class="sub-label">{esc_html(title)}</p>', unsafe_allow_html=True)


def stat_row(tiles: Sequence[tuple]) -> None:
    """Render `(label, value, sub, tone)` tuples as a row of stat tiles.

    A number that *is* the story belongs in a tile, not a one-bar chart.
    """
    html = ['<div class="tile-row">']
    for t in tiles:
        label, value, sub = t[0], t[1], (t[2] if len(t) > 2 else "")
        tone = t[3] if len(t) > 3 else ""
        html.append(
            f'<div class="tile"><div class="tile-label">{esc_html(label)}</div>'
            f'<div class="tile-value {tone}">{esc_html(str(value))}</div>'
            + (f'<div class="tile-sub">{esc_html(sub)}</div>' if sub else "")
            + "</div>"
        )
    html.append("</div>")
    st.markdown("".join(html), unsafe_allow_html=True)


@contextmanager
def card(key: str, title: str = "", subtitle: str = ""):
    """A surface card. `key` must be unique on the page."""
    with st.container(key=f"card-{key}"):
        if title:
            sub = f'<p class="card-sub">{esc_html(subtitle)}</p>' if subtitle else ""
            st.markdown(f'<p class="card-title">{esc_html(title)}</p>{sub}',
                        unsafe_allow_html=True)
        yield


# ------------------------------------------------- chart + its table twin

def chart(fig: go.Figure, table: Optional[pd.DataFrame] = None, key: Optional[str] = None,
          table_label: str = "View as table") -> None:
    """Every chart ships with a WCAG-clean table equivalent — tooltips enhance,
    they never gate access to a value.

    The title comes off ``fig.layout.meta`` and is rendered as HTML above the
    plot. Plotly lays its title and its legend into the same top margin, which
    is what used to jam the legend against the title.
    """
    meta = fig.layout.meta or {}
    with card(key or "chart", meta.get("title", ""), meta.get("subtitle", "")):
        st.plotly_chart(fig, width="stretch", key=key,
                        config={"displayModeBar": False, "responsive": True})
        if table is not None and not table.empty:
            with st.expander(table_label):
                st.dataframe(table, width="stretch", hide_index=True)


def table(df: pd.DataFrame, key: str, title: str = "", subtitle: str = "", **kw):
    """A standalone dataframe on its own surface, so it never blends into the page."""
    with card(key, title, subtitle):
        return st.dataframe(df, width="stretch", hide_index=True, key=f"tbl-{key}", **kw)


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
        st.session_state.payments = db.load_payments(user_id)
    return st.session_state.debts, st.session_state.profile


def user_payments() -> list[Payment]:
    """The recorded ledger for the signed-in user."""
    if "payments" not in st.session_state:
        st.session_state.payments = db.load_payments(current_user())
    return st.session_state.payments


def refresh_payments(user_id: int) -> list[Payment]:
    """Re-read the ledger after a write, so every page sees the new row."""
    st.session_state.payments = db.load_payments(user_id)
    return st.session_state.payments


def persist(user_id: int, debts: list[Debt], profile: Profile, snapshot: bool = True) -> None:
    """Write everything back. Called on every edit, so returning users pick up
    exactly where they left off."""
    db.save_debts(user_id, debts)
    db.save_profile(user_id, profile)
    if snapshot:
        db.record_snapshot(user_id, debts)
    st.session_state.debts = debts
    st.session_state.profile = profile
