"""First run — the four questions that are enough to say something useful.

Onboarding used to be a checkbox on the signup form ("Start with example debts I
can edit"). Tick it and the app opened onto someone else's money; untick it and
it opened onto a page telling you to go and find a different page. Neither is an
introduction.

This asks for one account and answers before it is even saved, because the
answer *is* the product: a date and an interest figure the user did not have
this morning. Everything the rest of the app wants — minimums, credit limits,
due days, the other four accounts — is worth asking for only once someone has a
reason to keep typing.
"""

from __future__ import annotations

import streamlit as st

from .. import engine as E
from ..models import ACCOUNT_TYPES, Debt, kind_for
from .common import (banner, caption, current_user, duration, money, page_header, persist,
                     section, text)


def _preview(account_type: str, balance: float, apr: float, payment: float) -> None:
    """Answer with whatever has been typed so far.

    Deliberately live rather than behind the button: a projection shown *after*
    you commit is a reward for trusting the form, and a new user has no reason
    to.
    """
    if balance <= 0:
        caption("Fill in a balance and this turns into a payoff date before you save anything.")
        return

    kind, subtype = kind_for(account_type)
    probe = Debt(name="This account", kind=kind, subtype=subtype, balance=balance, apr=apr,
                 current_payment=payment)
    monthly = probe.monthly_interest

    if payment <= 0:
        if monthly <= 0:
            caption("No interest on this one — whatever you pay comes straight off the balance.")
        else:
            text(f"At {apr:.2f}% this balance costs you "
                 f"**{money(probe.daily_interest, cents=True)} a day** — "
                 f"{money(monthly, cents=True)} a month — before you pay a cent of it back. "
                 "Add what you pay each month and we'll date the last payment.")
        return

    plan = E.simulate([probe], payment, strategy=E.AVALANCHE)
    if plan.never_pays_off:
        banner("error",
               f"At {money(payment)} a month this balance never clears: interest alone is "
               f"{money(monthly, cents=True)} a month, so the payment barely touches what you "
               "owe. Add it anyway — the app is at its most useful on exactly this account.")
        return

    text(f"At {money(payment)} a month you'd clear this in **{duration(plan.months)}**, in "
         f"{plan.payoff_date:%B %Y}, and hand the lender **{money(plan.total_interest)}** in "
         f"interest getting there — {plan.interest_share:.0%} of every dollar you send.")


def first_debt() -> None:
    """Ask for one account, then get out of the way."""
    page_header("Let's start with one account",
                "Add whichever debt bothers you most. Four things is enough to project it — "
                "the rest of your accounts, and everything optional, go on My debts.")

    section("The account")
    c1, c2 = st.columns([2, 1])
    name = c1.text_input("What do you call it?", key="ob_name", placeholder="e.g. Chase card")
    account_type = c2.selectbox("Type", ACCOUNT_TYPES, key="ob_type")

    c3, c4, c5 = st.columns(3)
    balance = c3.number_input("Balance you owe", min_value=0.0, step=100.0, key="ob_balance")
    apr = c4.number_input("APR", min_value=0.0, max_value=99.0, step=0.25, format="%.2f",
                          key="ob_apr", help="The interest rate printed on your statement.")
    payment = c5.number_input("You pay each month", min_value=0.0, step=25.0, key="ob_payment",
                              help="What you actually send, not what they ask for.")

    _preview(account_type, balance, apr, payment)

    ready = bool(name.strip()) and balance > 0
    if st.button("Add this account", key="ob_add", type="primary", disabled=not ready):
        kind, subtype = kind_for(account_type)
        debts = list(st.session_state.get("debts", []))
        debts.append(Debt(name=name.strip(), kind=kind, subtype=subtype, balance=balance,
                          apr=apr, current_payment=payment))
        # The budget is left at zero on purpose: `effective_budget` then derives
        # it from what the accounts actually pay, so adding a second debt raises
        # it automatically instead of stranding the plan at the first one's
        # payment.
        persist(current_user(), debts, st.session_state.profile)
        # The grid on My debts keys its pending edits off this; a row appearing
        # underneath it from somewhere else is exactly what it wants to know.
        st.session_state.debts_rev = st.session_state.get("debts_rev", 0) + 1
        st.session_state.pop("_saved_sig", None)
        for k in ("ob_name", "ob_balance", "ob_apr", "ob_payment"):
            st.session_state.pop(k, None)
        st.rerun()

    if not ready:
        caption("A name and a balance are all that's required.")
