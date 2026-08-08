"""Dashboard — where the money is going, and when it stops."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import charts, db
from .. import engine as E
from . import ledger
from .common import (active_debts, build_plan, chart, current_user, duration,
                     effective_budget, is_dark, money, needs_debts, stat_row)


def _schedule_table(schedule: E.Schedule) -> pd.DataFrame:
    """The table-view twin for the projection charts."""
    if schedule.monthly.empty:
        return pd.DataFrame()
    m = schedule.monthly.copy()
    m["Month"] = m["date"].dt.strftime("%b %Y")
    out = m[["Month", "payment", "interest", "principal", "balance"]].copy()
    out.columns = ["Month", "Payment", "Interest", "Principal", "Balance remaining"]
    for c in out.columns[1:]:
        out[c] = out[c].map(lambda v: f"${v:,.2f}")
    return out


def render() -> None:
    if needs_debts():
        return

    dark = is_dark()
    debts = active_debts()
    profile = st.session_state.profile
    budget = effective_budget(debts, profile)
    plan = build_plan(debts, profile)

    total = sum(d.balance for d in debts)
    daily = sum(d.daily_interest for d in debts)
    monthly_int = sum(d.monthly_interest for d in debts)
    blended = (monthly_int * 12 / total * 100) if total else 0.0

    st.markdown("### Dashboard")
    st.caption(f"Projected on **{E.STRATEGY_LABELS[plan.strategy].lower()}** at "
               f"**{money(budget)}/month**. Change either on the *My debts* page.")

    # ------------------------------------------------------------------ tiles
    if plan.never_pays_off:
        payoff_val, payoff_sub, payoff_tone = "Never", "Payments don't outrun the interest", "critical"
        interest_val, interest_tone = "Unbounded", "critical"
    else:
        payoff_val = plan.payoff_date.strftime("%b %Y")
        payoff_sub = duration(plan.months)
        payoff_tone = ""
        interest_val = money(plan.total_interest)
        interest_tone = "critical" if plan.interest_share > 0.3 else "warning"

    stat_row([
        ("Total owed", money(total), f"across {len(debts)} account{'s' if len(debts) > 1 else ''}"),
        ("Blended APR", f"{blended:.1f}%", f"{money(monthly_int)}/mo in interest",
         "critical" if blended >= 20 else "warning" if blended >= 12 else ""),
        ("Interest per day", money(daily, cents=True), "before you pay a cent of principal",
         "warning"),
        ("Debt-free", payoff_val, payoff_sub, payoff_tone),
        ("Interest you'll pay", interest_val,
         f"{plan.interest_share:.0%} of every dollar" if not plan.never_pays_off else "",
         interest_tone),
    ])

    if plan.short_months:
        st.error(
            f"Your budget of **{money(budget)}/mo** can't cover the required minimums for "
            f"**{plan.short_months}** month{'s' if plan.short_months > 1 else ''}. The projection "
            "splits what you have proportionally; real lenders would charge late fees and could "
            "raise your APR to ~30%. Call them before that happens."
        )

    # ------------------------------------------------------------- what's due
    # What's due beats what's projected — a payment you forget costs more than
    # any ordering decision below.
    if ledger.due_panel(limit=4):
        st.divider()

    # ----------------------------------------------------------------- charts
    chart(charts.balance_projection(plan, dark, [d.name for d in debts]),
          _schedule_table(plan), key="proj", table_label="View month-by-month schedule")

    left, right = st.columns([1, 1])
    with left:
        chart(charts.dollar_split_meter(plan, dark, 12), key="meter")
    with right:
        y = plan.yearly_interest()
        tbl = y.copy()
        if not tbl.empty:
            tbl.columns = ["Year", "Interest", "Principal"]
            tbl["Interest"] = tbl["Interest"].map(lambda v: f"${v:,.2f}")
            tbl["Principal"] = tbl["Principal"].map(lambda v: f"${v:,.2f}")
        chart(charts.yearly_interest(plan, dark), tbl, key="yearly",
              table_label="View yearly totals")

    chart(charts.principal_vs_interest(plan, dark), key="cum")

    left, right = st.columns([1, 1])
    with left:
        chart(charts.payoff_timeline(plan, dark), key="timeline")
    with right:
        t = plan.per_debt_totals()
        tbl = t.copy()
        if not tbl.empty:
            tbl["payoff_month"] = tbl["payoff_month"].map(
                lambda m: duration(int(m)) if pd.notna(m) else "never")
            for c in ("interest", "principal", "payment"):
                tbl[c] = tbl[c].map(lambda v: f"${v:,.2f}")
            tbl.columns = ["Account", "Interest", "Principal", "Total paid", "Paid off in"]
        chart(charts.interest_by_debt(plan, dark), tbl, key="bydebt",
              table_label="View per-account totals")

    # --------------------------------------------------------------- progress
    snaps = db.load_snapshots(current_user())
    if len(snaps) >= 2:
        st.markdown("#### Your progress")
        st.caption("Update your balances whenever you check in and this tracks the real curve, "
                   "not the projected one.")
        tbl = pd.DataFrame(snaps)[["taken_at", "total_balance", "blended_apr", "debt_count"]]
        tbl["taken_at"] = pd.to_datetime(tbl["taken_at"], format="mixed",
                                         utc=True).dt.strftime("%d %b %Y")
        tbl["total_balance"] = tbl["total_balance"].map(lambda v: f"${v:,.2f}")
        tbl["blended_apr"] = tbl["blended_apr"].map(lambda v: f"{v:.2f}%")
        tbl.columns = ["Date", "Total balance", "Blended APR", "Accounts"]
        chart(charts.progress_history(snaps, dark), tbl, key="hist",
              table_label="View check-in history")
