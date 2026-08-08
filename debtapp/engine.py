"""Amortization engine.

Everything the app claims — payoff dates, interest totals, "what if you added
$100" — comes out of :func:`simulate`. It is deliberately the only place that
knows how a month works, so the numbers can never disagree with each other.

Month mechanics, in order:

1. Interest accrues on every balance at ``apr / 12``.
2. Each lender's required payment is computed *against the post-interest
   balance* (that is how card minimums actually work).
3. If the budget cannot cover the minimums, payments are pro-rated and the
   month is flagged as short.
4. Any surplus cascades down the strategy's priority list, capped at each
   debt's payoff amount, so freed-up minimums roll over automatically.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Callable, Iterable, Optional, Sequence

import pandas as pd

from .models import Debt

MAX_MONTHS = 1200  # 100 years — past this we call it "never"

AVALANCHE = "avalanche"
SNOWBALL = "snowball"
MINIMUM = "minimum"
CURRENT = "current"
CUSTOM = "custom"

STRATEGY_LABELS = {
    AVALANCHE: "Avalanche — highest APR first",
    SNOWBALL: "Snowball — smallest balance first",
    MINIMUM: "Minimums only",
    CURRENT: "What you pay today",
    CUSTOM: "Custom priority order",
}

STRATEGY_BLURBS = {
    AVALANCHE: "Mathematically optimal. Always the cheapest and usually the fastest.",
    SNOWBALL: "Clears individual accounts soonest. Costs more, but the wins come faster.",
    MINIMUM: "The default if you do nothing. Shown so you can see what it costs.",
    CURRENT: "Your existing payments, held flat, with freed-up money rolled forward.",
    CUSTOM: "Your own priority order.",
}


def _priority(debts: Sequence[Debt], strategy: str, custom_order: Sequence[int] | None) -> list[int]:
    """Return debt indices in the order surplus money should attack them."""
    idx = list(range(len(debts)))
    if strategy == AVALANCHE:
        return sorted(idx, key=lambda i: (-debts[i].apr, debts[i].balance))
    if strategy == SNOWBALL:
        return sorted(idx, key=lambda i: (debts[i].balance, -debts[i].apr))
    if strategy == CURRENT:
        # Surplus follows where the user already sends the most money.
        return sorted(idx, key=lambda i: (-debts[i].effective_payment(), -debts[i].apr))
    if strategy == CUSTOM and custom_order:
        by_name = {d.name: i for i, d in enumerate(debts)}
        ordered = [by_name[n] for n in custom_order if n in by_name]
        return ordered + [i for i in idx if i not in ordered]
    return idx


@dataclass
class Schedule:
    """The full month-by-month result of one plan."""

    ledger: pd.DataFrame  # tidy: one row per (month, debt)
    monthly: pd.DataFrame  # one row per month, totals
    strategy: str
    budget: float
    start_date: date
    payoff_month: dict = field(default_factory=dict)  # debt name -> month index
    never_pays_off: bool = False
    short_months: int = 0

    # ------------------------------------------------------------- summaries
    @property
    def months(self) -> int:
        return 0 if self.monthly.empty else int(self.monthly["month"].max())

    @property
    def total_interest(self) -> float:
        return float(self.monthly["interest"].sum()) if not self.monthly.empty else 0.0

    @property
    def total_paid(self) -> float:
        return float(self.monthly["payment"].sum()) if not self.monthly.empty else 0.0

    @property
    def total_principal(self) -> float:
        return self.total_paid - self.total_interest

    @property
    def payoff_date(self) -> Optional[date]:
        if self.never_pays_off or self.monthly.empty:
            return None
        return self.monthly["date"].iloc[-1].date()

    @property
    def interest_share(self) -> float:
        """Cents of every dollar paid that the lender keeps."""
        return 0.0 if self.total_paid <= 0 else self.total_interest / self.total_paid

    def yearly_interest(self) -> pd.DataFrame:
        if self.monthly.empty:
            return pd.DataFrame(columns=["year", "interest", "principal"])
        m = self.monthly.copy()
        m["year"] = m["date"].dt.year
        out = m.groupby("year", as_index=False)[["interest", "principal"]].sum()
        return out

    def per_debt_totals(self) -> pd.DataFrame:
        if self.ledger.empty:
            return pd.DataFrame(columns=["debt", "interest", "principal", "payment", "payoff_month"])
        g = self.ledger.groupby("debt", as_index=False)[["interest", "principal", "payment"]].sum()
        g["payoff_month"] = g["debt"].map(self.payoff_month)
        return g.sort_values("interest", ascending=False, ignore_index=True)


def _month_dates(start: date, n: int) -> list[pd.Timestamp]:
    base = pd.Timestamp(start.year, start.month, 1)
    return list(pd.date_range(base, periods=n, freq="MS"))


def simulate(
    debts: Sequence[Debt],
    budget: float = 0.0,
    strategy: str = AVALANCHE,
    custom_order: Sequence[str] | None = None,
    extra: float = 0.0,
    lump_sum: float = 0.0,
    lump_month: int = 1,
    start_date: Optional[date] = None,
    budget_growth: float = 0.0,
    max_months: int = MAX_MONTHS,
) -> Schedule:
    """Run a plan to completion.

    Args:
        budget: total dollars available per month. ``0`` (or anything below the
            required minimums) floats up to whatever the minimums are that month.
        extra: added on top of ``budget`` every month.
        lump_sum: one-time payment applied in ``lump_month``.
        budget_growth: annual % increase in the budget (raises, escaping lifestyle creep).
    """
    debts = [Debt.from_dict(d.to_dict()) for d in debts]  # never mutate the caller's
    if not debts:
        empty = pd.DataFrame(columns=["month", "date", "debt", "start_balance", "interest",
                                      "payment", "principal", "end_balance"])
        return Schedule(empty, pd.DataFrame(columns=["month", "date", "interest", "payment",
                                                     "principal", "balance"]),
                        strategy, budget, start_date or date.today())

    start_date = start_date or date.today()
    order = _priority(debts, strategy, custom_order)
    balances = [max(0.0, d.balance) for d in debts]

    rows: list[dict] = []
    payoff_month: dict[str, int] = {}
    short_months = 0
    month = 0
    base_budget = budget
    prev_total = round(sum(balances), 2)
    stalled = 0

    while month < max_months and any(b > 0.005 for b in balances):
        month += 1
        if budget_growth and month > 1 and (month - 1) % 12 == 0:
            base_budget *= 1 + budget_growth / 100.0

        interest = [0.0] * len(debts)
        due = [0.0] * len(debts)
        for i, d in enumerate(debts):
            if balances[i] <= 0.005:
                continue
            interest[i] = round(balances[i] * d.monthly_rate, 2)
            due[i] = round(d.required_payment(balances[i] + interest[i]), 2)

        required = round(sum(due), 2)
        # "Minimums only" floats with the (shrinking) required payment; every
        # other strategy holds a flat budget so freed-up money rolls forward.
        avail = required if strategy == MINIMUM else base_budget + extra
        if month == lump_month and lump_sum > 0:
            avail += lump_sum

        pay = [0.0] * len(debts)
        if avail < required - 0.005:
            # Can't even make minimums. Split what exists proportionally and flag it.
            short_months += 1
            scale = avail / required if required > 0 else 0.0
            pay = [round(x * scale, 2) for x in due]
            surplus = 0.0
        else:
            pay = list(due)
            surplus = round(avail - required, 2)
            for i in order:
                if surplus <= 0.005:
                    break
                room = round(balances[i] + interest[i] - pay[i], 2)
                if room <= 0:
                    continue
                add = min(surplus, room)
                pay[i] = round(pay[i] + add, 2)
                surplus = round(surplus - add, 2)

        for i, d in enumerate(debts):
            start_bal = balances[i]
            if start_bal <= 0.005 and pay[i] <= 0:
                continue
            end_bal = round(start_bal + interest[i] - pay[i], 2)
            if end_bal < 0.005:
                end_bal = 0.0
            balances[i] = end_bal
            rows.append({
                "month": month,
                "debt": d.name,
                "kind": d.kind,
                "apr": d.apr,
                "start_balance": start_bal,
                "interest": interest[i],
                "payment": pay[i],
                "principal": round(pay[i] - interest[i], 2),
                "end_balance": end_bal,
            })
            if end_bal == 0.0 and d.name not in payoff_month:
                payoff_month[d.name] = month

        # Negative amortization — the payment doesn't even cover interest, so the
        # balance is growing. Stop early rather than grinding to MAX_MONTHS.
        if round(sum(balances), 2) >= prev_total - 0.005 and month > 1:
            stalled += 1
            if stalled >= 6:
                break
        else:
            stalled = 0
        prev_total = round(sum(balances), 2)

    never = any(b > 0.005 for b in balances)
    ledger = pd.DataFrame(rows)
    if ledger.empty:
        monthly = pd.DataFrame(columns=["month", "date", "interest", "payment", "principal", "balance"])
    else:
        dates = _month_dates(start_date, int(ledger["month"].max()))
        ledger["date"] = ledger["month"].map(lambda m: dates[m - 1])
        monthly = ledger.groupby("month", as_index=False).agg(
            interest=("interest", "sum"),
            payment=("payment", "sum"),
            principal=("principal", "sum"),
            balance=("end_balance", "sum"),
        )
        monthly["date"] = monthly["month"].map(lambda m: dates[m - 1])

    return Schedule(
        ledger=ledger,
        monthly=monthly,
        strategy=strategy,
        budget=budget,
        start_date=start_date,
        payoff_month=payoff_month,
        never_pays_off=never,
        short_months=short_months,
    )


# ------------------------------------------------------------------ utilities

def minimum_budget(debts: Iterable[Debt]) -> float:
    """Sum of this month's required payments."""
    return round(sum(d.required_payment(d.balance + d.monthly_interest) for d in debts), 2)


