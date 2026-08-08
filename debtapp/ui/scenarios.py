"""What-if — move the levers and watch the payoff date move."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .. import charts
from .. import engine as E
from .common import (active_debts, build_plan, chart, duration, effective_budget,
                     is_dark, money, needs_debts, stat_row)


def _delta_tile(label: str, base: E.Schedule, alt: E.Schedule) -> tuple:
    if base.never_pays_off or alt.never_pays_off:
        return (label, "—", "not comparable")
    saved = base.total_interest - alt.total_interest
    sign = "−" if saved >= 0 else "+"
    return (label, f"{sign}${abs(saved):,.0f}",
            f"{duration(abs(base.months - alt.months))} "
            f"{'sooner' if alt.months <= base.months else 'later'}",
            "good" if saved > 0 else "critical" if saved < 0 else "")


def render() -> None:
    if needs_debts():
        return

    dark = is_dark()
    debts = active_debts()
    profile = st.session_state.profile
    budget = effective_budget(debts, profile)
    base = build_plan(debts, profile)

    st.markdown("### What if…")
    st.caption("Every scenario below runs the same engine as your dashboard. Nothing here is "
               "saved — it's a sandbox.")

    # ------------------------------------------------------- the levers
    st.markdown("#### Move the levers")
    c1, c2, c3 = st.columns(3)
    with c1:
        extra = st.slider("Extra per month", 0, 2_000, 100, step=25, format="$%d")
    with c2:
        lump = st.slider("One-time payment", 0, 20_000, 0, step=250, format="$%d")
    with c3:
        lump_when = st.slider("…applied in month", 1, 36, 1, disabled=lump == 0)

    growth = st.slider(
        "Annual raise you'll add to payments", 0, 25, 0, format="%d%%",
        help="Most people's payments stay flat while income rises. Routing even part of a "
             "raise at debt compounds hard.",
    )

    alt = E.simulate(debts, budget, strategy=profile.strategy,
                     custom_order=profile.custom_order, extra=extra,
                     lump_sum=lump, lump_month=lump_when, budget_growth=growth)

    stat_row([
        ("New debt-free date",
         alt.payoff_date.strftime("%b %Y") if not alt.never_pays_off else "Never",
         f"was {base.payoff_date.strftime('%b %Y')}" if not base.never_pays_off else "was never"),
        ("Time saved",
         duration(base.months - alt.months) if not (base.never_pays_off or alt.never_pays_off)
         else "—", "off your timeline", "good"),
        _delta_tile("Interest change", base, alt),
        ("Total you'd pay", money(alt.total_paid),
         f"was {money(base.total_paid)}"),
    ])

    chart(charts.plan_race({"Your current plan": base, "This scenario": alt}, dark), key="race")

    # --------------------------------------------------- target-date solver
    st.divider()
    st.markdown("#### Work backwards from a date")
    st.caption("Pick a deadline and we'll solve for the payment that hits it.")
    years = st.slider("I want to be debt-free in", 1, 20,
                      value=max(1, min(20, (base.months // 12) or 3)), format="%d years")
    target = years * 12
    needed = E.budget_for_target(debts, target, profile.strategy, profile.custom_order)

    if needed is None:
        st.warning(f"Clearing this debt in {years} year{'s' if years > 1 else ''} isn't reachable "
                   "even by paying the entire balance immediately — the accounts have longer "
                   "required terms than that.")
    else:
        gap = needed - budget
        solved = E.simulate(debts, needed, strategy=profile.strategy,
                            custom_order=profile.custom_order)
        stat_row([
            ("Payment needed", f"{money(needed)}/mo",
             f"{'+' if gap >= 0 else '−'}{money(abs(gap))} vs your {money(budget)}",
             "warning" if gap > 0 else "good"),
            ("Interest you'd pay", money(solved.total_interest),
             f"vs {money(base.total_interest)} today",
             "good" if solved.total_interest < base.total_interest else ""),
            ("Total cost", money(solved.total_paid), f"over {duration(solved.months)}"),
        ])
        if gap > 0:
            st.info(f"That's **{money(gap / 30, cents=True)} a day** more than you're paying now.")

    # ----------------------------------------------------- strategy bake-off
    st.divider()
    st.markdown("#### Strategy bake-off")
    st.caption("Same budget, same debts — only the *order* changes. The gap between the bars is "
               "money you get for free just by re-aiming your payments.")

    plans = E.compare_strategies(debts, budget, custom_order=profile.custom_order)
    rows = []
    for k, s in plans.items():
        rows.append({
            "Strategy": E.STRATEGY_LABELS[k],
            "Debt-free in": "never" if s.never_pays_off else duration(s.months),
            "Total interest": f"${s.total_interest:,.2f}",
            "Total paid": f"${s.total_paid:,.2f}",
        })
    chart(charts.strategy_comparison(plans, dark, highlight=profile.strategy),
          pd.DataFrame(rows), key="bakeoff", table_label="Compare every strategy")
    chart(charts.plan_race({E.STRATEGY_LABELS[k]: s for k, s in plans.items()}, dark),
          key="race_all")

    # ----------------------------------------------- extra-payment sensitivity
    st.divider()
    st.markdown("#### What each extra dollar buys")
    curve = E.sensitivity_curve(debts, budget, strategy=profile.strategy,
                                custom_order=profile.custom_order)
    tbl = curve.copy()
    tbl["extra"] = tbl["extra"].map(lambda v: f"+${v:,.0f}/mo")
    tbl["months"] = tbl["months"].map(lambda m: duration(int(m)) if pd.notna(m) else "never")
    tbl["months_saved"] = tbl["months_saved"].map(
        lambda m: duration(int(m)) if pd.notna(m) else "—")
    for c in ("interest", "interest_saved"):
        tbl[c] = tbl[c].map(lambda v: f"${v:,.2f}")
    tbl.columns = ["Extra payment", "Debt-free in", "Total interest", "Interest saved",
                   "Time saved"]
    chart(charts.sensitivity(curve, dark), tbl, key="sens",
          table_label="View the full sensitivity table")
