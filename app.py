"""Debt Manager — Streamlit entry point.

Run with:  streamlit run app.py
"""

from __future__ import annotations

import streamlit as st

from debtapp import db
from debtapp.ui import account, auth, debts, ledger, plan
from debtapp.ui.common import (active_debts, build_plan, duration, esc_html, inject_css,
                               load_state, money)
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
