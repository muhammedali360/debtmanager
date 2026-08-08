"""Insights — the "what should I actually do" page."""

from __future__ import annotations

import html
import re

import streamlit as st

from ..insights import SEVERITY_ICON, SEVERITY_LABEL, Insight, generate
from .common import active_debts, effective_budget, needs_debts, stat_row

_ORDER = ["critical", "serious", "warning", "info", "good"]


def _md(text: str) -> str:
    """Minimal markdown → HTML for the insight bodies: escape first, then allow
    **bold** and paragraph breaks. Nothing here is user-authored, but escaping
    keeps a debt named `<b>` from breaking the layout."""
    safe = html.escape(text)
    safe = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", safe)
    paras = [p.strip().replace("\n", " ") for p in safe.split("\n\n") if p.strip()]
    return "".join(f"<p>{p}</p>" for p in paras)


def _card(ins: Insight) -> str:
    metric = f'<div class="ins-metric">{html.escape(ins.metric)}</div>' if ins.metric else ""
    action = (f'<div class="ins-action"><b>Do this:</b> {_md(ins.action)}</div>'
              if ins.action else "")
    # The action block already wraps in <p>; unwrap so it stays on one line.
    action = action.replace("<p>", "").replace("</p>", " ")
    return (
        f'<div class="ins {ins.severity}">'
        f'<div class="ins-head">{SEVERITY_ICON[ins.severity]} '
        f'<span class="ins-title">{html.escape(ins.title)}</span>{metric}</div>'
        f'<div class="ins-body">{_md(ins.body)}</div>{action}</div>'
    )


def render() -> None:
    if needs_debts():
        return

    debts = active_debts()
    profile = st.session_state.profile
    budget = effective_budget(debts, profile)

    with st.spinner("Running the numbers…"):
        found = generate(debts, profile, budget)

    st.markdown("### Insights")
    st.caption("Ranked by how much money is on the table. Everything below is computed from "
               "your own numbers — no generic advice.")

    counts = {s: sum(1 for i in found if i.severity == s) for s in _ORDER}
    savings = sum(i.stake for i in found if i.severity in ("serious", "info"))
    stat_row([
        ("Things to fix", str(counts["critical"] + counts["serious"]),
         "critical or serious", "critical" if counts["critical"] else "warning"),
        ("Opportunities", str(counts["info"]), "money you could recover"),
        ("Identified savings", f"${savings:,.0f}",
         "if you acted on everything below", "good"),
    ])

    show = st.multiselect(
        "Show", _ORDER, default=_ORDER,
        format_func=lambda s: f"{SEVERITY_ICON[s]} {SEVERITY_LABEL[s]}",
        help="Filter by severity. The order never changes — most consequential first.",
    )

    visible = [i for i in found if i.severity in show]
    if not visible:
        st.info("Nothing matches that filter.")
        return

    st.markdown("".join(_card(i) for i in visible), unsafe_allow_html=True)

    st.caption(
        "These projections assume monthly compounding, unchanging APRs, and no new borrowing. "
        "They're a planning tool, not financial advice — and if your minimums are unaffordable, "
        "a nonprofit credit counselor (NFCC member agencies are free) will beat any calculator."
    )
