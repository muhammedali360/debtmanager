"""My debts — the data-entry page. Edits autosave."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import engine as E
from ..models import CREDIT_CARD, LOAN_SUBTYPES, TERM_LOAN, Debt
from .common import current_user, money, persist, stat_row

CARD_COLS = ["name", "balance", "apr", "credit_limit", "min_payment", "min_percent",
             "current_payment"]
LOAN_COLS = ["name", "subtype", "balance", "apr", "min_payment", "term_months",
             "current_payment"]


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
            if c in ("name", "subtype"):
                vals[c] = str(v) if pd.notna(v) else ("Other" if c == "subtype" else name)
            else:
                vals[c] = 0.0 if pd.isna(v) else float(v)
        vals["term_months"] = int(vals.get("term_months", 0) or 0)
        out.append(Debt(kind=kind, **vals))
    return out


_MONEY = dict(format="dollar", min_value=0.0, step=25.0)


def render() -> None:
    uid = current_user()
    debts: list[Debt] = st.session_state.debts
    profile = st.session_state.profile

    st.markdown("### My debts")
    st.caption("Everything here saves automatically. Come back any time and your numbers "
               "will be waiting.")

    # ----------------------------------------------------------- credit cards
    st.markdown("#### Credit cards & revolving credit")
    st.caption("Minimums on revolving credit are a *percentage of the balance*, so they shrink "
               "as you pay — which is exactly why they take so long to clear. Set the percentage "
               "and the dollar floor from your statement.")
    cards = st.data_editor(
        _to_df(debts, CREDIT_CARD, CARD_COLS),
        num_rows="dynamic", width="stretch", hide_index=True, key="ed_cards",
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
        },
    )

    # ------------------------------------------------------------ term loans
    st.markdown("#### Term loans")
    st.caption("Auto, student, personal, mortgage — anything with a fixed monthly payment "
               "and an end date.")
    loans = st.data_editor(
        _to_df(debts, TERM_LOAN, LOAN_COLS),
        num_rows="dynamic", width="stretch", hide_index=True, key="ed_loans",
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
        },
    )

    new_debts = _from_df(cards, CREDIT_CARD, CARD_COLS) + _from_df(loans, TERM_LOAN, LOAN_COLS)

    # ------------------------------------------------------------- the budget
    st.divider()
    st.markdown("#### Your monthly plan")
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
            st.error(f"Your budget of **{money(budget)}** is below the **{money(min_budget)}** "
                     "your lenders require. Projections below assume payments get pro-rated, "
                     "which in reality means late fees and penalty APRs.")
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

    signature = ([d.to_dict() for d in new_debts], profile.__dict__.copy())
    if st.session_state.get("_saved_sig") != repr(signature):
        persist(uid, new_debts, profile)
        st.session_state._saved_sig = repr(signature)
    st.caption("✓ Saved")
