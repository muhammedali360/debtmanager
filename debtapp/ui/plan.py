"""Your plan — what you owe, what to do about it, and when it ends.

One page, deliberately. This was three (Dashboard, Insights, What if…) and every
one of them rendered the same :func:`build_plan` call — as tiles, as ranked
prose, and as sliders. Three views of one simulation asked the user to choose a
rendering before they knew what any of them said, and the choice carried no
information because the answer was the same on all three.

The collapse follows the same logic all the way down. The suggestion list is
ranked by dollars at stake, so showing twenty of them spends the ranking: the
top card is the page, the rest are a drawer. The what-if had six sliders, five
of which asked variations of "and if you paid a bit more?", so it is one slider.
Four of the six charts said "interest is expensive and this account clears
first", which the tiles already say in words, so they are behind *More detail*.
"""

from __future__ import annotations

import html

import pandas as pd
import streamlit as st

from .. import charts, db
from .. import engine as E
from ..insights import SEVERITY_ICON, Insight, generate
from . import ledger
from .common import (active_debts, banner, build_plan, caption, chart, current_user, duration,
                     effective_budget, esc_html, is_dark, money, needs_debts,
                     open_recommendation, page_header, persist, section, stat_row, toast,
                     user_payments)

_EXECUTABLE = {"accounts", "plan_extra", "plan_budget", "plan_strategy"}


def _money_cols(df: pd.DataFrame, cols) -> pd.DataFrame:
    for c in cols:
        df[c] = df[c].map(lambda v: f"${v:,.2f}")
    return df


def _schedule_table(schedule: E.Schedule) -> pd.DataFrame:
    """The table-view twin for the projection chart."""
    if schedule.monthly.empty:
        return pd.DataFrame()
    m = schedule.monthly.copy()
    m["Month"] = m["date"].dt.strftime("%b %Y")
    out = m[["Month", "payment", "interest", "principal", "balance"]].copy()
    out.columns = ["Month", "Payment", "Interest", "Principal", "Balance remaining"]
    return _money_cols(out, out.columns[1:])


# ----------------------------------------------------------------- suggestions

def _md(text: str) -> str:
    """Insight bodies: escaped inline markdown, split into paragraphs.

    Escaping matters even though none of this is user-authored — it keeps a debt
    the user named `<b>` from rewriting the page.
    """
    paras = (p.strip().replace("\n", " ") for p in esc_html(text).split("\n\n"))
    return "".join(f"<p>{p}</p>" for p in paras if p)


def _card(ins: Insight) -> str:
    metric = f'<div class="ins-metric">{html.escape(ins.metric)}</div>' if ins.metric else ""
    action = (f'<div class="ins-action"><b>Do this:</b> {_md(ins.action)}</div>'
              if ins.action else "")
    # The action block already wraps in <p>; unwrap so it stays on one line.
    action = action.replace("<p>", "").replace("</p>", " ")
    return (
        f'<div class="ins {ins.severity}">'
        f'<div class="ins-head">{SEVERITY_ICON[ins.severity]} '
        f'<span class="ins-title">{html.escape(ins.title)}</span>{metric}</div>'
        f'<div class="ins-body">{_md(ins.body)}</div>{action}</div>'
    )


def _suggestions(found: list[Insight]) -> None:
    """The top-ranked move, with the rest folded away behind it."""
    if not found:
        return
    section("What to do next",
            "The single move with the most money behind it, worked out from your own numbers.")
    lead = found[0]
    st.markdown(_card(lead), unsafe_allow_html=True)
    if lead.action_type in _EXECUTABLE:
        if st.button(lead.action_label or "Set this up", key="plan_rec_0", type="primary"):
            open_recommendation(lead)

    rest = found[1:]
    if not rest:
        return
    with st.expander(f"More suggestions ({len(rest)})"):
        caption("Ranked the same way — by how many dollars are on the table.")
        for n, insight in enumerate(rest, start=1):
            st.markdown(_card(insight), unsafe_allow_html=True)
            if insight.action_type in _EXECUTABLE:
                if st.button(insight.action_label or "Set this up", key=f"plan_rec_{n}"):
                    open_recommendation(insight)


# --------------------------------------------------------------------- what if

