"""Ledger — what's due next, and what you've actually paid.

The rest of the app projects forward. This page looks backward, and the two must
never be confused: the numbers here are money that genuinely left the user's
account, so they are the only figures the app can state without a caveat.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

import pandas as pd
import streamlit as st

from .. import charts, db
from .. import payments as P
from ..models import Debt
from .common import (active_debts, chart, current_user, is_dark, money, persist,
                     refresh_payments, stat_row, user_payments)

# Urgency reads off the colour before the words are read at all.
_COLOUR = {P.OVERDUE: "red", P.DUE_TODAY: "orange", P.PAID: "green"}


# ------------------------------------------------------------------ recording

def log_payment(debt: Debt, amount: float, when: date, interest: Optional[float] = None,
                note: str = "", reduce_balance: bool = True) -> None:
    """Write one payment to the ledger, optionally moving the balance down too.

    ``reduce_balance`` is not a convenience flag — it is the difference between
    two genuinely different actions. Recording a payment you *just made* should
    reduce the balance, because the figure on file predates it. Backfilling a
    payment from three months ago must not: the balance the user typed in
    already reflects it, and deducting it again understates the debt and quietly
    corrupts every projection downstream.
    """
    uid = current_user()
    payment = P.build_payment(debt, amount, when, interest, note)
    if not reduce_balance:
        # We can't know what the balance was on that date, and guessing would put
        # a fabricated number in a table whose whole claim is that it's real.
        payment.balance_after = None
    db.record_payment(uid, payment)
    refresh_payments(uid)
    if not reduce_balance:
        return

    debt.balance = payment.balance_after or 0.0
    persist(uid, st.session_state.debts, st.session_state.profile)

    # The grids on the My debts page keep their edits as a delta against whatever
    # dataframe they're handed, so an edit made earlier in this session would be
    # replayed over the balance we just reduced — silently undoing the payment on
    # that page's next autosave. Dropping their state forces a rebuild from the
    # stored values. Cheap insurance; the user loses nothing but an unsaved cell.
    for grid in ("ed_cards", "ed_loans"):
        st.session_state.pop(grid, None)
    st.session_state.pop("_saved_sig", None)


# ------------------------------------------------- the "did you pay?" widget

def due_panel(heading: str = "#### Payments coming up", limit: Optional[int] = None) -> bool:
    """Render due/overdue accounts with a one-click way to confirm payment.

    Returns True if anything was drawn. Shared with the dashboard so the prompt
    is wherever the user already is, rather than on a page they have to think
    to visit.
    """
    debts = active_debts()
    items = P.upcoming(debts, user_payments())
    if not items:
        if any(not d.due_day for d in debts):
            st.caption("💡 Add a **due day** to each account on the *My debts* page and this "
                       "turns into a payment calendar that tells you what's coming.")
        return False

    live = [i for i in items if i.is_actionable]
    shown = live or items
    if limit:
        shown = shown[:limit]

    st.markdown(heading)
    if not live:
        st.success("Nothing due right now — everything on file is paid for this cycle.")

    for item in shown:
        cols = st.columns([4.2, 1.5, 1.5], vertical_alignment="center")
        colour = _COLOUR.get(item.status, "gray")
        # An account with no required payment and no stated payment has no amount
        # we can honestly quote, so ask rather than assert.
        owed = money(item.amount) if item.amount > 0 else "amount not set"
        cols[0].markdown(
            f"{P.STATUS_ICON[item.status]} **{item.name}** · {owed} "
            f":{colour}[{item.phrase}] · {item.due_date:%d %b}"
            + (f" · {money(item.paid)} logged" if item.paid > 0 and item.status != P.PAID else "")
        )
        if item.status == P.PAID:
            cols[1].caption("Paid ✓")
            continue
        amount = cols[1].number_input(
            f"Amount for {item.name}", min_value=0.0, step=25.0,
            value=float(round(item.outstanding or item.amount, 2)),
            key=f"due_amt_{item.debt.id}_{item.name}", label_visibility="collapsed",
        )
        if cols[2].button("I paid this", key=f"due_btn_{item.debt.id}_{item.name}",
                          width="stretch", disabled=amount <= 0):
            log_payment(item.debt, amount, date.today())
            st.toast(f"Logged {money(amount)} to {item.name}")
            st.rerun()

    overdue = [i for i in shown if i.status == P.OVERDUE]
    if overdue:
        st.caption("Late fees land immediately, but nothing reaches your credit report until "
                   "**30 days** past due. If you're going to miss one, call the lender first — "
                   "a hardship note costs nothing and a 30-day mark costs you for seven years.")
    return True


# ------------------------------------------------------------------- the page

def _history_table(ordered: list) -> pd.DataFrame:
    """One display row per payment, in the order given.

    Row *order* is load-bearing: the delete button maps a selected row index
    straight back onto the same list, so this must not re-sort.
    """
    return pd.DataFrame([{
        "Date": p.paid_on.strftime("%d %b %Y"),
        "Account": p.debt_name,
        "Paid": f"${p.amount:,.2f}",
        "Interest": f"${p.interest:,.2f}",
        "Principal": f"${p.principal:,.2f}",
        "Balance after": f"${p.balance_after:,.2f}" if p.balance_after is not None else "—",
        "Note": p.note,
    } for p in ordered])


def _log_form(debts: list[Debt]) -> None:
    st.markdown("#### Log a payment")
    st.caption("Backfill anything you've already sent — the ledger is only as useful as it is "
               "complete.")
    # Account and date sit outside the form: widgets inside a form don't rerun
    # until submit, and the balance question below has to follow the date.
    left, right = st.columns(2)
    chosen = left.selectbox("Account", [d.name for d in debts], key="log_debt")
    when = right.date_input("Date paid", value=date.today(), max_value=date.today(),
                            key="log_when")
    debt = next(d for d in debts if d.name == chosen)
    is_backfill = when < date.today()

    with st.form("log_payment_form"):
        c1, c2 = st.columns(2)
        amount = c1.number_input("Amount paid", min_value=0.0, step=25.0,
                                 value=float(round(debt.effective_payment(), 2)))
        interest = c2.number_input(
            "Interest charged", min_value=0.0, step=5.0,
            value=float(round(debt.monthly_interest, 2)),
            help="Pre-filled with one month's interest at this balance and APR. If your "
                 "statement shows a different figure, use theirs — it's the real one.")
        note = st.text_input("Note (optional)", placeholder="e.g. extra from my bonus")
        reduce_balance = st.checkbox(
            f"Also take this off {debt.name}'s balance of {money(debt.balance)}",
            value=not is_backfill,
            help="Leave this off when you're backfilling a payment you've already accounted "
                 "for — the balance on file would otherwise be reduced by it twice.",
        )

        principal = amount - interest
        if amount > 0 and reduce_balance:
            if principal < 0:
                st.error(f"At {money(amount)} against {money(interest, cents=True)} of interest, "
                         f"this payment leaves **{money(-principal, cents=True)}** of unpaid "
                         "interest on the balance — it goes *up*, not down.")
            else:
                st.caption(f"**{money(principal, cents=True)}** comes off what you owe; "
                           f"**{money(interest, cents=True)}** goes to {debt.name}'s lender. "
                           f"New balance: **{money(max(0.0, debt.balance + interest - amount))}**.")
        elif amount > 0:
            st.caption(f"Recorded as history only — {debt.name}'s balance of "
                       f"**{money(debt.balance)}** stays as it is.")

        if st.form_submit_button("Log this payment", type="primary") and amount > 0:
            log_payment(debt, amount, when, interest, note.strip(), reduce_balance)
            st.toast(f"Logged {money(amount)} to {debt.name}")
            st.rerun()


def render() -> None:
    uid = current_user()
    dark = is_dark()
    debts = st.session_state.get("debts", [])
    rows = user_payments()
    t = P.totals(rows)

    st.markdown("### Ledger")
    st.caption("Every payment you've actually made. Unlike the projections elsewhere in the app, "
               "nothing on this page is an estimate.")

    due_panel()
    st.divider()

    if not rows:
        st.info("No payments logged yet. Record one below — after two or three months this page "
                "will show you exactly what your debt has cost you in real dollars.")
        if debts:
            _log_form(debts)
        return

    # --------------------------------------------------------------- counters
    share = t["interest_share"]
    stat_row([
        ("Total paid", money(t["paid"]),
         f"{t['count']} payment{'s' if t['count'] != 1 else ''} since "
         f"{t['first']:%b %Y}"),
        ("Gone to interest", money(t["interest"]),
         f"{share:.0%} of every dollar you paid",
         "critical" if share >= 0.3 else "warning" if share >= 0.15 else ""),
        ("Off your balance", money(t["principal"]), "the part that was actually yours", "good"),
        ("Average month", money(t["avg_monthly"]),
         f"across {t['months']} month{'s' if t['months'] != 1 else ''}"),
        ("Logging streak", f"{P.streak(rows)} mo",
         "consecutive months recorded", "good"),
    ])

    if t["interest"] > 0:
        st.markdown(
            f"Since **{t['first']:%d %B %Y}** you have sent lenders **{money(t['paid'])}**. "
            f"**{money(t['interest'])}** of that never touched your balance — it was rent on "
            f"money you'd already borrowed. That's **{share * 100:.0f}¢ of every dollar**, "
            f"or about **{money(t['interest'] / max(1, t['months']))} a month** of pure cost."
        )
        year = P.interest_since_tracking(rows, 365)
        if year > 0 and year < t["interest"]:
            st.caption(f"In the last 12 months alone: **{money(year)}** in interest.")

    # ----------------------------------------------------------------- charts
    chart(charts.ledger_cumulative(P.cumulative(rows), dark), key="ledger_cum")

    monthly = P.by_month(rows)
    tbl = monthly.drop(columns=["date"]).copy()
    if not tbl.empty:
        for c in ("payment", "interest", "principal"):
            tbl[c] = tbl[c].map(lambda v: f"${v:,.2f}")
        tbl.columns = ["Month", "Paid", "Interest", "Principal"]
    chart(charts.payments_by_month(monthly, dark), tbl, key="ledger_month",
          table_label="View monthly totals")

    # ------------------------------------------------------------- by account
    st.markdown("#### What each account has cost you")
    per = P.by_debt(rows)
    view = per.copy()
    view["interest_share"] = view["interest_share"].map(lambda v: f"{v:.0%}")
    view["last"] = view["last"].map(lambda d: d.strftime("%d %b %Y"))
    for c in ("paid", "interest", "principal"):
        view[c] = view[c].map(lambda v: f"${v:,.2f}")
    view.columns = ["Account", "Payments", "Total paid", "Interest", "Principal",
                    "Interest share", "Last payment"]
    st.dataframe(view, width="stretch", hide_index=True)

    # ---------------------------------------------------------------- history
    st.markdown("#### Payment history")
    newest_first = list(reversed(rows))
    event = st.dataframe(_history_table(newest_first), width="stretch", hide_index=True,
                         on_select="rerun", selection_mode="multi-row", key="hist_select")
    picked = list(getattr(event, "selection", {}).get("rows", []))
    if picked:
        if st.button(f"Delete {len(picked)} selected payment"
                     f"{'s' if len(picked) != 1 else ''}", type="primary"):
            for i in picked:
                if 0 <= i < len(newest_first) and newest_first[i].id is not None:
                    db.delete_payment(uid, newest_first[i].id)
            refresh_payments(uid)
            st.rerun()
        st.caption("Deleting a payment removes it from these totals. It does **not** put the "
                   "money back on your balance — correct the balance on *My debts* if you need to.")

    st.divider()
    if debts:
        _log_form(debts)
