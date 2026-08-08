"""Plotly figures.

House rules, applied consistently: thin marks, hairline grid, 2px surface gaps
between stacked fills, a legend whenever there are two or more series, selective
direct labels (endpoint or extreme only — never a number on every point), and
one y-axis per plot. Every chart in the app also ships a table view alongside it.
"""

from __future__ import annotations

from typing import Optional, Sequence

import pandas as pd
import plotly.graph_objects as go

from . import engine as E
from .theme import palette, register_template, series_color

GAP = 2  # px of surface showing between adjacent fills


def _fig(dark: bool, title: str, height: int = 340) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(template=register_template(dark), title_text=title, height=height)
    return fig


def _money_axis(fig: go.Figure) -> go.Figure:
    fig.update_yaxes(tickprefix="$", separatethousands=True)
    return fig


def _no_data(dark: bool, title: str, msg: str = "Add a debt to see this chart") -> go.Figure:
    fig = _fig(dark, title, height=200)
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

    fig = _fig(dark, "Balance projection — what you still owe", height=380)
    for i, name in enumerate(names):
        color = p["muted"] if name == "Other" else series_color(i, dark)
        fig.add_trace(go.Scatter(
            x=wide.index, y=wide[name], name=name, mode="lines",
            stackgroup="one", fillcolor=color, line=dict(width=GAP, color=p["surface"]),
            hovertemplate="%{fullData.name}: $%{y:,.0f}<extra></extra>",
        ))

    # Direct-label the debt-free date rather than numbering every point.
    if not schedule.never_pays_off and schedule.payoff_date:
        fig.add_annotation(
            x=wide.index[-1], y=0, yshift=18, text=f"debt-free {schedule.payoff_date:%b %Y}",
            showarrow=False, xanchor="right", font=dict(color=p["good"], size=12),
        )
    return _money_axis(fig)


# ------------------------------------------------------- principal vs interest

def principal_vs_interest(schedule: E.Schedule, dark: bool) -> go.Figure:
    """Cumulative split of every dollar paid."""
    p = palette(dark)
    if schedule.monthly.empty:
        return _no_data(dark, "Where your payments go")

    m = schedule.monthly.copy()
    m["cum_principal"] = m["principal"].cumsum()
    m["cum_interest"] = m["interest"].cumsum()

    fig = _fig(dark, "Where your payments go, cumulatively", height=340)
    for col, name, color in (("cum_principal", "Principal (reduces your balance)", p["principal"]),
                             ("cum_interest", "Interest (kept by the lender)", p["interest"])):
        fig.add_trace(go.Scatter(
            x=m["date"], y=m[col], name=name, mode="lines", stackgroup="one",
            fillcolor=color, line=dict(width=GAP, color=p["surface"]),
            hovertemplate="%{fullData.name}: $%{y:,.0f}<extra></extra>",
        ))

    last = m.iloc[-1]
    fig.add_annotation(x=last["date"], y=last["cum_principal"] + last["cum_interest"],
                       text=f"${last['cum_interest']:,.0f} interest", showarrow=False,
                       xanchor="right", yshift=14, font=dict(color=p["interest"], size=12))
    return _money_axis(fig)


def dollar_split_meter(schedule: E.Schedule, dark: bool, horizon: int = 12) -> go.Figure:
    """A single 100% bar: of every dollar you pay this year, how much is interest."""
    p = palette(dark)
    split = E.where_the_money_goes(schedule, horizon)
    total = split["payment"]
    if total <= 0:
        return _no_data(dark, "Every dollar you pay")

    ip = 100 * split["interest"] / total
    fig = _fig(dark, f"Every dollar you pay over the next {horizon} months", height=190)
    for label, value, color in (("Principal", 100 - ip, p["principal"]),
                                ("Interest", ip, p["interest"])):
        fig.add_trace(go.Bar(
            x=[value], y=[""], name=label, orientation="h", marker_color=color,
            marker_line=dict(width=GAP, color=p["surface"]),
            marker_cornerradius=4,
            hovertemplate=f"{label}: %{{x:.0f}}¢ of every dollar<extra></extra>",
        ))
    fig.update_layout(barmode="stack", hovermode="closest", showlegend=True,
                      margin=dict(l=8, r=8, t=48, b=28), bargap=0.55)
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

    fig = _fig(dark, "Interest paid, by year", height=320)
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
    fig = _fig(dark, "What each payoff strategy costs you", height=300)
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
                      margin=dict(l=8, r=90, t=48, b=8))
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

    fig = _fig(dark, "What each extra dollar per month buys you", height=320)
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

    fig = _fig(dark, "When each account disappears", height=max(200, 46 * len(names) + 90))
    fig.add_trace(go.Bar(
        x=months, y=names, orientation="h", marker_color=colors, marker_cornerradius=4,
        marker_line=dict(width=GAP, color=p["surface"]),
        text=[f"  {m // 12}y {m % 12}m" if m >= 12 else f"  {m}m" for m in months],
        textposition="outside", textfont=dict(color=p["text_secondary"], size=12),
        hovertemplate="%{y} paid off in month %{x}<extra></extra>", showlegend=False,
    ))
    fig.update_layout(hovermode="closest", bargap=0.42, margin=dict(l=8, r=70, t=48, b=32))
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

    fig = _fig(dark, "Lifetime interest, by account", height=max(200, 44 * len(t) + 90))
    fig.add_trace(go.Bar(
        x=t["interest"], y=t["debt"], orientation="h",
        marker_color=[series_color(order.get(n, 0), dark) for n in t["debt"]],
        marker_cornerradius=4, marker_line=dict(width=GAP, color=p["surface"]),
        text=[f"  ${v:,.0f}" for v in t["interest"]], textposition="outside",
        textfont=dict(color=p["text_secondary"], size=12),
        hovertemplate="%{y}: $%{x:,.0f} in interest<extra></extra>", showlegend=False,
    ))
    fig.update_layout(hovermode="closest", bargap=0.4, margin=dict(l=8, r=90, t=48, b=8))
    fig.update_xaxes(tickprefix="$", separatethousands=True, showgrid=True, gridcolor=p["grid"])
    fig.update_yaxes(showgrid=False)
    return fig


# ----------------------------------------------------------------- comparison

def plan_race(plans: dict[str, E.Schedule], dark: bool) -> go.Figure:
    """Total balance over time under competing plans — one axis, indexed to the
    same starting balance by construction."""
    p = palette(dark)
    fig = _fig(dark, "How fast the balance falls", height=340)
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
    fig = _fig(dark, "Your total balance, every time you've checked in", height=300)
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