def _what_if(debts, profile, base: E.Schedule, dark: bool) -> None:
    """One lever.

    The old page had six, but "one-time payment", "applied in month N", "annual
    raise" and the target-date solver are all the same question — *what does
    more money buy?* — asked in units most people cannot act on this month. The
    monthly figure is the one they can.
    """
    section("What if you paid more?")
    preset = min(1_000, max(0, int(st.session_state.get("plan_extra", 0))))
    extra = st.slider("Extra each month", 0, 1_000, preset, step=25, format="$%d",
                      key="plan_extra",
                      help="On top of what you already pay. Preview it first, then save it as "
                           "your monthly plan if it works for you.")
    if extra <= 0:
        caption("Drag the slider to price a bigger payment in months and dollars.")
        return

    alt = E.simulate(debts, effective_budget(debts, profile) + extra, strategy=profile.strategy,
                     custom_order=profile.custom_order)
    if base.never_pays_off or alt.never_pays_off:
        stat_row([
            ("Debt-free", "Never" if alt.never_pays_off else alt.payoff_date.strftime("%b %Y"),
             f"with {money(extra)}/mo more",
             "critical" if alt.never_pays_off else "good"),
        ])
        if alt.never_pays_off:
            banner("warning", f"Even with {money(extra)} a month more the balance never clears. "
                              "The interest is outrunning the payment — the fix is a lower rate "
                              "or a much larger payment, not a nudge.")
        return

    saved = base.total_interest - alt.total_interest
    stat_row([
        ("New debt-free date", alt.payoff_date.strftime("%b %Y"),
         f"was {base.payoff_date.strftime('%b %Y')}", "good"),
        ("Time saved", duration(base.months - alt.months), "off your timeline", "good"),
        ("Interest saved", money(saved), f"of {money(base.total_interest)}", "good"),
    ])
    caption(f"That's {money(extra / 30, cents=True)} a day.")
    chart(charts.plan_race({"Your plan today": base, f"With +{money(extra)}/mo": alt}, dark),
          key="race")
    if st.button("Use this monthly plan", key="save_extra", type="primary"):
        profile.monthly_budget = effective_budget(debts, profile) + extra
        persist(current_user(), st.session_state.debts, profile)
        st.session_state["reset_plan_extra"] = True
        toast(f"Monthly plan updated to {money(profile.monthly_budget)}.")
        st.rerun()


def _settings(debts, profile) -> None:
    """The two durable assumptions, kept together and out of the main flow."""
    recommended_budget = st.session_state.pop("recommended_budget", None)
    recommended_strategy = st.session_state.pop("recommended_strategy", None)
    current_budget = effective_budget(debts, profile)
    budget_default = float(recommended_budget if recommended_budget is not None
                           else current_budget)
    strategy_default = str(recommended_strategy or profile.strategy)
    choices = list(E.STRATEGY_LABELS)
    if strategy_default not in choices:
        strategy_default = profile.strategy

    with st.expander("Plan settings", expanded=recommended_budget is not None or
                     recommended_strategy is not None):
        caption("These are saved to your account and drive every projection.")
        with st.form("plan_settings_form"):
            left, right = st.columns(2)
            budget = left.number_input("Monthly debt budget", min_value=0.0, step=25.0,
                                       value=budget_default)
            strategy = right.selectbox(
                "Payoff strategy", choices, index=choices.index(strategy_default),
                format_func=lambda value: E.STRATEGY_LABELS[value],
            )
            save = st.form_submit_button("Save plan settings", type="primary")
        if save:
            profile.monthly_budget = budget
            profile.strategy = strategy
            persist(current_user(), st.session_state.debts, profile)
            toast("Plan settings saved.")
            st.rerun()


# ----------------------------------------------------------------- more detail

