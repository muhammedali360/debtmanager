"""Accounts — simple summaries with focused add/edit forms."""

from __future__ import annotations

from typing import Optional

import streamlit as st

from ..models import CREDIT_CARD, LOAN_SUBTYPES, TERM_LOAN, Debt
from .common import banner, caption, current_user, esc, money, page_header, persist, section, toast


def _kind_label(debt: Debt) -> str:
    return "Credit card" if debt.kind == CREDIT_CARD else (debt.subtype or "Loan")


def _apply(uid: int, debts: list[Debt], profile, message: str = "") -> None:
    """Persist an account-level action and rebuild the page from stored state."""
    persist(uid, debts, profile)
    st.session_state.pop("editing_account", None)
    st.session_state.pop("new_account_kind", None)
    st.session_state.pop("confirm_remove_account", None)
    if message:
        toast(message)
    st.rerun()


def _account_row(debt: Debt, index: int) -> None:
    payment = debt.effective_payment()
    status = "Paid off" if debt.balance <= 0 else f"{money(payment)}/mo"
    with st.container(key=f"account-row-{debt.id or index}"):
        info, action = st.columns([5, 1], vertical_alignment="center")
        info.markdown(esc(
            f"**{debt.name}**  \n"
            f"{_kind_label(debt)} · {money(debt.balance)} · {debt.apr:.2f}% APR · {status}"
            + (f" · due day {debt.due_day}" if debt.due_day else "")
        ))
        if action.button("Edit", key=f"edit_account_{debt.id or index}", width="stretch"):
            st.session_state.editing_account = index
            st.session_state.pop("new_account_kind", None)
            st.rerun()


def _editor(uid: int, debts: list[Debt], profile, *, index: Optional[int], kind: str) -> None:
    """Render the small required surface first; keep model-specific detail optional."""
    original = debts[index] if index is not None else Debt(name="", kind=kind)
    draft = Debt.from_dict(original.to_dict())
    editing = index is not None
    title = f"Edit {original.name}" if editing else (
        "Add a credit card" if kind == CREDIT_CARD else "Add a loan"
    )
    section(title, "Start with the figures shown on your latest statement.")

    with st.form(f"account_form_{'edit_' + str(index) if editing else kind}"):
        c1, c2 = st.columns([1.4, 1])
        name = c1.text_input("Account name", value=draft.name,
                             placeholder="e.g. Chase card or Car loan")
        if kind == TERM_LOAN:
            subtype = c2.selectbox(
                "Loan type", LOAN_SUBTYPES,
                index=LOAN_SUBTYPES.index(draft.subtype)
                if draft.subtype in LOAN_SUBTYPES else len(LOAN_SUBTYPES) - 1,
            )
        else:
            subtype = "Other"

        c1, c2, c3 = st.columns(3)
        balance = c1.number_input("Current balance", min_value=0.0, step=25.0,
                                  value=float(draft.balance))
        apr = c2.number_input("APR", min_value=0.0, max_value=99.0, step=0.25,
                              value=float(draft.apr), format="%.2f")
        minimum = c3.number_input(
            "Minimum payment", min_value=0.0, step=5.0, value=float(draft.min_payment),
            help=("The dollar floor from your statement." if kind == CREDIT_CARD
                  else "The required contractual monthly payment."),
        )

        due_day = st.number_input(
            "Due day (optional)", min_value=0, max_value=31, step=1,
            value=int(draft.due_day),
            help="Use 1–31. Leave 0 if you don't want payment reminders.",
        )

        with st.expander("More details"):
            current_payment = st.number_input(
                "Planned payment for this account (optional)", min_value=0.0, step=25.0,
                value=float(draft.current_payment),
                help="Leave 0 to use the required minimum. Your total monthly plan is set on Plan.",
            )
            if kind == CREDIT_CARD:
                c1, c2 = st.columns(2)
                min_percent = c1.number_input(
                    "Minimum as % of balance", min_value=0.0, max_value=25.0,
                    step=0.5, value=float(draft.min_percent), format="%.1f",
                    help="Usually 1–3%. Together with the dollar floor, this models shrinking minimums.",
                )
                credit_limit = c2.number_input(
                    "Credit limit", min_value=0.0, step=100.0,
                    value=float(draft.credit_limit),
                    help="Optional; used only for utilization recommendations.",
                )
                term_months = 0
            else:
                term_months = st.number_input(
                    "Months remaining", min_value=0, max_value=480, step=1,
                    value=int(draft.term_months),
                    help="Optional; used to calculate a payment only when minimum payment is 0.",
                )
                min_percent, credit_limit = 0.0, 0.0

        buttons = st.columns([1, 1, 1, 2])
        save = buttons[0].form_submit_button("Save account", type="primary", width="stretch")
        cancel = buttons[1].form_submit_button("Cancel", width="stretch")
        paid = buttons[2].form_submit_button(
            "Mark paid off", width="stretch", disabled=not editing or original.balance <= 0,
        )
        remove = False
        if editing:
            remove = st.form_submit_button("Remove account")

    if cancel:
        st.session_state.pop("editing_account", None)
        st.session_state.pop("new_account_kind", None)
        st.rerun()

    if save:
        if not name.strip():
            banner("error", "Give this account a name before saving it.")
            return
        draft.name = name.strip()
        draft.kind = kind
        draft.subtype = subtype
        draft.balance = balance
        draft.apr = apr
        draft.min_payment = minimum
        draft.due_day = int(due_day)
        draft.current_payment = current_payment
        draft.min_percent = min_percent
        draft.credit_limit = credit_limit
        draft.term_months = int(term_months)
        if editing:
            if original.name != draft.name:
                profile.custom_order = [draft.name if n == original.name else n
                                        for n in (profile.custom_order or [])]
            debts[index] = draft
        else:
            debts.append(draft)
        _apply(uid, debts, profile, f"Saved {draft.name}.")

    if paid and editing:
        debts[index].balance = 0.0
        debts[index].current_payment = 0.0
        _apply(uid, debts, profile, f"Nice — {original.name} is paid off.")

    if remove and editing:
        st.session_state.confirm_remove_account = index
        st.rerun()


