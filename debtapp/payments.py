"""Due dates and the real-money ledger.

Two separate jobs live here, and the split matters:

* **Due dates** answer "what is coming, and did I already handle it?" — pure
  calendar arithmetic over ``Debt.due_day``.
* **The ledger** answers "what have I actually paid?" — aggregation over
  recorded :class:`~debtapp.models.Payment` rows.

Neither one touches :mod:`debtapp.engine`. Everything in the engine is a
*projection* that moves whenever an assumption moves; everything here is
*history*, which never moves. Keeping them apart is what lets the app say "you
have paid $4,210 in interest" without that number quietly re-deriving itself.
"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

import pandas as pd

from .models import Debt, Payment

# How long after a missed due date we keep saying "overdue" rather than rolling
# the display forward to the next one. A user who never logs payments should see
# a countdown, not a permanent red banner they learn to ignore.
LATE_WINDOW_DAYS = 10
# Someone who *does* log payments has established a pattern, so a gap is real
# signal and we hold the warning most of the way to the next cycle.
LATE_WINDOW_TRACKED_DAYS = 25

DUE_SOON_DAYS = 7  # inside this many days we surface it as "coming up"

PAID = "paid"
DUE_TODAY = "due_today"
OVERDUE = "overdue"
DUE_SOON = "due_soon"
SCHEDULED = "scheduled"

STATUS_RANK = {OVERDUE: 0, DUE_TODAY: 1, DUE_SOON: 2, SCHEDULED: 3, PAID: 4}
STATUS_ICON = {OVERDUE: "🛑", DUE_TODAY: "📅", DUE_SOON: "⏳", SCHEDULED: "🗓️", PAID: "✅"}


# ------------------------------------------------------------- calendar helpers

def _clamp(year: int, month: int, day: int) -> date:
    """The given day-of-month in that month, clamped to a day that exists.

    A payment due on the 31st is due on the 28th in February — every lender
    handles it this way, and so does every user's expectation.
    """
    return date(year, month, min(day, calendar.monthrange(year, month)[1]))


def _shift_month(d: date, months: int, day: int) -> date:
    """`day`-of-month, `months` away from `d`'s month, clamped to real dates."""
    total = d.year * 12 + (d.month - 1) + months
    return _clamp(total // 12, total % 12 + 1, day)


def last_due_date(due_day: int, today: date) -> date:
    """The most recent due date at or before `today`."""
    this_month = _clamp(today.year, today.month, due_day)
    return this_month if this_month <= today else _shift_month(today, -1, due_day)


def next_due_date(due_day: int, today: date) -> date:
    """The next due date strictly after `today`."""
    this_month = _clamp(today.year, today.month, due_day)
    return this_month if this_month > today else _shift_month(today, 1, due_day)


# --------------------------------------------------------------- due statuses

@dataclass
class DueItem:
    """One account's answer to "where do I stand this cycle?"."""

    debt: Debt
    due_date: date
    amount: float          # what we expect the payment to be
    paid: float            # logged against this cycle
    status: str
    days: int              # >0 days away, 0 today, <0 days late

    @property
    def name(self) -> str:
        return self.debt.name

    @property
    def outstanding(self) -> float:
        """Still to send this cycle."""
        return max(0.0, self.amount - self.paid)

    @property
    def is_actionable(self) -> bool:
        """Worth putting in front of the user right now."""
        return self.status in (OVERDUE, DUE_TODAY, DUE_SOON)

    @property
    def phrase(self) -> str:
        """Human wording for the timing, e.g. "in 3 days" / "9 days late"."""
        if self.status == DUE_TODAY:
            return "due today"
        if self.days < 0:
            n = -self.days
            return f"{n} day{'s' if n != 1 else ''} late"
        if self.days == 1:
            return "due tomorrow"
        return f"in {self.days} days"


def cycle_for(paid_on: date, due_day: int) -> date:
    """The due date a payment made on `paid_on` most plausibly settles.

    Whichever due date it lands nearest — people pay a few days early or a few
    days late, and both are the same intent. Attributing by *window* instead
    ("anything since the last due date") quietly credits a late payment to the
    following month, so someone who habitually pays a week late would be told
    they were current and skip a cycle entirely. Ties go to the earlier date,
    settling the older obligation first.
    """
    before = last_due_date(due_day, paid_on)
    after = next_due_date(due_day, paid_on)
    return before if (paid_on - before).days <= (after - paid_on).days else after


def belongs_to(payment: Payment, debt: Debt) -> bool:
    """Whether `payment` was recorded against `debt`.

    Identity, not name. Two accounts can share a name — a Chase card and a Chase
    auto loan live in separate grids and nothing stops both being called "Chase"
    — and crediting one's payment to the other would tell the user a bill was
    settled when it wasn't. A deleted account also leaves its ledger rows behind
    with a null id; those are history, and must not be inherited by whatever
    account is named that way next.

    Falls back to the name only for a debt that has never been saved, where an
    id doesn't exist yet and the name is genuinely all there is.
    """
    if debt.id is not None:
        return payment.debt_id == debt.id
    return payment.debt_name == debt.name


def paid_for_cycle(payments: Iterable[Payment], due_day: int, anchor: date) -> float:
    """Total, among payments already known to be this account's, settling `anchor`."""
    return round(sum(p.amount for p in payments
                     if cycle_for(p.paid_on, due_day) == anchor), 2)


def due_status(debt: Debt, payments: Sequence[Payment], today: Optional[date] = None,
               soon_days: int = DUE_SOON_DAYS) -> Optional[DueItem]:
    """Where this account stands. ``None`` when no due day is on file."""
    if not debt.due_day or debt.balance <= 0:
        return None
    today = today or date.today()
    amount = round(debt.effective_payment(), 2)
    mine = [p for p in payments if belongs_to(p, debt)]

    last = last_due_date(debt.due_day, today)
    upcoming = next_due_date(debt.due_day, today)

    # Two cycles can legitimately be settled right now: the one whose date has
    # just passed, and the next one if the user paid ahead. Either means silence.
    #
    # `settled > 0` is load-bearing. When we don't know what this account's
    # payment is — a loan saved with no required payment and no term — `amount`
    # is 0, and without this guard every such account reports itself paid
    # forever, which is the exact failure this feature exists to prevent.
    ahead = paid_for_cycle(mine, debt.due_day, upcoming)
    current = paid_for_cycle(mine, debt.due_day, last)
    for settled in (ahead, current):
        if settled > 0 and settled >= amount - 0.5:
            return DueItem(debt, upcoming, amount, settled, PAID, (upcoming - today).days)

    paid = current
    days_late = (today - last).days
    grace = LATE_WINDOW_TRACKED_DAYS if mine else LATE_WINDOW_DAYS
    if days_late == 0:
        return DueItem(debt, last, amount, paid, DUE_TODAY, 0)
    if days_late <= grace:
        return DueItem(debt, last, amount, paid, OVERDUE, -days_late)

    # The miss is old enough that pointing forward is more useful than shouting
    # about a date the user has clearly moved on from. The item now describes the
    # *next* cycle, so it must report that cycle's money — carrying the stale
    # partial forward would pre-fill the payment box with too little.
    days = (upcoming - today).days
    return DueItem(debt, upcoming, amount, ahead,
                   DUE_SOON if days <= soon_days else SCHEDULED, days)


def upcoming(debts: Sequence[Debt], payments: Sequence[Payment],
             today: Optional[date] = None, soon_days: int = DUE_SOON_DAYS) -> list[DueItem]:
    """Every account with a due date, most urgent first."""
    items = [due_status(d, payments, today, soon_days) for d in debts]
    return sorted((i for i in items if i),
                  key=lambda i: (STATUS_RANK[i.status], i.days, i.name))


def actionable(debts: Sequence[Debt], payments: Sequence[Payment],
               today: Optional[date] = None) -> list[DueItem]:
    """Just the ones that need the user to do or confirm something."""
    return [i for i in upcoming(debts, payments, today) if i.is_actionable]


# ------------------------------------------------------------ recording money

def split_payment(debt: Debt, amount: float,
                  interest: Optional[float] = None) -> tuple[float, float, float]:
    """Split `amount` into (interest, principal, balance after).

    Interest defaults to one month's accrual at today's balance, which is what
    a statement will show. The caller can override it with the real figure —
    that is the whole reason it's a parameter.
    """
    accrued = debt.monthly_interest if interest is None else max(0.0, interest)
    # Interest is charged whether or not the payment covers it; anything left
    # over reduces principal, and a payment that falls short leaves the balance
    # *higher* than it started. That negative principal is the trap made visible.
    principal = round(amount - accrued, 2)
    new_balance = round(max(0.0, debt.balance + accrued - amount), 2)
    return round(accrued, 2), principal, new_balance


def build_payment(debt: Debt, amount: float, paid_on: date,
                  interest: Optional[float] = None, note: str = "") -> Payment:
    """A ledger row for a payment against `debt`, with the split worked out."""
    accrued, principal, new_balance = split_payment(debt, amount, interest)
    return Payment(
        debt_name=debt.name,
        paid_on=paid_on,
        amount=round(amount, 2),
        interest=accrued,
        principal=principal,
        balance_after=new_balance,
        note=note,
        debt_id=debt.id,
    )


# ------------------------------------------------------------------ the ledger

def totals(payments: Sequence[Payment]) -> dict:
    """The counters at the top of the ledger page."""
    if not payments:
        return {"paid": 0.0, "interest": 0.0, "principal": 0.0, "count": 0,
                "interest_share": 0.0, "first": None, "last": None, "months": 0,
                "avg_monthly": 0.0}
    paid = round(sum(p.amount for p in payments), 2)
    interest = round(sum(p.interest for p in payments), 2)
    dates = [p.paid_on for p in payments]
    first, last = min(dates), max(dates)
    # Count distinct calendar months so a user who pays four accounts a month
    # doesn't look like they've been at this four times as long as they have.
    months = len({(d.year, d.month) for d in dates})
    return {
        "paid": paid,
        "interest": interest,
        "principal": round(paid - interest, 2),
        "count": len(payments),
        "interest_share": interest / paid if paid > 0 else 0.0,
        "first": first,
        "last": last,
        "months": months,
        "avg_monthly": round(paid / months, 2) if months else 0.0,
    }


def by_month(payments: Sequence[Payment]) -> pd.DataFrame:
    """One row per calendar month: what went out, and where it landed."""
    cols = ["month", "date", "payment", "interest", "principal"]
    if not payments:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame([{"date": pd.Timestamp(p.paid_on.year, p.paid_on.month, 1),
                        "payment": p.amount, "interest": p.interest,
                        "principal": p.principal} for p in payments])
    out = df.groupby("date", as_index=False)[["payment", "interest", "principal"]].sum()
    out = out.sort_values("date", ignore_index=True)
    out.insert(0, "month", out["date"].dt.strftime("%b %Y"))
    return out[cols]


def by_debt(payments: Sequence[Payment]) -> pd.DataFrame:
    """One row per account: lifetime paid, and how much of it the lender kept."""
    cols = ["debt", "payments", "paid", "interest", "principal", "interest_share", "last"]
    if not payments:
        return pd.DataFrame(columns=cols)
    df = pd.DataFrame([{"debt": p.debt_name, "paid": p.amount, "interest": p.interest,
                        "principal": p.principal, "last": p.paid_on} for p in payments])
    g = df.groupby("debt", as_index=False).agg(
        payments=("paid", "size"), paid=("paid", "sum"), interest=("interest", "sum"),
        principal=("principal", "sum"), last=("last", "max"))
    g["interest_share"] = g.apply(
        lambda r: r["interest"] / r["paid"] if r["paid"] > 0 else 0.0, axis=1)
    return g.sort_values("interest", ascending=False, ignore_index=True)[cols]


def cumulative(payments: Sequence[Payment]) -> pd.DataFrame:
    """Running interest-vs-principal totals, for the ledger's area chart."""
    cols = ["date", "cum_interest", "cum_principal", "cum_paid"]
    if not payments:
        return pd.DataFrame(columns=cols)
    rows = sorted(payments, key=lambda p: p.paid_on)
    df = pd.DataFrame([{"date": pd.Timestamp(p.paid_on), "interest": p.interest,
                        "principal": p.principal, "paid": p.amount} for p in rows])
    df = df.groupby("date", as_index=False)[["interest", "principal", "paid"]].sum()
    df["cum_interest"] = df["interest"].cumsum()
    df["cum_principal"] = df["principal"].cumsum()
    df["cum_paid"] = df["paid"].cumsum()
    return df[cols]


def streak(payments: Sequence[Payment], today: Optional[date] = None) -> int:
    """Consecutive months, ending with the current one, in which *something*
    was logged. A simple, honest motivator — not a claim about on-time-ness."""
    if not payments:
        return 0
    today = today or date.today()
    months = {(p.paid_on.year, p.paid_on.month) for p in payments}
    n, cursor = 0, date(today.year, today.month, 1)
    # The current month may legitimately have no payment yet, so start counting
    # from last month if today's is empty rather than resetting to zero.
    if (cursor.year, cursor.month) not in months:
        cursor = _shift_month(cursor, -1, 1)
    while (cursor.year, cursor.month) in months:
        n += 1
        cursor = _shift_month(cursor, -1, 1)
    return n


def interest_since_tracking(payments: Sequence[Payment], days: int = 365,
                            today: Optional[date] = None) -> float:
    """Interest actually paid in the trailing window — the honest annual number."""
    today = today or date.today()
    cutoff = today - timedelta(days=days)
    return round(sum(p.interest for p in payments if p.paid_on > cutoff), 2)
