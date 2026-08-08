"""Debt Manager — Streamlit entry point.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from debtapp import db
from debtapp import engine as E
from debtapp import payments as P
from debtapp.ui import account, auth, dashboard, debts, insights_page, ledger, scenarios
from debtapp.ui.common import (active_debts, build_plan, duration, effective_budget,
                               esc_html, inject_css, load_state, money, user_payments)
from debtapp.version import build_id

st.set_page_config(page_title="Debt Manager", page_icon="💸", layout="wide",
                   initial_sidebar_state="expanded")


def _build_tag() -> None:
    """Stamp the running commit into the corner of every screen.

    Lives here, styled inline, rather than alongside the rest of the CSS in
    ``ui.common`` — and that is the whole point of it. A hosted Streamlit
    re-executes *this* file on every run but keeps already-imported modules
    cached, so after a deploy ``app.py`` can be a commit ahead of everything
    under ``debtapp/``. A badge whose job is to report that skew must not be
    able to go stale in the same way, so it depends on nothing but a module
    that did not exist before it.
    """
    # Sat clear of the bottom-right corner: Community Cloud parks its own
    # floating "manage app" pill there and covers anything underneath it.
    st.markdown(
        '<div style="position:fixed;right:12px;bottom:58px;z-index:90;font-size:10.5px;'
        'letter-spacing:.02em;opacity:.5;pointer-events:none;font-variant-numeric:'
        f'tabular-nums">build {esc_html(build_id())}</div>',
        unsafe_allow_html=True)


def _sidebar_summary() -> None:
    """A standing reminder of the two numbers that matter."""
    ds = active_debts()
    if not ds:
        st.sidebar.info("Add your debts to get started.")
        return
    profile = st.session_state.profile
    plan = build_plan(ds, profile)
    total = sum(d.balance for d in ds)
    daily = sum(d.daily_interest for d in ds)

    st.sidebar.metric("Total owed", money(total))
    if plan.never_pays_off:
        st.sidebar.metric("Debt-free", "Never")
        st.sidebar.error("At this payment level the balance never clears.")
    else:
        st.sidebar.metric("Debt-free", plan.payoff_date.strftime("%b %Y"),
                          duration(plan.months), delta_color="off")
        st.sidebar.metric("Interest you'll pay", money(plan.total_interest),
                          f"{plan.interest_share:.0%} of every dollar", delta_color="inverse")

    strategy = E.STRATEGY_LABELS[plan.strategy].split("—")[0].strip().lower()
    note = (f"Costing you <b>{money(daily, cents=True)} a day</b> at "
            f"{money(effective_budget(ds, profile))}/mo on {esc_html(strategy)}.")

    # The one thing that is actionable today rather than in five years.
    due = P.actionable(ds, user_payments())
    if due:
        head = due[0]
        more = f" +{len(due) - 1} more due soon." if len(due) > 1 else ""
        note += (f"<br><br>{P.STATUS_ICON[head.status]} <b>{esc_html(head.name)}</b> — "
                 f"{money(head.amount)} {esc_html(head.phrase)}.{more}")

    # Written as HTML rather than markdown: two money figures in one string make
    # Streamlit's markdown read everything between them as LaTeX.
    st.sidebar.markdown(f'<div class="side-note">{note}</div>', unsafe_allow_html=True)


def main() -> None:
    db.init_db()
    inject_css()
    # Before the auth branch: the sign-in screen is the one an unauthenticated
    # visitor sees, so it is the one that has to carry the build stamp.
    _build_tag()
    auth.restore_session()

    if not st.session_state.get("user_id"):
        auth.render()
        return

    # Straight after signup, make the user acknowledge their recovery codes —
    # they are the only way back in and are shown exactly once.
    if auth.render_recovery_codes_gate():
        return

    load_state(st.session_state.user_id)

    st.sidebar.markdown(
        '<div class="side-brand"><span class="mark">DM</span>Debt Manager</div>',
        unsafe_allow_html=True)
    _sidebar_summary()
    st.sidebar.divider()

    # Every page callable is named ``render``, so the URL path has to be given
    # explicitly — Streamlit would otherwise infer "render" for all of them.
    nav = st.navigation([
        st.Page(dashboard.render, title="Dashboard", icon=":material/dashboard:",
                url_path="dashboard", default=True),
        st.Page(debts.render, title="My debts", icon=":material/credit_card:",
                url_path="debts"),
        st.Page(ledger.render, title="Ledger", icon=":material/receipt_long:",
                url_path="ledger"),
        st.Page(insights_page.render, title="Insights", icon=":material/lightbulb:",
                url_path="insights"),
        st.Page(scenarios.render, title="What if…", icon=":material/timeline:",
                url_path="scenarios"),
        st.Page(account.render, title="Account", icon=":material/settings:",
                url_path="account"),
    ])

    if st.sidebar.button("Sign out", width="stretch"):
        auth.sign_out()

    nav.run()


if __name__ == "__main__":
    main()
