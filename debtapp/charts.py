"""Plotly figures.

House rules, applied consistently: thin marks, hairline grid, 2px surface gaps
between stacked fills, a legend whenever there are two or more series, selective
direct labels (endpoint or extreme only — never a number on every point), and
one y-axis per plot. Every chart in the app also ships a table view alongside it.

Titles are *not* drawn by Plotly. Plotly lays the title and a horizontal legend
into the same top margin, so the two crowd each other at any height that fits on
a dashboard. Each figure instead carries its title on ``layout.meta`` and
``ui.common.chart`` renders it as the card header above the plot, which leaves
the whole top margin to the legend.
"""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd
import plotly.graph_objects as go

from . import engine as E
from .theme import LEGEND_MARGIN, palette, register_template, series_color

GAP = 2  # px of surface showing between adjacent fills


def _fig(dark: bool, title: str, height: int = 300, subtitle: str = "",
         legend: bool = True) -> go.Figure:
    """A themed, empty figure. `title`/`subtitle` ride on ``meta`` for the card
    header; `legend=False` reclaims the top margin the legend would have used."""
    fig = go.Figure()
    fig.update_layout(template=register_template(dark), height=height,
                      meta={"title": title, "subtitle": subtitle},
                      margin_t=LEGEND_MARGIN if legend else 8)
    return fig


def _money_axis(fig: go.Figure) -> go.Figure:
    fig.update_yaxes(tickprefix="$", separatethousands=True)
    return fig


def _no_data(dark: bool, title: str, msg: str = "Add a debt to see this chart") -> go.Figure:
    fig = _fig(dark, title, height=150, legend=False)
    fig.add_annotation(text=msg, showarrow=False, xref="paper", yref="paper", x=0.5, y=0.5,
                       font=dict(color=palette(dark)["muted"], size=13))
    fig.update_xaxes(visible=False)
    fig.update_yaxes(visible=False)
    return fig


# --------------------------------------------------------------- balance curve

def balance_projection(schedule: E.Schedule, dark: bool, debt_order: Sequence[str] | None = None) -> go.Figure:
    """Stacked area: what you owe, month by month, until it's gone."""
    p = palette(dark)
    if schedule.ledger.empty:
        return _no_data(dark, "Balance projection")

    wide = schedule.ledger.pivot_table(index="date", columns="debt", values="end_balance",
                                       aggfunc="sum").fillna(0.0)
    names = list(debt_order) if debt_order else list(wide.columns)
    names = [n for n in names if n in wide.columns]

    # Past eight debts, the tail folds into "Other" rather than inventing hues.
    if len(names) > 8:
        head, tail = names[:7], names[7:]
        wide["Other"] = wide[tail].sum(axis=1)
        names = head + ["Other"]

    fig = _fig(dark, "Balance projection", height=330,
               subtitle="What you still owe, month by month, until it's gone.")
    for i, name in enumerate(names):
        color = p["muted"] if name == "Other" else series_color(i, dark)
        fig.add_trace(go.Scatter(
            x=wide.index, y=wide[name], name=name, mode="lines",
            stackgroup="one", fillcolor=color, line=dict(width=GAP, color=p["surface"]),
            hovertemplate="%{fullData.name}: $%{y:,.0f}<extra></extra>",
        ))

    # Direct-label the debt-free date rather than numbering every point. The
    # label lands wherever the last wedge happens to be, so it carries its own
    # surface behind it instead of relying on the fill underneath being pale.
    if not schedule.never_pays_off and schedule.payoff_date:
        fig.add_annotation(
            x=wide.index[-1], y=0, yshift=20, text=f"debt-free {schedule.payoff_date:%b %Y}",
            showarrow=False, xanchor="right", font=dict(color=p["good"], size=12),
            bgcolor=p["surface"], borderpad=3,
        )
    return _money_axis(fig)


# ------------------------------------------------------- principal vs interest

