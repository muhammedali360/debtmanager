"""Sign in / sign up screen."""

from __future__ import annotations

import streamlit as st

from .. import db
from ..models import CREDIT_CARD, TERM_LOAN, Debt, Profile

SESSION_QS = "s"  # query-param key holding the session token


def restore_session() -> None:
    """Log the user back in from the session token in the URL."""
    if st.session_state.get("user_id"):
        return
    token = st.query_params.get(SESSION_QS)
    if not token:
        return
    uid = db.resolve_session(token)
    if uid:
        st.session_state.user_id = uid
        st.session_state.token = token
    else:
        del st.query_params[SESSION_QS]


def _sign_in(uid: int) -> None:
    token = db.start_session(uid)
    st.session_state.user_id = uid
    st.session_state.token = token
    st.query_params[SESSION_QS] = token
    for k in ("debts", "profile"):
        st.session_state.pop(k, None)
    st.rerun()


def sign_out() -> None:
    if st.session_state.get("token"):
        db.end_session(st.session_state.token)
    st.query_params.clear()
    st.session_state.clear()
    st.rerun()


def _seed_demo(uid: int) -> None:
    """A realistic starting portfolio so the app has something to say on day one."""
    db.save_debts(uid, [
        Debt(name="Chase Sapphire", kind=CREDIT_CARD, balance=8_400, apr=24.99,
             min_payment=35, min_percent=2.0, credit_limit=12_000, current_payment=250),
        Debt(name="Citi Double Cash", kind=CREDIT_CARD, balance=3_150, apr=21.49,
             min_payment=25, min_percent=2.0, credit_limit=6_000, current_payment=95),
        Debt(name="Store card", kind=CREDIT_CARD, balance=1_250, apr=29.99,
             min_payment=25, min_percent=3.0, credit_limit=2_000, current_payment=40),
        Debt(name="Car loan", kind=TERM_LOAN, subtype="Auto", balance=18_600, apr=7.4,
             min_payment=445, term_months=48, current_payment=445),
        Debt(name="Student loan", kind=TERM_LOAN, subtype="Student", balance=21_800,
             apr=5.5, min_payment=232, term_months=120, current_payment=232),
    ])
    db.save_profile(uid, Profile(monthly_budget=1_250, monthly_income=6_200,
                                 emergency_fund=1_800, strategy="avalanche"))


def render() -> None:
    st.markdown("## Debt Manager")
    st.caption("See exactly where your money is going — and what it would take to get out.")

    tab_in, tab_up = st.tabs(["Sign in", "Create account"])

    with tab_in:
        with st.form("signin"):
            email = st.text_input("Email", key="in_email", autocomplete="username")
            pw = st.text_input("Password", type="password", key="in_pw",
                               autocomplete="current-password")
            if st.form_submit_button("Sign in", type="primary", width="stretch"):
                try:
                    _sign_in(db.verify_user(email, pw))
                except db.AuthError as e:
                    st.error(str(e))

    with tab_up:
        with st.form("signup"):
            email = st.text_input("Email", key="up_email", autocomplete="username")
            pw = st.text_input("Password", type="password", key="up_pw",
                               help="At least 8 characters.", autocomplete="new-password")
            pw2 = st.text_input("Confirm password", type="password", key="up_pw2",
                                autocomplete="new-password")
            demo = st.checkbox("Start with example debts I can edit", value=True)
            if st.form_submit_button("Create account", type="primary", width="stretch"):
                if pw != pw2:
                    st.error("The two passwords don't match.")
                else:
                    try:
                        uid = db.create_user(email, pw)
                        if demo:
                            _seed_demo(uid)
                        _sign_in(uid)
                    except db.AuthError as e:
                        st.error(str(e))

    st.caption(
        "Your data is stored locally in this app's database and is only used to compute your "
        "projections. Passwords are hashed with bcrypt — nothing is sent anywhere else."
    )