def _more_detail(debts, profile, plan: E.Schedule, budget: float, dark: bool) -> None:
    """Everything that is true but not decision-changing on first read."""
    with st.expander("More detail"):
        left, right = st.columns([1, 1])
        with left:
            chart(charts.dollar_split_meter(plan, dark, 12), key="meter")
        with right:
            y = plan.yearly_interest()
            tbl = y.copy()
            if not tbl.empty:
                tbl.columns = ["Year", "Interest", "Principal"]
                tbl = _money_cols(tbl, ("Interest", "Principal"))
            chart(charts.yearly_interest(plan, dark), tbl, key="yearly",
                  table_label="View yearly totals")

        left, right = st.columns([1, 1])
        with left:
            chart(charts.payoff_timeline(plan, dark), key="timeline")
        with right:
            tbl = plan.per_debt_totals().copy()
            if not tbl.empty:
                tbl["payoff_month"] = tbl["payoff_month"].map(
                    lambda m: duration(int(m)) if pd.notna(m) else "never")
                tbl = _money_cols(tbl, ("interest", "principal", "payment"))
                tbl.columns = ["Account", "Interest", "Principal", "Total paid", "Paid off in"]
            chart(charts.interest_by_debt(plan, dark), tbl, key="bydebt",
                  table_label="View per-account totals")

        # Kept from the old What-if page because it is the one comparison that
        # is free money: same budget, same debts, only the order changes.
        plans = E.compare_strategies(debts, budget, custom_order=profile.custom_order)
        rows = [{
            "Strategy": E.STRATEGY_LABELS[k],
            "Debt-free in": "never" if s.never_pays_off else duration(s.months),
            "Total interest": f"${s.total_interest:,.2f}",
            "Total paid": f"${s.total_paid:,.2f}",
        } for k, s in plans.items()]
        chart(charts.strategy_comparison(plans, dark, highlight=profile.strategy),
              pd.DataFrame(rows), key="bakeoff", table_label="Compare every strategy")

        snaps = db.load_snapshots(current_user())
        if len(snaps) >= 2:
            tbl = pd.DataFrame(snaps)[["taken_at", "total_balance", "blended_apr", "debt_count"]]
            tbl["taken_at"] = pd.to_datetime(tbl["taken_at"], format="mixed",
                                             utc=True).dt.strftime("%d %b %Y")
            tbl["total_balance"] = tbl["total_balance"].map(lambda v: f"${v:,.2f}")
            tbl["blended_apr"] = tbl["blended_apr"].map(lambda v: f"{v:.2f}%")
            tbl.columns = ["Date", "Total balance", "Blended APR", "Accounts"]
            chart(charts.progress_history(snaps, dark), tbl, key="hist",
                  table_label="View check-in history")


# --------------------------------------------------------------------- the page

def render() -> None:
    if needs_debts():
        return

    # Widget state may only be changed before its widget is instantiated. The
    # save click sets this flag on the previous run, so reset the preview here.
    if st.session_state.pop("reset_plan_extra", False):
        st.session_state["plan_extra"] = 0

    dark = is_dark()
    debts = active_debts()
    profile = st.session_state.profile
    budget = effective_budget(debts, profile)
    plan = build_plan(debts, profile)

    total = sum(d.balance for d in debts)
    daily = sum(d.daily_interest for d in debts)

    page_header(
        "Your plan",
        f"Projected on {E.STRATEGY_LABELS[plan.strategy].lower()} at "
        f"{money(budget)}/month. Change either on the My debts page.",
    )

    # Three tiles, not five. "Blended APR" and "Interest per day" were both
    # restatements of the interest bill; the per-day figure is the one that
    # motivates, so it rides along as the subtitle rather than taking a tile.
    if plan.never_pays_off:
        payoff_val, payoff_sub, payoff_tone = "Never", "Payments don't outrun the interest", \
            "critical"
        interest_val, interest_sub, interest_tone = "Unbounded", "the balance grows", "critical"
    else:
        payoff_val = plan.payoff_date.strftime("%b %Y")
        payoff_sub = duration(plan.months)
        payoff_tone = ""
        interest_val = money(plan.total_interest)
        interest_sub = f"{plan.interest_share:.0%} of every dollar"
        interest_tone = "critical" if plan.interest_share > 0.3 else "warning"

    stat_row([
        ("Total owed", money(total),
         f"across {len(debts)} account{'s' if len(debts) > 1 else ''}"),
        ("Debt-free", payoff_val, payoff_sub, payoff_tone),
        ("Interest you'll pay", interest_val,
         f"{interest_sub} · {money(daily, cents=True)} a day right now", interest_tone),
    ])

    if plan.short_months:
        banner("error",
               f"Your budget of {money(budget)}/mo can't cover the required minimums for "
               f"**{plan.short_months}** month{'s' if plan.short_months > 1 else ''}. The "
               "projection splits what you have proportionally; real lenders would charge late "
               "fees and could raise your APR to ~30%. Call them before that happens.")

    # What's due beats what's projected — a payment you forget costs more than
    # any ordering decision below.
    ledger.due_panel(limit=4)

    with st.spinner("Running the numbers…"):
        found = generate(debts, profile, budget, user_payments())
    _suggestions(found)

    section("Where the money goes", "Every dollar between now and the last payment.")
    chart(charts.balance_projection(plan, dark, [d.name for d in debts]),
          _schedule_table(plan), key="proj", table_label="View month-by-month schedule")

    _what_if(debts, profile, plan, dark)
    _settings(debts, profile)
    _more_detail(debts, profile, plan, budget, dark)

    caption(
        "These projections assume monthly compounding, unchanging APRs, and no new borrowing. "
        "They're a planning tool, not financial advice — and if your minimums are unaffordable, "
        "a nonprofit credit counselor (NFCC member agencies are free) will beat any calculator."
    )
