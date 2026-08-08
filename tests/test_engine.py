"""Engine correctness. These pin the math down against closed-form answers."""

from datetime import date

import pytest

from debtapp import engine as E
from debtapp.models import CREDIT_CARD, TERM_LOAN, Debt, amortized_payment


def card(name="Card", balance=5000, apr=22.99, floor=25, pct=2.0, limit=0):
    return Debt(name=name, kind=CREDIT_CARD, balance=balance, apr=apr,
                min_payment=floor, min_percent=pct, credit_limit=limit)


def loan(name="Loan", balance=20000, apr=6.5, payment=400, term=0):
    return Debt(name=name, kind=TERM_LOAN, balance=balance, apr=apr,
                min_payment=payment, term_months=term)


# ------------------------------------------------------------------ core math

def test_amortized_payment_matches_closed_form():
    # $200,000 at 6% over 360 months = $1,199.10 (standard mortgage table value)
    assert amortized_payment(200_000, 6.0, 360) == pytest.approx(1199.10, abs=0.01)
    assert amortized_payment(20_000, 6.5, 60) == pytest.approx(391.32, abs=0.01)


def test_zero_apr_is_pure_division():
    assert amortized_payment(1200, 0.0, 12) == pytest.approx(100.0)


def test_term_loan_pays_off_on_schedule():
    """A loan paid its amortized payment retires in `term` months, plus at most a
    token stub — payments round down to the cent, exactly like a real servicer's
    final adjusted payment."""
    term = 60
    pmt = amortized_payment(20_000, 6.5, term)
    s = E.simulate([loan(balance=20_000, apr=6.5, payment=pmt)], budget=pmt)
    assert not s.never_pays_off
    assert term <= s.months <= term + 1
    if s.months == term + 1:
        assert s.ledger["payment"].iloc[-1] < 1.00  # a stub, not a real payment
    # Total interest must match the closed-form answer.
    assert s.total_interest == pytest.approx(pmt * term - 20_000, abs=1.0)


def test_zero_interest_debt_is_simple_division():
    s = E.simulate([loan(balance=1200, apr=0.0, payment=100)], budget=100)
    assert s.months == 12
    assert s.total_interest == pytest.approx(0.0)
    assert s.total_paid == pytest.approx(1200.0, abs=0.01)


def test_first_month_interest_is_balance_times_monthly_rate():
    d = card(balance=10_000, apr=24.0)
    s = E.simulate([d], budget=500)
    first = s.ledger.iloc[0]
    assert first["interest"] == pytest.approx(10_000 * 0.24 / 12, abs=0.01)  # $200
    assert first["end_balance"] == pytest.approx(10_000 + 200 - 500, abs=0.01)


def test_ledger_is_internally_consistent():
    """start + interest - payment == end, every row, no exceptions."""
    s = E.simulate([card(), loan()], budget=900)
    L = s.ledger
    drift = (L["start_balance"] + L["interest"] - L["payment"] - L["end_balance"]).abs()
    assert drift.max() < 0.011


def test_principal_plus_interest_equals_total_paid():
    s = E.simulate([card(), loan()], budget=900)
    assert s.total_principal + s.total_interest == pytest.approx(s.total_paid, abs=0.01)


def test_total_principal_equals_starting_balance():
    debts = [card(balance=5000), loan(balance=20_000)]
    s = E.simulate(debts, budget=900)
    assert s.total_principal == pytest.approx(25_000, abs=1.0)


def test_balances_never_go_negative():
    s = E.simulate([card(balance=300), loan(balance=500, payment=400)], budget=5000)
    assert (s.ledger["end_balance"] >= 0).all()
    assert (s.ledger["payment"] >= 0).all()


# ------------------------------------------------------------- card behaviour

def test_card_minimum_shrinks_with_the_balance():
    d = card(balance=10_000, apr=20, floor=25, pct=2.0)
    assert d.required_payment(10_000) == pytest.approx(200.0)
    assert d.required_payment(5_000) == pytest.approx(100.0)
    assert d.required_payment(500) == pytest.approx(25.0)  # dollar floor takes over


def test_minimum_only_costs_far_more_than_a_flat_payment():
    d = card(balance=10_000, apr=24.0, floor=25, pct=2.0)
    mins = E.simulate([d], E.minimum_budget([d]), strategy=E.MINIMUM)
    flat = E.simulate([d], E.minimum_budget([d]), strategy=E.AVALANCHE)
    # Same starting payment; the only difference is letting it shrink.
    assert mins.months > flat.months
    assert mins.total_interest > flat.total_interest


def test_negative_amortization_is_detected_not_looped_forever():
    """Minimum below the interest charge: balance grows, engine must bail out."""
    d = Debt(name="Trap", kind=TERM_LOAN, balance=10_000, apr=29.99, min_payment=100)
    s = E.simulate([d], budget=100, max_months=E.MAX_MONTHS)
    assert s.never_pays_off
    assert s.payoff_date is None
    assert s.months < E.MAX_MONTHS  # stalled out early rather than grinding


# ------------------------------------------------------------------ strategies

