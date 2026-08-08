"""Debt Manager — Streamlit entry point.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from debtapp import db
from debtapp.ui import account, auth, debts, ledger, plan
from debtapp.ui.common import (active_debts, build_plan, duration, inject_css, load_state,
                               money)

st.set_page_config(page_title="Debt Manager", page_icon="💸", layout="wide",
                   initial_sidebar_state="expanded")


def _sidebar_summary() -> None:
    """The two numbers, and only the two numbers.

    This used to carry five figures, a strategy note and the next payment due —
    every one of which the Plan page states again, larger, two inches to the
    right. A summary that repeats the page it sits beside is not a summary.
    """
    ds = active_debts()
    if not ds:
        st.sidebar.info("Add your debts to get started.")
        return
    schedule = build_plan(ds, st.session_state.profile)
    st.sidebar.metric("Total owed", money(sum(d.balance for d in ds)))
    if schedule.never_pays_off:
        st.sidebar.metric("Debt-free", "Never")
    else:
        st.sidebar.metric("Debt-free", schedule.payoff_date.strftime("%b %Y"),
                          duration(schedule.months), delta_color="off")


def main() -> None:
    db.init_db()
    inject_css()
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

    # Three pages, because there are three things to do here: decide, record
    # what you owe, record what you paid. Dashboard, Insights and What-if were
    # all the same projection and are now one page; Account is settings and is
    # demoted out of the main list rather than sitting beside them as a peer.
    #
    # Every page callable is named ``render``, so the URL path has to be given
    # explicitly — Streamlit would otherwise infer "render" for all of them.
    nav = st.navigation({
        "Your debt": [
            st.Page(plan.render, title="Plan", icon=":material/insights:",
                    url_path="plan", default=True),
            st.Page(debts.render, title="My debts", icon=":material/credit_card:",
                    url_path="debts"),
            st.Page(ledger.render, title="Ledger", icon=":material/receipt_long:",
                    url_path="ledger"),
        ],
        "Settings": [
            st.Page(account.render, title="Account", icon=":material/settings:",
                    url_path="account"),
        ],
    })

    if st.sidebar.button("Sign out", width="stretch"):
        auth.sign_out()

    nav.run()


if __name__ == "__main__":
    main()