def current_budget(debts: Iterable[Debt]) -> float:
    """Sum of what the user says they pay today."""
    return round(sum(d.effective_payment() for d in debts), 2)


def compare_strategies(
    debts: Sequence[Debt],
    budget: float,
    custom_order: Sequence[str] | None = None,
    **kw,
) -> dict[str, Schedule]:
    """Run every strategy against the same budget so they are comparable."""
    out: dict[str, Schedule] = {}
    for s in (MINIMUM, CURRENT, SNOWBALL, AVALANCHE):
        b = minimum_budget(debts) if s == MINIMUM else budget
        out[s] = simulate(debts, b, strategy=s, custom_order=custom_order, **kw)
    if custom_order:
        out[CUSTOM] = simulate(debts, budget, strategy=CUSTOM, custom_order=custom_order, **kw)
    return out


def sensitivity_curve(
    debts: Sequence[Debt],
    budget: float,
    strategy: str = AVALANCHE,
    steps: Sequence[float] = (0, 25, 50, 100, 150, 200, 300, 500, 750, 1000),
    **kw,
) -> pd.DataFrame:
    """How months-to-freedom and lifetime interest respond to extra dollars."""
    base = simulate(debts, budget, strategy=strategy, **kw)
    rows = []
    for step in steps:
        s = base if step == 0 else simulate(debts, budget, strategy=strategy, extra=step, **kw)
        rows.append({
            "extra": step,
            "months": s.months if not s.never_pays_off else None,
            "interest": s.total_interest,
            "interest_saved": max(0.0, base.total_interest - s.total_interest),
            "months_saved": (base.months - s.months) if not (base.never_pays_off or s.never_pays_off) else None,
        })
    return pd.DataFrame(rows)


