"""Core domain objects for the debt manager."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import date
from typing import Optional

CREDIT_CARD = "credit_card"
TERM_LOAN = "term_loan"

DEBT_KINDS = {
    CREDIT_CARD: "Credit card / revolving",
    TERM_LOAN: "Term loan (auto, student, personal, mortgage)",
}

# Sub-labels help us give better advice without changing the math.
LOAN_SUBTYPES = ["Auto", "Student", "Personal", "Mortgage", "Medical", "Other"]

# The one list the UI asks from. `kind` is a real modelling distinction — a card's
# minimum is a percentage of the balance and shrinks as you pay, a loan's is a
# fixed contractual figure — but it is *our* distinction, not the user's, and
# making them declare it up front (by choosing which of two tables to type into)
# asked them to classify their debt before they had entered any of it.
ACCOUNT_TYPES = ["Credit card"] + LOAN_SUBTYPES


def amortized_payment(balance: float, apr: float, months: int) -> float:
    """The level monthly payment that retires `balance` in exactly `months`."""
    if months <= 0:
        return balance
    r = apr / 1200.0
    if r == 0:
        return balance / months
    factor = (1 + r) ** months
    return balance * r * factor / (factor - 1)


@dataclass
class Debt:
    """A single liability.

    Two shapes exist and they behave differently, which is the whole point:

    * ``credit_card`` — revolving. The required payment is a *percentage of the
      balance* with a dollar floor, so it shrinks as you pay down. That is the
      minimum-payment trap.
    * ``term_loan`` — a fixed contractual payment that does not move.
    """

    name: str
    kind: str = CREDIT_CARD
    balance: float = 0.0
    apr: float = 0.0
    min_payment: float = 0.0  # dollar floor (card) or contractual payment (loan)
    min_percent: float = 2.0  # cards only: % of balance
    credit_limit: float = 0.0  # cards only, 0 = unknown
    term_months: int = 0  # loans only, 0 = unknown
    subtype: str = "Other"
    current_payment: float = 0.0  # what the user actually pays today
    due_day: int = 0  # day of the month the payment is due, 0 = not set
    id: Optional[int] = None

    # ---------------------------------------------------------------- helpers
    @property
    def is_card(self) -> bool:
        return self.kind == CREDIT_CARD

    @property
    def monthly_rate(self) -> float:
        return self.apr / 1200.0

    @property
    def monthly_interest(self) -> float:
        """Interest accrued this month at today's balance."""
        return self.balance * self.monthly_rate

    @property
    def daily_interest(self) -> float:
        return self.balance * self.apr / 100.0 / 365.0

    @property
    def utilization(self) -> Optional[float]:
        if not self.is_card or self.credit_limit <= 0:
            return None
        return 100.0 * self.balance / self.credit_limit

    def required_payment(self, balance_with_interest: float) -> float:
        """The smallest payment the lender will accept this month."""
        if balance_with_interest <= 0:
            return 0.0
        if self.is_card:
            due = max(self.min_payment, self.min_percent / 100.0 * balance_with_interest)
        else:
            due = self.min_payment
            if due <= 0 and self.term_months > 0:
                due = amortized_payment(self.balance, self.apr, self.term_months)
        return min(due, balance_with_interest)

    def effective_payment(self) -> float:
        """What the user pays today — falls back to the required minimum."""
        if self.current_payment > 0:
            return self.current_payment
        return self.required_payment(self.balance + self.monthly_interest)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Debt":
        known = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in d.items() if k in known})


def kind_for(account_type: str) -> tuple[str, str]:
    """``(kind, subtype)`` for one of the :data:`ACCOUNT_TYPES` labels."""
    if account_type == "Credit card":
        return CREDIT_CARD, "Other"
    return TERM_LOAN, (account_type if account_type in LOAN_SUBTYPES else "Other")


def type_of(debt: Debt) -> str:
    """The inverse of :func:`kind_for` — the label to show for a stored debt."""
    return "Credit card" if debt.kind == CREDIT_CARD else (debt.subtype or "Other")


@dataclass
class Payment:
    """One payment the user actually made — a row in the ledger.

    This is *recorded history*, deliberately separate from the projections in
    :mod:`debtapp.engine`. Projections change every time an assumption moves;
    a payment happened and never changes again.

    ``debt_id`` links to the account so a rename carries history with it, but
    ``debt_name`` is stored alongside it so the row still reads correctly after
    the account itself is deleted.
    """

    debt_name: str
    paid_on: date
    amount: float
    interest: float = 0.0    # the slice the lender kept
    principal: float = 0.0   # the slice that reduced the balance
    balance_after: Optional[float] = None
    note: str = ""
    debt_id: Optional[int] = None
    id: Optional[int] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["paid_on"] = self.paid_on.isoformat()
        return d


@dataclass
class Profile:
    """User-level settings that shape the advice, not the amortization."""

    monthly_budget: float = 0.0  # 0 => "just the minimums"
    monthly_income: float = 0.0
    emergency_fund: float = 0.0
    strategy: str = "avalanche"
    custom_order: list = field(default_factory=list)
