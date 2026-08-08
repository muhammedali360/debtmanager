"""My debts — the data-entry page. Edits autosave."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import engine as E
from ..models import CREDIT_CARD, LOAN_SUBTYPES, TERM_LOAN, Debt
from .common import (banner, caption, current_user, money, page_header, persist, section,
                     stat_row, toast)

# "id" rides along hidden (column_config maps it to None). It is what keeps a
# card's logged payment history attached to it across renames and edits.
CARD_COLS = ["id", "name", "balance", "apr", "credit_limit", "min_payment", "min_percent",
             "current_payment", "due_day"]
LOAN_COLS = ["id", "name", "subtype", "balance", "apr", "min_payment", "term_months",
             "current_payment", "due_day"]

_INT_COLS = ("term_months", "due_day")


def _to_df(debts: list[Debt], kind: str, cols: list[str]) -> pd.DataFrame:
    rows = [{c: getattr(d, c) for c in cols} for d in debts if d.kind == kind]
    return pd.DataFrame(rows, columns=cols)


def _from_df(df: pd.DataFrame, kind: str, cols: list[str]) -> list[Debt]:
    out = []
    for _, r in df.iterrows():
        name = str(r.get("name") or "").strip()
        if not name:
            continue  # a blank row the user hasn't filled in yet
        vals = {}
        for c in cols:
            v = r.get(c)
            if c == "id":
                # Blank on rows the user just added — those become new records.
                vals["id"] = int(v) if pd.notna(v) else None
            elif c in ("name", "subtype"):
                vals[c] = str(v) if pd.notna(v) else ("Other" if c == "subtype" else name)
            elif c in _INT_COLS:
                vals[c] = 0 if pd.isna(v) else int(v)
            else:
                vals[c] = 0.0 if pd.isna(v) else float(v)
        out.append(Debt(kind=kind, **vals))
    return out


def _label(d: Debt) -> str:
    kind = "Card" if d.kind == CREDIT_CARD else (d.subtype or "Loan")
    return f"{d.name} — {kind}, {money(d.balance)}"


def _apply(uid: int, debts: list[Debt], profile) -> None:
    """Save a change made by a button rather than by typing in a table, then
    start the page over from what was saved.

    The rerun is not optional. Both editors below hold their pending edits keyed
    by *row position*, so removing a row underneath them would re-apply an old
    edit to whatever slid up into that slot. Bumping the revision gives them
    fresh widget keys, which is the only way to drop that state — and returning
    here rather than falling through skips the autosave at the bottom of the
    page, which would otherwise write the pre-click rows straight back.
    """
    persist(uid, debts, profile)
    st.session_state.debts_rev = st.session_state.get("debts_rev", 0) + 1
    st.session_state.pop("_saved_sig", None)
    st.session_state.pop("_confirm_remove", None)
    st.rerun()


def _manage(uid: int, debts: list[Debt], profile) -> None:
    """Whole-account actions, spelled out.

    The checkboxes down the left of the tables above drive Streamlit's own
    toolbar, which deletes a row the moment you click it. That is fine for a
    blank row you added by accident and much too quiet for closing an account
    you have payment history against — and it cannot express "this one is paid
    off" at all, which is the thing people actually reach for. Both of those are
    decisions about an account rather than edits to a cell, so they live here
    where the consequences fit on screen.
    """
    if not debts:
        return

    section("Paid one off? Added one by mistake?",
            "Pick the accounts and say what happened to them. Marking one paid off keeps it "
            "and its history but drops it out of every projection; removing it deletes the "
            "account.")

    rev = st.session_state.get("debts_rev", 0)
    picked = st.multiselect(
        "Accounts to act on", options=list(range(len(debts))),
        format_func=lambda i: _label(debts[i]), key=f"manage_pick_{rev}",
        label_visibility="collapsed", placeholder="Choose one or more accounts",
    )

    if picked:
        chosen = [debts[i] for i in picked]
        caption(f"{len(chosen)} selected · {money(sum(d.balance for d in chosen))} owed · "
                f"{money(sum(d.effective_payment() for d in chosen))}/mo of your budget")
    else:
        # Say what the tick boxes in the tables do, since they look like they
        # should feed this box and don't.
        caption("Nothing selected. The tick boxes in the tables above work too, but the bin "
                "icon there deletes straight away — none of the warnings you get here.")

    c1, c2, _ = st.columns([1, 1, 2])
    paid = c1.button("Mark as paid off", key="mark_paid", width="stretch", disabled=not picked,
                     help="Sets the balance and the payment to zero. The account stays on "
                          "this page and in your Ledger.")
    if c2.button("Remove from my list", key="ask_remove", width="stretch", disabled=not picked):
        st.session_state._confirm_remove = True
        st.rerun()

    if paid:
        for i in picked:
            debts[i].balance = 0.0
            debts[i].current_payment = 0.0   # else it still counts against your budget
        toast(f"Nice — {len(picked)} account{'s' if len(picked) != 1 else ''} paid off.")
        _apply(uid, debts, profile)

    if not (picked and st.session_state.get("_confirm_remove")):
        st.session_state.pop("_confirm_remove", None)
        return

    # Read off `picked` rather than off what was selected when the button was
    # clicked, so editing the selection while this is open changes what goes.
    names = [debts[i].name for i in picked]
    banner("warning",
           f"Remove **{'**, **'.join(names)}**? This deletes the account and cannot be undone. "
           "Any payments you logged against it stay in your Ledger, so what it has already "
           "cost you is still counted.")
    d1, d2, _ = st.columns([1, 1, 2])
    if d1.button("Yes, remove", key="do_remove", type="primary", width="stretch"):
        gone = set(picked)
        removed = {debts[i].name for i in gone}
        profile.custom_order = [n for n in (profile.custom_order or []) if n not in removed]
        toast(f"Removed {len(removed)} account{'s' if len(removed) != 1 else ''}.")
        _apply(uid, [d for i, d in enumerate(debts) if i not in gone], profile)
    if d2.button("Keep them", key="cancel_remove", width="stretch"):
        st.session_state.pop("_confirm_remove", None)
        st.rerun()


_MONEY = dict(format="dollar", min_value=0.0, step=25.0)
_DUE_DAY = st.column_config.NumberColumn(
    "Due day", help="Day of the month this payment is due (1–31). Set it and the app will "
                    "tell you what's coming up and ask whether you've paid. Leave 0 to skip.",
    min_value=0, max_value=31, step=1, format="%d")


def render() -> None:
    uid = current_user()
    debts: list[Debt] = st.session_state.debts
    profile = st.session_state.profile

    page_header("My debts",
                "Everything here saves automatically. Come back any time and your numbers "
                "will be waiting.")

    # Both editors are keyed by revision so a button below can throw their
    # pending edits away — see `_apply`.
    rev = st.session_state.get("debts_rev", 0)

    # ----------------------------------------------------------- credit cards
    section("Credit cards & revolving credit",
            "Minimums on revolving credit are a *percentage of the balance*, so they shrink as "
            "you pay — which is exactly why they take so long to clear. Set the percentage and "
            "the dollar floor from your statement.")
    cards = st.data_editor(
        _to_df(debts, CREDIT_CARD, CARD_COLS),
        num_rows="dynamic", width="stretch", hide_index=True, key=f"ed_cards_{rev}",
        column_config={
            "name": st.column_config.TextColumn("Card", required=True, width="medium"),
            "balance": st.column_config.NumberColumn("Balance", **_MONEY),
            "apr": st.column_config.NumberColumn("APR", format="%.2f%%", min_value=0.0,
                                                 max_value=99.0, step=0.25),
            "credit_limit": st.column_config.NumberColumn(
                "Credit limit", help="Used for utilization. Leave 0 if you'd rather not.",
                **_MONEY),
            "min_payment": st.column_config.NumberColumn(
                "Min ($ floor)", help="The dollar minimum, usually $25–$40.",
                format="dollar", min_value=0.0, step=5.0),
            "min_percent": st.column_config.NumberColumn(
                "Min (% of balance)", help="Usually 1–3%.", format="%.1f%%",
                min_value=0.0, max_value=25.0, step=0.5),
            "current_payment": st.column_config.NumberColumn(
                "You pay now", help="What you actually send each month.", **_MONEY),
            "due_day": _DUE_DAY,
            "id": None,
        },
    )

    # ------------------------------------------------------------ term loans
    section("Term loans",
            "Auto, student, personal, mortgage — anything with a fixed monthly payment and an "
            "end date.")
    loans = st.data_editor(
        _to_df(debts, TERM_LOAN, LOAN_COLS),
        num_rows="dynamic", width="stretch", hide_index=True, key=f"ed_loans_{rev}",
        column_config={
            "name": st.column_config.TextColumn("Loan", required=True, width="medium"),
            "subtype": st.column_config.SelectboxColumn("Type", options=LOAN_SUBTYPES,
                                                        default="Other"),
            "balance": st.column_config.NumberColumn("Balance owed", **_MONEY),
            "apr": st.column_config.NumberColumn("APR", format="%.2f%%", min_value=0.0,
                                                 max_value=99.0, step=0.25),
            "min_payment": st.column_config.NumberColumn(
                "Required payment", help="The contractual monthly payment.", **_MONEY),
            "term_months": st.column_config.NumberColumn(
                "Months left", help="Optional — used only if you leave the payment at 0.",
                min_value=0, max_value=480, step=1),
            "current_payment": st.column_config.NumberColumn("You pay now", **_MONEY),
            "due_day": _DUE_DAY,
            "id": None,
        },
    )

    new_debts = _from_df(cards, CREDIT_CARD, CARD_COLS) + _from_df(loans, TERM_LOAN, LOAN_COLS)

    # ------------------------------------------------------ account actions
    # After the editors, so it acts on what is on screen right now rather than
    # on what was last written to the database.
    _manage(uid, new_debts, profile)

    # ------------------------------------------------------------- the budget
    section("Your monthly plan",
            "One budget drives every projection in the app, so the differences between "
            "strategies come from ordering alone.")
    min_budget = E.minimum_budget(new_debts) if new_debts else 0.0
    cur_budget = E.current_budget(new_debts) if new_debts else 0.0

    c1, c2, c3 = st.columns(3)
    with c1:
        budget = st.number_input(
            "Total you can put toward debt each month", min_value=0.0, step=25.0,
            value=float(profile.monthly_budget or cur_budget or min_budget),
            help="Every strategy is compared at this same budget, so the differences you see "
                 "come from ordering alone.",
        )
    with c2:
        income = st.number_input("Monthly take-home income (optional)", min_value=0.0, step=100.0,
                                 value=float(profile.monthly_income),
                                 help="Used for debt-to-income insights. Leave 0 to skip.")
    with c3:
        fund = st.number_input("Emergency fund (optional)", min_value=0.0, step=100.0,
                               value=float(profile.emergency_fund),
                               help="Cash on hand. Changes what we recommend you do first.")

    strategy = st.radio(
        "Payoff strategy", [E.AVALANCHE, E.SNOWBALL, E.CUSTOM],
        format_func=lambda k: E.STRATEGY_LABELS[k],
        index=[E.AVALANCHE, E.SNOWBALL, E.CUSTOM].index(
            profile.strategy if profile.strategy in (E.AVALANCHE, E.SNOWBALL, E.CUSTOM)
            else E.AVALANCHE),
        horizontal=True, captions=[E.STRATEGY_BLURBS[k] for k in
                                   (E.AVALANCHE, E.SNOWBALL, E.CUSTOM)],
    )

    custom_order = list(profile.custom_order or [])
    if strategy == E.CUSTOM and new_debts:
        names = [d.name for d in new_debts]
        seed = [n for n in custom_order if n in names] + [n for n in names if n not in custom_order]
        custom_order = st.multiselect(
            "Attack in this order (first gets every spare dollar)", options=names, default=seed,
            help="Anything you leave out gets its minimum payment only.",
        )

    if new_debts:
        if budget < min_budget:
            banner("error",
                   f"Your budget of {money(budget)} is below the **{money(min_budget)}** your "
                   "lenders require. Projections below assume payments get pro-rated, which in "
                   "reality means late fees and penalty APRs.")
        stat_row([
            ("Total owed", money(sum(d.balance for d in new_debts))),
            ("Required minimums", f"{money(min_budget)}/mo"),
            ("Your budget", f"{money(budget)}/mo"),
            ("Extra above minimums", f"{money(max(0, budget - min_budget))}/mo",
             "This is the money doing the real work", "good" if budget > min_budget else ""),
        ])
    else:
        st.info("Add a credit card or a loan above to get started.")

    # ---------------------------------------------------------------- autosave
    profile.monthly_budget = budget
    profile.monthly_income = income
    profile.emergency_fund = fund
    profile.strategy = strategy
    profile.custom_order = custom_order

    def signature() -> str:
        return repr(([d.to_dict() for d in new_debts], profile.__dict__.copy()))

    if st.session_state.get("_saved_sig") != signature():
        persist(uid, new_debts, profile)
        # Recomputed *after* the write: saving stamps ids onto brand-new rows, and
        # signing the pre-write state would make the next rerun look dirty again.
        st.session_state._saved_sig = signature()
    caption("✓ Saved")