def budget_for_target(
    debts: Sequence[Debt],
    target_months: int,
    strategy: str = AVALANCHE,
    custom_order: Sequence[str] | None = None,
) -> Optional[float]:
    """Smallest monthly budget that clears everything within `target_months`.

    Binary search — the relationship is monotonic (more money is never slower),
    so bisection is exact to the cent in ~25 iterations.
    """
    if not debts or target_months < 1:
        return None
    total = sum(d.balance for d in debts)
    lo, hi = minimum_budget(debts), total + 1.0
    if simulate(debts, hi, strategy=strategy, custom_order=custom_order,
                max_months=target_months + 1).months > target_months:
        return None  # not reachable even by paying the whole balance at once
    for _ in range(40):
        if hi - lo < 0.01:
            break
        mid = (lo + hi) / 2
        s = simulate(debts, mid, strategy=strategy, custom_order=custom_order,
                     max_months=target_months + 1)
        if s.never_pays_off or s.months > target_months:
            lo = mid
        else:
            hi = mid
    return round(hi, 2)


def where_the_money_goes(schedule: Schedule, horizon_months: int = 12) -> dict:
    """Split the next `horizon_months` of payments into interest vs principal."""
    if schedule.monthly.empty:
        return {"interest": 0.0, "principal": 0.0, "payment": 0.0}
    window = schedule.monthly[schedule.monthly["month"] <= horizon_months]
    return {
        "interest": float(window["interest"].sum()),
        "principal": float(window["principal"].sum()),
        "payment": float(window["payment"].sum()),
    }
