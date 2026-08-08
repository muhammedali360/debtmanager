"""Insights — the "what should I actually do" page."""

from __future__ import annotations

import html

import streamlit as st

from ..insights import SEVERITY_ICON, SEVERITY_LABEL, Insight, generate
from .common import (active_debts, caption, effective_budget, esc_html, needs_debts,
                     page_header, stat_row, user_payments)

_ORDER = ["critical", "serious", "warning", "info", "good"]


def _md(text: str) -> str:
    """Insight bodies: escaped inline markdown, split into paragraphs.

    Escaping matters even though none of this is user-authored — it keeps a debt
    the user named `<b>` from rewriting the page.
    """
    paras = (p.strip().replace("\n", " ") for p in esc_html(text).split("\n\n"))
    return "".join(f"<p>{p}</p>" for p in paras if p)


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
        found = generate(debts, profile, budget, user_payments())

    page_header("Insights",
                "Ranked by how much money is on the table. Everything below is computed from "
                "your own numbers — no generic advice.")

    counts = {s: sum(1 for i in found if i.severity == s) for s in _ORDER}
    # Only money still on the table. Interest already paid — and the projected
    # interest bill as a whole — rank their insights but are not something acting
    # today recovers, so they carry recoverable=False and are excluded here.
    actionable = [i for i in found
                  if i.severity in ("serious", "info") and i.recoverable and i.stake > 0]
    # The biggest one, not the sum. A balance transfer, a bigger monthly payment
    # and a consolidation loan all attack the same interest, so adding them up
    # would advertise savings no user could actually collect.
    best = max((i.stake for i in actionable), default=0.0)
    stat_row([
        ("Things to fix", str(counts["critical"] + counts["serious"]),
         "critical or serious", "critical" if counts["critical"] else "warning"),
        ("Opportunities", str(len(actionable)), "priced moves you could make"),
        ("Biggest single move", f"${best:,.0f}", "saved by the top card below", "good"),
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

    caption(
        "These projections assume monthly compounding, unchanging APRs, and no new borrowing. "
        "They're a planning tool, not financial advice — and if your minimums are unaffordable, "
        "a nonprofit credit counselor (NFCC member agencies are free) will beat any calculator."
    )
