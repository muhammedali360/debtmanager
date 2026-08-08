"""Debt Manager — Streamlit entry point.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from debtapp import db
from debtapp import engine as E
from debtapp.ui import account, auth, dashboard, debts, insights_page, scenarios
from debtapp.ui.common import (active_debts, build_plan, duration, effective_budget,
                               inject_css, load_state, money)

st.set_page_config(page_title="Debt Manager", page_icon="💸", layout="wide",
                   initial_sidebar_state="expanded")


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
    st.sidebar.caption(f"Costing you **{money(daily, cents=True)} a day** at "
                       f"**{money(effective_budget(ds, profile))}/mo** on "
                       f"{E.STRATEGY_LABELS[plan.strategy].split('—')[0].strip().lower()}.")


def main() -> None:
    db.init_db()
    inject_css()
    auth.restore_session()

    if not st.session_state.get("user_id"):
        _, mid, _ = st.columns([1, 1.15, 1])
        with mid:
            auth.render()
        return

    load_state(st.session_state.user_id)

    st.sidebar.markdown("## 💸 Debt Manager")
    _sidebar_summary()
    st.sidebar.divider()

    nav = st.navigation([
        st.Page(dashboard.render, title="Dashboard", icon=":material/dashboard:", default=True),
        st.Page(debts.render, title="My debts", icon=":material/credit_card:"),
        st.Page(insights_page.render, title="Insights", icon=":material/lightbulb:"),
        st.Page(scenarios.render, title="What if…", icon=":material/timeline:"),
        st.Page(account.render, title="Account", icon=":material/settings:"),
    ])

    if st.sidebar.button("Sign out", width="stretch"):
        auth.sign_out()

    nav.run()


if __name__ == "__main__":
    main()