def test_avalanche_beats_snowball_on_interest():
    # The two methods must actually disagree: the expensive debt is the *big*
    # one, so avalanche and snowball pick opposite targets.
    debts = [
        card(name="Small low APR", balance=2_000, apr=11.0),
        card(name="Big high APR", balance=9_000, apr=27.0),
    ]
    budget = E.minimum_budget(debts) + 400
    av = E.simulate(debts, budget, strategy=E.AVALANCHE)
    sn = E.simulate(debts, budget, strategy=E.SNOWBALL)
    assert av.payoff_month["Big high APR"] < sn.payoff_month["Big high APR"]
    assert av.total_interest < sn.total_interest


def test_snowball_clears_the_smallest_account_first():
    debts = [
        card(name="Big", balance=9_000, apr=27.0),
        card(name="Tiny", balance=800, apr=9.0),
    ]
    sn = E.simulate(debts, E.minimum_budget(debts) + 300, strategy=E.SNOWBALL)
    assert sn.payoff_month["Tiny"] < sn.payoff_month["Big"]


def test_avalanche_targets_the_highest_apr_first():
    debts = [
        card(name="Cheap", balance=3_000, apr=5.0),
        card(name="Expensive", balance=3_000, apr=27.0),
    ]
    av = E.simulate(debts, E.minimum_budget(debts) + 300, strategy=E.AVALANCHE)
    assert av.payoff_month["Expensive"] < av.payoff_month["Cheap"]


def test_custom_order_is_respected():
    debts = [card(name="A", balance=3_000, apr=25.0), card(name="B", balance=3_000, apr=10.0)]
    s = E.simulate(debts, E.minimum_budget(debts) + 300, strategy=E.CUSTOM, custom_order=["B", "A"])
    assert s.payoff_month["B"] < s.payoff_month["A"]


def test_freed_up_minimums_roll_over():
    """Once a debt clears, the whole budget keeps flowing to what's left."""
    debts = [card(name="A", balance=1_000, apr=20.0), card(name="B", balance=8_000, apr=20.0)]
    budget = E.minimum_budget(debts) + 500
    s = E.simulate(debts, budget, strategy=E.SNOWBALL)
    after = s.monthly[s.monthly["month"] > s.payoff_month["A"]]
    # Payments hold at the full budget instead of dropping to B's minimum.
    assert after["payment"].iloc[0] == pytest.approx(budget, abs=0.02)


# ---------------------------------------------------------------- extra money

def test_extra_payments_strictly_help():
    debts = [card(balance=8_000, apr=22.0)]
    base = E.simulate(debts, 300)
    more = E.simulate(debts, 300, extra=200)
    assert more.months < base.months
    assert more.total_interest < base.total_interest


def test_lump_sum_earlier_beats_later():
    debts = [card(balance=8_000, apr=22.0)]
    now = E.simulate(debts, 300, lump_sum=1_000, lump_month=1)
    later = E.simulate(debts, 300, lump_sum=1_000, lump_month=13)
    assert now.total_interest < later.total_interest


def test_sensitivity_curve_is_monotonic():
    debts = [card(balance=8_000, apr=22.0)]
    df = E.sensitivity_curve(debts, 300, steps=(0, 50, 100, 200))
    assert df["interest_saved"].is_monotonic_increasing
    assert df["interest"].is_monotonic_decreasing


# ------------------------------------------------------------------ shortfall

def test_budget_below_minimums_is_flagged_and_prorated():
    debts = [card(balance=5_000, apr=22.0), loan(balance=20_000, payment=400)]
    required = E.minimum_budget(debts)
    s = E.simulate(debts, required / 2, strategy=E.AVALANCHE, max_months=6)
    assert s.short_months > 0
    assert s.monthly["payment"].iloc[0] == pytest.approx(required / 2, abs=0.05)


# --------------------------------------------------------------------- shapes

def test_simulate_does_not_mutate_the_caller_debts():
    d = card(balance=5_000)
    E.simulate([d], 500)
    assert d.balance == 5_000


def test_empty_debt_list_is_safe():
    s = E.simulate([], 500)
    assert s.months == 0
    assert s.total_interest == 0
    assert s.ledger.empty


def test_zero_balance_debts_are_ignored():
    s = E.simulate([card(balance=0), card(name="Real", balance=1_000, apr=10)], 200)
    assert set(s.ledger["debt"].unique()) == {"Real"}


def test_yearly_breakdown_sums_to_the_total():
    s = E.simulate([card(balance=9_000, apr=21.0)], 400, start_date=date(2026, 1, 1))
    y = s.yearly_interest()
    assert y["interest"].sum() == pytest.approx(s.total_interest, abs=0.01)
    assert y["principal"].sum() == pytest.approx(s.total_principal, abs=0.01)


def test_per_debt_totals_sum_to_the_total():
    s = E.simulate([card(), loan()], 900)
    t = s.per_debt_totals()
    assert t["interest"].sum() == pytest.approx(s.total_interest, abs=0.01)
    assert set(t["debt"]) == {"Card", "Loan"}


def test_compare_strategies_uses_one_budget_for_all_but_minimum():
    debts = [card(), loan()]
    budget = E.minimum_budget(debts) + 300
    out = E.compare_strategies(debts, budget)
    assert set(out) == {E.MINIMUM, E.CURRENT, E.SNOWBALL, E.AVALANCHE}
    assert out[E.AVALANCHE].total_interest <= out[E.SNOWBALL].total_interest
    assert out[E.MINIMUM].total_interest >= out[E.AVALANCHE].total_interest
