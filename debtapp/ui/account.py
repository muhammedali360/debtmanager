"""Account settings — password, data export, deletion."""

from __future__ import annotations

import json

import pandas as pd
import streamlit as st

from .. import db, security
from .common import active_debts, build_plan, current_user, money, user_payments
from . import auth


def render() -> None:
    uid = current_user()
    st.markdown("### Account")
    st.caption(f"Signed in as **{db.get_email(uid)}**")

    st.markdown("#### Security")
    left, right = st.columns(2)
    with left:
        st.markdown("**Change password**")
        # In a form so the confirmation field's current value reaches the click —
        # see the note in auth.render().
        with st.form("change_password"):
            old = st.text_input("Current password", type="password", key="ac_old")
            new = st.text_input("New password", type="password", key="ac_new",
                                help=f"At least {security.MIN_LENGTH} characters.")
            if new:
                score, label = security.password_strength(new)
                st.progress(score / 4, text=label)
                for p in security.password_problems(new, db.get_email(uid))[:2]:
                    st.caption(f"• {p}")
            new2 = st.text_input("Confirm new password", type="password", key="ac_new2")
            if st.form_submit_button("Update password"):
                if new != new2:
                    st.error("The two new passwords don't match.")
                else:
                    try:
                        db.change_password(uid, old, new)
                        st.success("Password updated — every session was signed out, including "
                                   "this one. Sign in again with the new password.")
                        auth.sign_out()
                    except db.AuthError as e:
                        st.error(str(e))

    with right:
        st.markdown("**Recovery codes**")
        left_codes = db.unused_recovery_code_count(uid)
        if left_codes == 0:
            st.warning("You have no unused recovery codes. Without one you cannot get back "
                       "into this account if you forget your password.")
        else:
            st.caption(f"**{left_codes}** unused code{'s' if left_codes != 1 else ''} remaining.")
        if st.button("Generate a new set"):
            st.session_state.new_codes = db.issue_recovery_codes(uid)
        if st.session_state.get("new_codes"):
            st.warning("Your old codes no longer work. Save these — shown once.")
            st.code("\n".join(st.session_state.new_codes), language=None)
            st.download_button("Download codes", "\n".join(st.session_state.new_codes),
                               file_name="debt-manager-recovery-codes.txt", mime="text/plain")

        st.markdown("**Sessions**")
        st.caption(f"{db.active_session_count(uid)} active session(s).")
        if st.button("Sign out everywhere"):
            db.end_all_sessions(uid)
            auth.sign_out()

    with st.expander("Recent sign-in activity"):
        events = db.recent_logins(uid)
        if not events:
            st.caption("Nothing recorded yet.")
        else:
            hist = pd.DataFrame(events)
            hist["at"] = pd.to_datetime(hist["at"], format="mixed", utc=True
                                        ).dt.strftime("%d %b %Y, %H:%M UTC")
            hist["ok"] = hist["ok"].map({1: "✅ Success", 0: "❌ Failed"})
            hist["note"] = hist["note"].fillna("")
            hist.columns = ["When", "Result", "Note"]
            st.dataframe(hist, width="stretch", hide_index=True)
            st.caption("Failed attempts you don't recognise mean someone knows your email. "
                       "Five failures locks the account for 15 minutes.")

    st.divider()
    st.markdown("#### Export your data")
    debts = st.session_state.get("debts", [])
    profile = st.session_state.profile
    paid = user_payments()
    c1, c2, c3, c4 = st.columns(4)
    with c1:
        st.download_button(
            "Debts (CSV)",
            pd.DataFrame([d.to_dict() for d in debts]).to_csv(index=False) if debts else "",
            file_name="debts.csv", mime="text/csv", disabled=not debts, width="stretch",
        )
    with c2:
        st.download_button(
            "Payment ledger (CSV)",
            pd.DataFrame([p.to_dict() for p in paid]).to_csv(index=False) if paid else "",
            file_name="payment-ledger.csv", mime="text/csv", disabled=not paid,
            width="stretch",
        )
    with c3:
        payload = {"debts": [d.to_dict() for d in debts], "profile": profile.__dict__,
                   "payments": [p.to_dict() for p in paid],
                   "snapshots": db.load_snapshots(uid)}
        st.download_button("Everything (JSON)", json.dumps(payload, indent=2, default=str),
                           file_name="debt-manager-export.json", mime="application/json",
                           width="stretch")
    with c4:
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
