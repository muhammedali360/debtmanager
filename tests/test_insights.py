"""Insight correctness — especially that we never quote a number we can't stand behind."""

import pytest

from debtapp import engine as E
from debtapp import insights as I
from debtapp.models import CREDIT_CARD, TERM_LOAN, Debt, Profile


def card(name="Card", balance=5_000, apr=22.99, floor=25, pct=2.0, limit=0, pays=0):
    return Debt(name=name, kind=CREDIT_CARD, balance=balance, apr=apr, min_payment=floor,
                min_percent=pct, credit_limit=limit, current_payment=pays)


def titles(ins):
    return " | ".join(i.title for i in ins)


# ------------------------------------------------------------------ formatting

def test_money_and_duration_formatting():
    assert I.money(1234.567) == "$1,235"
    assert I.money(1234.567, cents=True) == "$1,234.57"
    assert I.money(None) == "—"
    assert I.duration(0) == "0 mo"
    assert I.duration(11) == "11 mo"
    assert I.duration(12) == "1 yr"
    assert I.duration(27) == "2 yr 3 mo"
    assert I.duration(None) == "never"


# ------------------------------------------------------------------ the basics

def test_no_debts_returns_a_single_all_clear():
    ins = I.generate([], Profile())
    assert len(ins) == 1 and ins[0].severity == I.GOOD


def test_insights_are_sorted_by_severity_then_stake():
    ins = I.generate([card(balance=9_000, apr=24.0, pays=300)], Profile(monthly_budget=300))
    ranks = [I.SEVERITY_RANK[i.severity] for i in ins]
    assert ranks == sorted(ranks)


def test_every_insight_has_a_title_and_body():
    ins = I.generate([card(), card(name="Two", balance=2_000)], Profile(monthly_budget=400))
    assert ins and all(i.title and i.body for i in ins)
    assert all(i.severity in I.SEVERITY_RANK for i in ins)


# ----------------------------------------------- the never-pays-off guardrails

def _unpayable():
    """A 2% minimum on a 24.99% card: the minimum is below the interest."""
    return [card(name="Trap", balance=8_400, apr=24.99, floor=35, pct=2.0)]


def test_minimums_that_never_clear_are_called_out_as_never():
    debts = _unpayable()
    mins = E.simulate(debts, E.minimum_budget(debts), strategy=E.MINIMUM)
    assert mins.never_pays_off  # precondition for this test to mean anything

    ins = I.generate(debts, Profile(monthly_budget=400))
    trap = [i for i in ins if "never clear" in i.title.lower()]
    assert trap, f"expected a 'never clears' insight, got: {titles(ins)}"
    assert "Never" in (trap[0].metric or "")


def test_no_dollar_savings_are_quoted_against_an_unbounded_baseline():
    """The bug this pins: comparing to a plan that never terminates produced a
    made-up 'you saved $67,017' from wherever the simulation happened to stop."""
    debts = _unpayable()
    ins = I.generate(debts, Profile(monthly_budget=400))
    bogus = [i for i in ins if "already saving you real money" in i.title]
    assert not bogus, "quoted a savings figure against a non-terminating baseline"


def test_a_plan_that_never_pays_off_is_flagged_critical():
    debts = [Debt(name="Deep trap", kind=TERM_LOAN, balance=10_000, apr=29.99, min_payment=100)]
    ins = I.generate(debts, Profile(monthly_budget=100))
    assert any(i.severity == I.CRITICAL for i in ins)
    assert any("never goes away" in i.title for i in ins)


def test_extra_payment_that_escapes_the_trap_is_framed_as_escape_not_savings():
    debts = [Debt(name="Trap", kind=TERM_LOAN, balance=10_000, apr=29.99, min_payment=100)]
    ins = I.generate(debts, Profile(monthly_budget=100))
    escape = [i for i in ins if "finite" in i.title]
    assert escape and escape[0].severity == I.CRITICAL


# ------------------------------------------------------------ specific advice

def test_minimum_below_interest_is_reported_per_card():
    ins = I.generate(_unpayable(), Profile(monthly_budget=400))
    hits = [i for i in ins if "smaller than the interest" in i.title]
    assert hits and hits[0].severity == I.CRITICAL