def dollar_split_meter(schedule: E.Schedule, dark: bool, horizon: int = 12) -> go.Figure:
    """A single 100% bar: of every dollar you pay this year, how much is interest."""
    p = palette(dark)
    split = E.where_the_money_goes(schedule, horizon)
    total = split["payment"]
    if total <= 0:
        return _no_data(dark, "Every dollar you pay")

    ip = 100 * split["interest"] / total
    fig = _fig(dark, "Every dollar you pay", height=132,
               subtitle=f"Where each dollar lands over the next {horizon} months.")
    for label, value, color in (("Principal", 100 - ip, p["principal"]),
                                ("Interest", ip, p["interest"])):
        fig.add_trace(go.Bar(
            x=[value], y=[""], name=label, orientation="h", marker_color=color,
            marker_line=dict(width=GAP, color=p["surface"]),
            marker_cornerradius=4,
            hovertemplate=f"{label}: %{{x:.0f}}¢ of every dollar<extra></extra>",
        ))
    fig.update_layout(barmode="stack", hovermode="closest", showlegend=True,
                      margin=dict(l=4, r=4, t=LEGEND_MARGIN, b=26), bargap=0.62)
    fig.update_xaxes(range=[0, 100], ticksuffix="¢", showgrid=False)
    fig.update_yaxes(visible=False)
    # Only label the segment if it fits comfortably inside the bar.
    if ip >= 14:
        fig.add_annotation(x=100 - ip / 2, y=0, text=f"{ip:.0f}¢ interest", showarrow=False,
                           font=dict(color="#ffffff", size=13))
    return fig


# ---------------------------------------------------------------- yearly bars