def _remove_confirmation(uid: int, debts: list[Debt], profile, index: int) -> None:
    debt = debts[index]
    banner(
        "warning",
        f"Remove **{debt.name}**? This deletes the account but keeps its recorded payments "
        "in Activity. This cannot be undone.",
    )
    yes, no, _ = st.columns([1, 1, 3])
    if yes.button("Yes, remove", type="primary", width="stretch"):
        profile.custom_order = [n for n in (profile.custom_order or []) if n != debt.name]
        _apply(uid, [d for i, d in enumerate(debts) if i != index], profile,
               f"Removed {debt.name}.")
    if no.button("Keep it", width="stretch"):
        st.session_state.pop("confirm_remove_account", None)
        st.rerun()


def render() -> None:
    uid = current_user()
    debts: list[Debt] = st.session_state.debts
    profile = st.session_state.profile

    page_header("Accounts", "The balances and payment terms from your latest statements.")

    if "editing_account" not in st.session_state and "new_account_kind" not in st.session_state:
        add_card, add_loan, _ = st.columns([1, 1, 3])
        if add_card.button("+ Add credit card", type="primary", width="stretch"):
            st.session_state.new_account_kind = CREDIT_CARD
            st.rerun()
        if add_loan.button("+ Add loan", width="stretch"):
            st.session_state.new_account_kind = TERM_LOAN
            st.rerun()

    if not debts:
        st.info("Add your first balance to build a payoff plan. You only need its balance, APR, "
                "and minimum payment to begin.")
    else:
        section("Your accounts", f"{len(debts)} account{'s' if len(debts) != 1 else ''} on file")
        editing = st.session_state.get("editing_account")
        editing = int(editing) if editing is not None else None
        shown = ([(editing, debts[editing])]
                 if editing is not None and 0 <= editing < len(debts)
                 else list(enumerate(debts)))
        for i, debt in shown:
            _account_row(debt, i)
            # Editing is a focused mode: hide the other summaries until Save
            # or Cancel so the form can never land below a long account list.
            if editing == i:
                _editor(uid, debts, profile, index=i, kind=debt.kind)

    if "new_account_kind" in st.session_state:
        _editor(uid, debts, profile, index=None, kind=st.session_state.new_account_kind)

    if "confirm_remove_account" in st.session_state:
        index = int(st.session_state.confirm_remove_account)
        if 0 <= index < len(debts):
            _remove_confirmation(uid, debts, profile, index)

    if debts:
        caption("Account changes are saved when you press **Save account**. Plan settings live "
                "on the Plan page.")