def test_shortfall_against_minimums_is_critical():
    debts = [card(balance=10_000, apr=22.0), card(name="B", balance=10_000, apr=22.0)]
    ins = I.generate(debts, Profile(monthly_budget=50), budget=50)
    assert any("doesn't cover your minimum" in i.title for i in ins)
    assert any(i.severity == I.CRITICAL for i in ins)


def test_high_utilization_is_flagged_with_the_right_percentage():
    debts = [card(balance=9_000, apr=20.0, limit=10_000)]
    ins = I.generate(debts, Profile(monthly_budget=500))
    hits = [i for i in ins if "utilization" in i.title.lower()]
    assert hits and hits[0].metric == "90%"


def test_low_utilization_is_not_flagged():
    debts = [card(balance=1_000, apr=20.0, limit=20_000)]
    ins = I.generate(debts, Profile(monthly_budget=300))
    assert not [i for i in ins if "utilization is" in i.title]


def test_cheap_debt_gets_a_dont_rush_it_note():
    debts = [card(name="Expensive", balance=5_000, apr=24.0),
             Debt(name="Cheap student loan", kind=TERM_LOAN, balance=10_000, apr=3.4,
                  min_payment=120)]
    ins = I.generate(debts, Profile(monthly_budget=700))
    assert any("Don't rush your cheap debt" in i.title for i in ins)


def test_highest_apr_debt_is_named():
    debts = [card(name="Low", balance=5_000, apr=9.0),
             card(name="Killer", balance=3_000, apr=29.99)]
    ins = I.generate(debts, Profile(monthly_budget=500))
    assert any("Killer" in i.title and "29.99" in i.title for i in ins)


def test_no_emergency_fund_is_flagged():
    ins = I.generate([card(balance=4_000)], Profile(monthly_budget=400, emergency_fund=0))
    assert any("emergency fund" in i.title.lower() for i in ins)


def test_idle_cash_versus_high_apr_is_flagged():
    ins = I.generate([card(balance=6_000, apr=24.0)],
                     Profile(monthly_budget=400, emergency_fund=6_000))
    assert any("idle cash" in i.title.lower() for i in ins)


def test_debt_to_income_is_flagged_when_minimums_are_heavy():
    debts = [card(balance=40_000, apr=22.0, limit=50_000)]
    ins = I.generate(debts, Profile(monthly_budget=900, monthly_income=2_000))
    assert any("income" in i.title.lower() for i in ins)


def test_balance_transfer_math_nets_out_the_fee():
    debts = [card(name="Big card", balance=10_000, apr=24.99)]
    ins = I.generate(debts, Profile(monthly_budget=600))
    bt = [i for i in ins if "balance transfer" in i.title.lower()]
    assert bt
    # Net must be strictly less than gross interest avoided — the 3% fee is real.
    assert "$" in (bt[0].metric or "")
    assert bt[0].stake < 10_000 * 0.2499 * 1.5


def test_no_balance_transfer_suggested_for_low_apr_cards():
    ins = I.generate([card(balance=5_000, apr=6.0)], Profile(monthly_budget=400))
    assert not [i for i in ins if "balance transfer" in i.title.lower()]


# ------------------------------------------------------------------ integrity

def test_stakes_are_never_negative():
    debts = [card(), card(name="B", balance=2_000, apr=28.0, limit=3_000)]
    ins = I.generate(debts, Profile(monthly_budget=600, monthly_income=5_000,
                                    emergency_fund=2_000))
    assert all(i.stake >= 0 for i in ins)


def test_generate_does_not_mutate_the_debts():
    d = card(balance=5_000)
    I.generate([d], Profile(monthly_budget=400))
    assert d.balance == 5_000


def test_a_realistic_portfolio_produces_a_broad_spread():
    debts = [
        card(name="Chase", balance=8_400, apr=24.99, floor=35, limit=12_000, pays=250),
        card(name="Citi", balance=3_150, apr=21.49, floor=25, limit=6_000, pays=95),
        Debt(name="Car", kind=TERM_LOAN, balance=18_600, apr=7.4, min_payment=445,
             current_payment=445),
    ]
    ins = I.generate(debts, Profile(monthly_budget=1_250, monthly_income=6_200,
                                    emergency_fund=1_800))
    assert len(ins) >= 8
    assert len({i.severity for i in ins}) >= 3  # not all one flavour
