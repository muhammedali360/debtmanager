"""Home — what to pay next, the current trajectory, and executable advice."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import charts
from .. import engine as E
from ..insights import generate
from . import ledger
from .common import (active_debts, banner, build_plan, card, chart, duration,
                     effective_budget, is_dark, money, open_page, open_recommendation,
                     needs_debts, page_header, plan_brief, section, stat_row, text,
                     user_payments)


def _focus_debt(debts, profile):
    """Mirror the engine's visible payoff priority for the dashboard brief."""
    if profile.strategy == E.SNOWBALL:
        return min(debts, key=lambda d: (d.balance, -d.apr))
    if profile.strategy == E.CUSTOM and profile.custom_order:
        by_name = {d.name: d for d in debts}
        for name in profile.custom_order:
            if name in by_name:
                return by_name[name]
    return max(debts, key=lambda d: (d.apr, -d.balance))


def _money_cols(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        df[c] = df[c].map(lambda v: f"${v:,.2f}")
    return df


def _schedule_table(schedule: E.Schedule) -> pd.DataFrame:
    """The table-view twin for the projection charts."""
    if schedule.monthly.empty:
        return pd.DataFrame()
    m = schedule.monthly.copy()
    m["Month"] = m["date"].dt.strftime("%b %Y")
    out = m[["Month", "payment", "interest", "principal", "balance"]].copy()
    out.columns = ["Month", "Payment", "Interest", "Principal", "Balance remaining"]
    return _money_cols(out, out.columns[1:])


def _recommendations(debts, profile, budget) -> None:
    found = generate(debts, profile, budget, user_payments())
    supported = {"accounts", "plan_extra", "plan_budget", "plan_strategy"}
    actionable = [i for i in found
                  if i.action_type in supported and i.severity != "good"][:3]
    if not actionable:
        return
    section("Recommended next moves", "Open any recommendation with its numbers already filled in.")
    for n, insight in enumerate(actionable):
        with card(f"home-rec-{n}", insight.title, insight.metric or ""):
            text(insight.body)
            if insight.action:
                text(f"**Do this:** {insight.action}")
            if st.button(insight.action_label or "Open", key=f"recommendation_{n}",
                         type="primary" if n == 0 else "secondary"):
                open_recommendation(insight)


def render() -> None:
    if needs_debts():
        return

    debts = active_debts()

    dark = is_dark()
    profile = st.session_state.profile
    budget = effective_budget(debts, profile)
    plan = build_plan(debts, profile)

    total = sum(d.balance for d in debts)
    monthly_int = sum(d.monthly_interest for d in debts)
    blended = (monthly_int * 12 / total * 100) if total else 0.0

    page_header(
        "Home",
        f"Projected on {E.STRATEGY_LABELS[plan.strategy].lower()} at "
        f"{money(budget)}/month. Adjust the plan whenever your situation changes.",
    )

    # What is due now outranks every projection and recommendation below it.
    ledger.due_panel(limit=4)

    # ------------------------------------------------------------------ tiles
    if plan.never_pays_off:
        payoff_val, payoff_sub, payoff_tone = "Never", "Payments don't outrun the interest", "critical"
    else:
        payoff_val = plan.payoff_date.strftime("%b %Y")
        payoff_sub = duration(plan.months)
        payoff_tone = ""

    stat_row([
        ("Total owed", money(total), f"across {len(debts)} account{'s' if len(debts) > 1 else ''}"),
        ("Monthly plan", f"{money(budget)}/mo", f"{money(monthly_int)}/mo is interest",
         "warning" if blended >= 12 else ""),
        ("Debt-free", payoff_val, payoff_sub, payoff_tone),
    ])

    # Headline numbers explain the plan; this turns them into a monthly action.
    required = E.minimum_budget(debts)
    extra = max(0.0, budget - required)
    focus = _focus_debt(debts, profile)
    if plan.payoff_month:
        next_name, next_month = min(plan.payoff_month.items(), key=lambda item: item[1])
        milestone, milestone_note = next_name, f"Paid off in {duration(next_month)}"
    else:
        milestone, milestone_note = "Stabilize balances", "raise payments above monthly interest"
    plan_brief(
        focus.name,
        f"{money(budget)}/mo",
        f"{money(required)} minimums + {money(extra)} extra",
        milestone,
        milestone_note,
    )

    if plan.short_months:
        banner("error",
               f"Your budget of {money(budget)}/mo can't cover the required minimums for "
               f"**{plan.short_months}** month{'s' if plan.short_months > 1 else ''}. The "
               "projection splits what you have proportionally; real lenders would charge late "
               "fees and could raise your APR to ~30%. Call them before that happens.")

    _recommendations(debts, profile, budget)

    # One chart earns the Home page; detailed comparisons live on Plan.
    section("Your trajectory", "The projected balance if you keep following this monthly plan.")
    chart(charts.balance_projection(plan, dark, [d.name for d in debts]),
          _schedule_table(plan), key="proj", table_label="View month-by-month schedule")