def yearly_interest(schedule: E.Schedule, dark: bool) -> go.Figure:
    """Interest paid per calendar year — the number people never see."""
    p = palette(dark)
    y = schedule.yearly_interest()
    if y.empty:
        return _no_data(dark, "Interest by year")

    fig = _fig(dark, "Interest paid, by year", height=285,
               subtitle="The number a statement never puts in front of you.")
    fig.add_trace(go.Bar(
        x=y["year"], y=y["principal"], name="Principal", marker_color=p["principal"],
        marker_line=dict(width=GAP, color=p["surface"]), marker_cornerradius=4,
        hovertemplate="Principal: $%{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=y["year"], y=y["interest"], name="Interest", marker_color=p["interest"],
        marker_line=dict(width=GAP, color=p["surface"]), marker_cornerradius=4,
        hovertemplate="Interest: $%{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(barmode="stack", bargap=0.35)
    fig.update_xaxes(type="category", title_text="")
    return _money_axis(fig)


# --------------------------------------------------------- strategy comparison

def strategy_comparison(schedules: dict[str, E.Schedule], dark: bool,
                        highlight: str = E.AVALANCHE) -> go.Figure:
    """Total interest by strategy. One hue, the winner emphasised — the
    categories are nominal, so a value ramp would be double-encoding."""
    p = palette(dark)
    rows = [{"strategy": E.STRATEGY_LABELS.get(k, k), "key": k,
             "interest": s.total_interest,
             "months": None if s.never_pays_off else s.months}
            for k, s in schedules.items()]
    if not rows:
        return _no_data(dark, "Strategy comparison")
    df = pd.DataFrame(rows).sort_values("interest", ascending=True)

    colors = [p["principal"] if k == highlight else p["muted"] for k in df["key"]]
    fig = _fig(dark, "What each payoff strategy costs you", height=250, legend=False)
    fig.add_trace(go.Bar(
        x=df["interest"], y=df["strategy"], orientation="h", marker_color=colors,
        marker_cornerradius=4, marker_line=dict(width=GAP, color=p["surface"]),
        text=[f"  ${v:,.0f}" for v in df["interest"]], textposition="outside",
        textfont=dict(color=p["text_secondary"], size=12),
        customdata=df["months"],
        hovertemplate="%{y}<br>$%{x:,.0f} interest<extra></extra>",
        showlegend=False,
    ))
    fig.update_layout(hovermode="closest", bargap=0.4,
                      margin=dict(l=4, r=92, t=8, b=4))
    fig.update_xaxes(tickprefix="$", separatethousands=True, showgrid=True,
                     gridcolor=p["grid"])
    fig.update_yaxes(showgrid=False)
    return fig


# ------------------------------------------------------------ extra-payment curve

def sensitivity(df: pd.DataFrame, dark: bool) -> go.Figure:
    """Interest saved as a function of the extra you send each month."""
    p = palette(dark)
    if df.empty:
        return _no_data(dark, "Extra payments")

    fig = _fig(dark, "What each extra dollar buys you", height=280, legend=False,
               subtitle="Interest saved against the extra you send every month.")
    fig.add_trace(go.Scatter(
        x=df["extra"], y=df["interest_saved"], mode="lines+markers",
        name="Interest saved", line=dict(width=2, color=p["good"]),
        marker=dict(size=8, color=p["good"], line=dict(width=GAP, color=p["surface"])),
        hovertemplate="+$%{x:,.0f}/mo → $%{y:,.0f} saved<extra></extra>",
        showlegend=False,
    ))
    # Label the extreme only.
    best = df.iloc[-1]
    fig.add_annotation(x=best["extra"], y=best["interest_saved"],
                       text=f"${best['interest_saved']:,.0f}", showarrow=False,
                       xanchor="right", yshift=16, font=dict(color=p["good"], size=12))
    fig.update_xaxes(tickprefix="+$", title_text="Extra payment per month",
                     title_font=dict(size=12, color=p["muted"]))
    return _money_axis(fig)


# ------------------------------------------------------------ payoff timeline

def payoff_timeline(schedule: E.Schedule, dark: bool) -> go.Figure:
    """When each account disappears."""
    p = palette(dark)
    if not schedule.payoff_month:
        return _no_data(dark, "Payoff timeline",
                        "Nothing pays off under this plan — raise your budget")

    items = sorted(schedule.payoff_month.items(), key=lambda kv: kv[1])
    names = [n for n, _ in items]
    months = [m for _, m in items]
    order = {n: i for i, n in enumerate(schedule.ledger["debt"].unique())}
    colors = [series_color(order.get(n, 0), dark) for n in names]

    fig = _fig(dark, "When each account disappears", legend=False,
               height=max(150, 44 * len(names) + 62))
    fig.add_trace(go.Bar(
        x=months, y=names, orientation="h", marker_color=colors, marker_cornerradius=4,
        marker_line=dict(width=GAP, color=p["surface"]),
        text=[f"  {m // 12}y {m % 12}m" if m >= 12 else f"  {m}m" for m in months],
        textposition="outside", textfont=dict(color=p["text_secondary"], size=12),
        hovertemplate="%{y} paid off in month %{x}<extra></extra>", showlegend=False,
    ))
    fig.update_layout(hovermode="closest", bargap=0.42, margin=dict(l=4, r=72, t=8, b=30))
    fig.update_xaxes(title_text="Months from today", showgrid=True, gridcolor=p["grid"],
                     title_font=dict(size=12, color=p["muted"]))
    fig.update_yaxes(autorange="reversed", showgrid=False)
    return fig


# ------------------------------------------------------------- per-debt cost

def interest_by_debt(schedule: E.Schedule, dark: bool) -> go.Figure:
    """Lifetime interest, per account — usually a surprise."""
    p = palette(dark)
    t = schedule.per_debt_totals()
    if t.empty:
        return _no_data(dark, "Interest by debt")
    t = t.sort_values("interest", ascending=True)
    order = {n: i for i, n in enumerate(schedule.ledger["debt"].unique())}

    fig = _fig(dark, "Lifetime interest, by account", legend=False,
               height=max(150, 44 * len(t) + 62))
    fig.add_trace(go.Bar(
        x=t["interest"], y=t["debt"], orientation="h",
        marker_color=[series_color(order.get(n, 0), dark) for n in t["debt"]],
        marker_cornerradius=4, marker_line=dict(width=GAP, color=p["surface"]),
        text=[f"  ${v:,.0f}" for v in t["interest"]], textposition="outside",
        textfont=dict(color=p["text_secondary"], size=12),
        hovertemplate="%{y}: $%{x:,.0f} in interest<extra></extra>", showlegend=False,
    ))
    fig.update_layout(hovermode="closest", bargap=0.4, margin=dict(l=4, r=92, t=8, b=4))
    fig.update_xaxes(tickprefix="$", separatethousands=True, showgrid=True, gridcolor=p["grid"])
    fig.update_yaxes(showgrid=False)
    return fig


# ----------------------------------------------------------------- comparison

def plan_race(plans: dict[str, E.Schedule], dark: bool) -> go.Figure:
    """Total balance over time under competing plans — one axis, indexed to the
    same starting balance by construction."""
    p = palette(dark)
    fig = _fig(dark, "How fast the balance falls", height=300)
    drawn = 0
    for i, (label, s) in enumerate(plans.items()):
        if s.monthly.empty:
            continue
        fig.add_trace(go.Scatter(
            x=s.monthly["month"], y=s.monthly["balance"], name=label, mode="lines",
            line=dict(width=2, color=series_color(i, dark)),
            hovertemplate="%{fullData.name}: $%{y:,.0f}<extra></extra>",
        ))
        drawn += 1
    if not drawn:
        return _no_data(dark, "How fast the balance falls")
    fig.update_xaxes(title_text="Months from today",
                     title_font=dict(size=12, color=p["muted"]))
    return _money_axis(fig)


# ------------------------------------------------------------- the real ledger

def ledger_cumulative(cum: pd.DataFrame, dark: bool) -> go.Figure:
    """Money you have *actually* paid, split principal vs interest.

    Everything else in the app projects forward; this is recorded history. The
    title and subtitle say so outright, because an area chart that looks like
    the Plan page's but means something else is how a user stops trusting both.
    """
    p = palette(dark)
    if cum.empty:
        return _no_data(dark, "What you've paid so far",
                        "Log a payment and your real history starts here")

    fig = _fig(dark, "What you've actually paid", height=300,
               subtitle="Recorded payments only — this is history, not a projection.")
    for col, name, color in (("cum_principal", "Principal (reduced your balance)", p["principal"]),
                             ("cum_interest", "Interest (kept by the lender)", p["interest"])):
        fig.add_trace(go.Scatter(
            x=cum["date"], y=cum[col], name=name, mode="lines", stackgroup="one",
            fillcolor=color, line=dict(width=GAP, color=p["surface"]),
            hovertemplate="%{fullData.name}: $%{y:,.0f}<extra></extra>",
        ))

    last = cum.iloc[-1]
    fig.add_annotation(x=last["date"], y=last["cum_paid"],
                       text=f"${last['cum_interest']:,.0f} to interest", showarrow=False,
                       xanchor="right", yshift=14, font=dict(color=p["interest"], size=12),
                       bgcolor=p["surface"], borderpad=3)
    return _money_axis(fig)


def payments_by_month(monthly: pd.DataFrame, dark: bool) -> go.Figure:
    """Every month you've logged, and how much of it the lender kept."""
    p = palette(dark)
    if monthly.empty:
        return _no_data(dark, "Your payments by month", "Log a payment to start your history")

    fig = _fig(dark, "Your payments, month by month", height=285,
               subtitle="Every month you've logged, and how much of it the lender kept.")
    fig.add_trace(go.Bar(
        x=monthly["month"], y=monthly["principal"], name="Principal",
        marker_color=p["principal"], marker_line=dict(width=GAP, color=p["surface"]),
        marker_cornerradius=4, hovertemplate="Principal: $%{y:,.0f}<extra></extra>",
    ))
    fig.add_trace(go.Bar(
        x=monthly["month"], y=monthly["interest"], name="Interest",
        marker_color=p["interest"], marker_line=dict(width=GAP, color=p["surface"]),
        marker_cornerradius=4, hovertemplate="Interest: $%{y:,.0f}<extra></extra>",
    ))
    fig.update_layout(barmode="stack", bargap=0.35)
    fig.update_xaxes(type="category", title_text="")
    return _money_axis(fig)


def progress_history(snapshots: list[dict], dark: bool) -> go.Figure:
    """Total balance at each save — the user's actual trajectory over sessions."""
    p = palette(dark)
    if len(snapshots) < 2:
        return _no_data(dark, "Your progress",
                        "Come back and update your balances to start tracking progress")
    df = pd.DataFrame(snapshots)
    df["taken_at"] = pd.to_datetime(df["taken_at"], format="mixed", utc=True)

    first, last = df["total_balance"].iloc[0], df["total_balance"].iloc[-1]
    color = p["good"] if last < first else p["interest"]
    fig = _fig(dark, "Your balance at every check-in", height=260, legend=False,
               subtitle="The real curve, not the projected one.")
    fig.add_trace(go.Scatter(
        x=df["taken_at"], y=df["total_balance"], mode="lines+markers", name="Total balance",
        line=dict(width=2, color=color),
        marker=dict(size=8, color=color, line=dict(width=GAP, color=p["surface"])),
        hovertemplate="$%{y:,.0f}<extra></extra>", showlegend=False,
    ))
    fig.add_annotation(x=df["taken_at"].iloc[-1], y=last,
                       text=f"{'−' if last < first else '+'}${abs(last - first):,.0f}",
                       showarrow=False, xanchor="right", yshift=16,
                       font=dict(color=color, size=12))
    return _money_axis(fig)
