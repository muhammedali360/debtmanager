"""Account settings — password, data export, deletion."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from .. import db
from .common import active_debts, build_plan, current_user, money
from . import auth


def render() -> None:
    uid = current_user()
    st.markdown("### Account")
    st.caption(f"Signed in as **{db.get_email(uid)}**")

    st.markdown("#### Change password")
    with st.form("pw"):
        old = st.text_input("Current password", type="password")
        new = st.text_input("New password", type="password", help="At least 8 characters.")
        new2 = st.text_input("Confirm new password", type="password")
        if st.form_submit_button("Update password"):
            if new != new2:
                st.error("The two new passwords don't match.")
            else:
                try:
                    db.change_password(uid, old, new)
                    st.success("Password updated. You've been signed out of other devices.")
                except db.AuthError as e:
                    st.error(str(e))

    st.divider()
    st.markdown("#### Export your data")
    debts = st.session_state.get("debts", [])
    profile = st.session_state.profile
    c1, c2, c3 = st.columns(3)
    with c1:
        st.download_button(
            "Debts (CSV)",
            pd.DataFrame([d.to_dict() for d in debts]).to_csv(index=False) if debts else "",
            file_name="debts.csv", mime="text/csv", disabled=not debts, width="stretch",
        )
    with c2:
        payload = {"debts": [d.to_dict() for d in debts], "profile": profile.__dict__,
                   "snapshots": db.load_snapshots(uid)}
        st.download_button("Everything (JSON)", json.dumps(payload, indent=2, default=str),
                           file_name="debt-manager-export.json", mime="application/json",
                           width="stretch")
    with c3:
        active = active_debts()
        sched = build_plan(active, profile).ledger if active else pd.DataFrame()
        st.download_button(
            "Payoff schedule (CSV)",
            sched.to_csv(index=False) if not sched.empty else "",
            file_name="payoff-schedule.csv", mime="text/csv",
            disabled=sched.empty, width="stretch",
        )

    st.divider()
    st.markdown("#### Danger zone")
    with st.expander("Delete my account and all my data"):
        st.warning("This erases your debts, history, and login. It cannot be undone.")
        confirm = st.text_input('Type **DELETE** to confirm', key="del_confirm")
        if st.button("Permanently delete my account", type="primary",
                     disabled=confirm.strip() != "DELETE"):
            db.delete_user(uid)
            auth.sign_out()
