"""Due-date arithmetic, the payment split, and ledger aggregation.

The calendar cases here are the ones that bite in production: month-end
clamping, year rollover, and the difference between "hasn't paid yet" and
"paid early for next cycle".
"""

from datetime import date

import pytest

from debtapp import payments as P
from debtapp.models import CREDIT_CARD, TERM_LOAN, Debt, Payment


def card(**kw) -> Debt:
    base = dict(name="Chase", kind=CREDIT_CARD, balance=8_400.0, apr=24.0, min_payment=35,
                min_percent=2.0, credit_limit=12_000, current_payment=250, due_day=15)
    base.update(kw)
    return Debt(**base)


def pay(day: date, amount: float = 250.0, name: str = "Chase") -> Payment:
    return Payment(debt_name=name, paid_on=day, amount=amount, interest=100.0,
                   principal=amount - 100.0)


# ------------------------------------------------------------ calendar mechanics

@pytest.mark.parametrize("due_day, today, expected", [
    (15, date(2026, 3, 10), date(2026, 3, 15)),   # later this month
    (15, date(2026, 3, 15), date(2026, 4, 15)),   # today has passed, roll forward
    (15, date(2026, 3, 20), date(2026, 4, 15)),
    (31, date(2026, 1, 31), date(2026, 2, 28)),   # clamped to a day that exists
    (31, date(2028, 1, 31), date(2028, 2, 29)),   # leap year
    (5, date(2026, 12, 20), date(2027, 1, 5)),    # year rollover
])
def test_next_due_date(due_day, today, expected):
    assert P.next_due_date(due_day, today) == expected


@pytest.mark.parametrize("due_day, today, expected", [
    (15, date(2026, 3, 10), date(2026, 2, 15)),
    (15, date(2026, 3, 15), date(2026, 3, 15)),   # today counts as "at or before"
    (15, date(2026, 3, 20), date(2026, 3, 15)),
    (31, date(2026, 3, 1), date(2026, 2, 28)),
    (5, date(2027, 1, 2), date(2026, 12, 5)),
])
def test_last_due_date(due_day, today, expected):
    assert P.last_due_date(due_day, today) == expected


def test_due_dates_never_produce_an_invalid_calendar_day():
    """Every day-of-month against every month of a leap year."""
    for due_day in range(1, 32):
        for month in range(1, 13):
            today = date(2028, month, 15)
            assert P.next_due_date(due_day, today) > today
            assert P.last_due_date(due_day, today) <= today


# ---------------------------------------------------------------- due statuses

def test_no_due_day_means_no_status():
    assert P.due_status(card(due_day=0), [], date(2026, 3, 20)) is None


def test_a_paid_off_account_is_not_chased_for_payment():
    assert P.due_status(card(balance=0.0), [], date(2026, 3, 20)) is None


def test_unpaid_and_past_the_due_date_is_overdue():
    item = P.due_status(card(), [], date(2026, 3, 20))
    assert item.status == P.OVERDUE
    assert item.days == -5
    assert item.due_date == date(2026, 3, 15)
    assert item.phrase == "5 days late"


def test_unpaid_on_the_due_date_itself_is_due_today():
    item = P.due_status(card(), [], date(2026, 3, 15))
    assert item.status == P.DUE_TODAY
    assert item.days == 0


def test_a_payment_this_cycle_clears_the_status_and_points_at_the_next_one():
    item = P.due_status(card(), [pay(date(2026, 3, 14))], date(2026, 3, 20))
    assert item.status == P.PAID
    assert item.due_date == date(2026, 4, 15)
    assert item.days == 26


def test_paying_early_does_not_nag_the_user():
    """Paid on the 8th for a 15th due date — the app must stay quiet."""
    item = P.due_status(card(), [pay(date(2026, 3, 8))], date(2026, 3, 12))
    assert item.status == P.PAID


def test_last_cycles_payment_does_not_satisfy_this_cycle():
    """Paid in February; by late March that is a new, unpaid cycle."""
    item = P.due_status(card(), [pay(date(2026, 2, 14))], date(2026, 3, 20))
    assert item.status == P.OVERDUE


def test_a_partial_payment_still_counts_as_outstanding():
    item = P.due_status(card(), [pay(date(2026, 3, 14), amount=100.0)], date(2026, 3, 20))
    assert item.status == P.OVERDUE
    assert item.paid == 100.0
    assert item.outstanding == 150.0


def test_due_soon_when_the_date_is_approaching():
    item = P.due_status(card(), [pay(date(2026, 3, 14))], date(2026, 4, 10))
    assert item.status == P.PAID  # March is settled
    item = P.due_status(card(due_day=15), [], date(2026, 4, 12))
    # April's 15th hasn't arrived, and March's is far enough back to roll forward.
    assert item.status == P.DUE_SOON
    assert item.days == 3


def test_a_user_who_never_logs_payments_is_not_permanently_overdue():
    """No history means no established pattern — show a countdown, not a siren."""
    item = P.due_status(card(), [], date(2026, 4, 12))
    assert item.status != P.OVERDUE


def test_a_user_who_does_log_payments_gets_a_longer_late_warning():
    history = [pay(date(2026, 1, 14)), pay(date(2026, 2, 14))]
    item = P.due_status(card(), history, date(2026, 4, 5))
    assert item.status == P.OVERDUE  # 21 days past the March 15 due date


def test_a_habitually_late_payment_is_not_credited_to_the_next_cycle():
    """The regression that matters most: paying Feb's bill on Feb 20 must not
    mark March as settled, or a late payer silently skips a whole month."""
    item = P.due_status(card(), [pay(date(2026, 2, 20))], date(2026, 3, 16))
    assert item.status == P.OVERDUE
    assert item.due_date == date(2026, 3, 15)
    assert item.paid == 0.0


@pytest.mark.parametrize("paid_on, settles", [
    (date(2026, 3, 14), date(2026, 3, 15)),   # a day early
    (date(2026, 3, 18), date(2026, 3, 15)),   # a few days late
    (date(2026, 3, 8), date(2026, 3, 15)),    # a week early, still this cycle
    (date(2026, 2, 20), date(2026, 2, 15)),   # late for February, not early for March
    (date(2026, 3, 1), date(2026, 2, 15)),    # equidistant — settle the older one
])
def test_a_payment_settles_whichever_due_date_it_lands_nearest(paid_on, settles):
    assert P.cycle_for(paid_on, 15) == settles


def test_paying_ahead_for_next_cycle_still_counts_as_paid():
    """Paid Mar 8 for the Mar 15 bill, checked on Mar 12 — stay quiet."""
    item = P.due_status(card(), [pay(date(2026, 3, 8))], date(2026, 3, 12))
    assert item.status == P.PAID
    assert item.paid == 250.0


def test_rolling_forward_past_an_old_miss_reports_the_new_cycles_money():
    """A stale partial on a long-missed cycle must not shrink what's now due."""
    history = [pay(date(2026, 1, 14)), pay(date(2026, 3, 14), amount=100.0)]
    item = P.due_status(card(), history, date(2026, 4, 12))
    assert item.status == P.DUE_SOON
    assert item.due_date == date(2026, 4, 15)
    assert item.paid == 0.0          # nothing paid toward April
    assert item.outstanding == 250.0  # not 150


def test_payments_for_another_account_are_not_credited():
    item = P.due_status(card(), [pay(date(2026, 3, 14), name="Other card")], date(2026, 3, 20))
    assert item.status == P.OVERDUE


def test_an_account_with_no_known_payment_amount_is_never_assumed_paid():
    """A loan saved with no required payment and no term has an expected payment
    of $0. Without a guard, `paid >= amount` is trivially true and the app
    reports it settled forever — the exact miss this feature exists to catch."""
    loan = Debt(name="Car loan", kind=TERM_LOAN, balance=18_600, apr=7.4, due_day=15)
    assert loan.effective_payment() == 0.0
    item = P.due_status(loan, [], date(2026, 3, 20))
    assert item.status == P.OVERDUE
    assert item.is_actionable


def test_a_real_payment_still_settles_an_account_with_no_stated_amount():
    loan = Debt(name="Car loan", kind=TERM_LOAN, balance=18_600, apr=7.4, due_day=15)
    item = P.due_status(loan, [pay(date(2026, 3, 14), name="Car loan")], date(2026, 3, 20))
    assert item.status == P.PAID


# ------------------------------------------------------- which account paid what

def test_two_accounts_sharing_a_name_do_not_share_credit():
    """A Chase card and a Chase auto loan are different bills. Cards and loans
    are entered in separate grids, so nothing stops both being called "Chase"."""
    paid_card = card(name="Chase")
    paid_card.id = 1
    unpaid_loan = Debt(name="Chase", kind=TERM_LOAN, balance=18_600, apr=7.4,
                       min_payment=445, current_payment=445, due_day=15, id=2)
    ledger = [Payment(debt_name="Chase", debt_id=1, paid_on=date(2026, 3, 14), amount=250.0)]

    assert P.due_status(paid_card, ledger, date(2026, 3, 20)).status == P.PAID
    assert P.due_status(unpaid_loan, ledger, date(2026, 3, 20)).status == P.OVERDUE


def test_a_new_account_does_not_inherit_a_deleted_ones_history():
    """Deleting an account nulls its ledger rows' id. Those rows are history —
    reusing the name must not make a brand-new account look paid."""
    orphan = Payment(debt_name="Store card", debt_id=None, paid_on=date(2026, 3, 14),
                     amount=250.0)
    fresh = card(name="Store card")
    fresh.id = 9
    assert P.due_status(fresh, [orphan], date(2026, 3, 20)).status == P.OVERDUE


def test_an_unsaved_account_still_matches_on_name():
    """Before a debt has ever been written there is no id, and the name is
    genuinely all we have to go on."""
    unsaved = card()
    assert unsaved.id is None
    assert P.due_status(unsaved, [pay(date(2026, 3, 14))], date(2026, 3, 20)).status == P.PAID


@pytest.mark.parametrize("debt_id, payment_debt_id, expected", [
    (1, 1, True),        # linked, same account
    (1, 2, False),       # linked, different accounts
    (1, None, False),    # orphaned row must not attach to a live account
    (None, None, True),  # neither saved — fall back to the name
    (None, 1, True),     # unsaved debt, name is all we have
])
def test_payment_ownership_rules(debt_id, payment_debt_id, expected):
    d = card(name="Chase")
    d.id = debt_id
    p = Payment(debt_name="Chase", debt_id=payment_debt_id, paid_on=date(2026, 3, 14),
                amount=250.0)
    assert P.belongs_to(p, d) is expected


def test_upcoming_sorts_most_urgent_first():
    debts = [
        card(name="Fine", due_day=15),
        card(name="Late", due_day=13),
        card(name="Today", due_day=20),
    ]
    items = P.upcoming(debts, [pay(date(2026, 3, 14), name="Fine")], date(2026, 3, 20))
    assert [i.name for i in items] == ["Late", "Today", "Fine"]
    assert [i.status for i in items] == [P.OVERDUE, P.DUE_TODAY, P.PAID]
    # Within overdue, whichever has been late longest sorts first.
    assert [i.name for i in P.actionable(debts, [], date(2026, 3, 20))] == \
        ["Late", "Fine", "Today"]


# ------------------------------------------------------------- the money split

def test_a_payment_splits_into_interest_then_principal():
    d = card(balance=1_200.0, apr=24.0)  # $24/mo interest
    interest, principal, new_balance = P.split_payment(d, 100.0)
    assert interest == 24.0
    assert principal == 76.0
    assert new_balance == 1_124.0


def test_a_payment_below_the_interest_grows_the_balance():
    """Negative amortization, made explicit rather than clamped away."""
    d = card(balance=1_200.0, apr=24.0)
    interest, principal, new_balance = P.split_payment(d, 10.0)
    assert principal == -14.0
    assert new_balance == 1_214.0


def test_the_statements_interest_figure_overrides_our_estimate():
    d = card(balance=1_200.0, apr=24.0)
    interest, principal, _ = P.split_payment(d, 100.0, interest=31.42)
    assert (interest, principal) == (31.42, 68.58)


def test_overpaying_settles_at_zero_rather_than_going_negative():
    d = card(balance=100.0, apr=24.0)
    _, _, new_balance = P.split_payment(d, 5_000.0)
    assert new_balance == 0.0


def test_build_payment_captures_the_account_link():
    d = card(balance=1_200.0, apr=24.0, name="Chase")
    d.id = 7
    p = P.build_payment(d, 100.0, date(2026, 3, 14), note="bonus")
    assert (p.debt_id, p.debt_name, p.note) == (7, "Chase", "bonus")
    assert p.balance_after == 1_124.0


def test_a_zero_apr_loan_sends_the_whole_payment_to_principal():
    d = Debt(name="0% furniture", kind=TERM_LOAN, balance=1_200.0, apr=0.0, min_payment=100)
    interest, principal, new_balance = P.split_payment(d, 100.0)
    assert (interest, principal, new_balance) == (0.0, 100.0, 1_100.0)


# ------------------------------------------------------------------ the ledger

LEDGER = [
    Payment(debt_name="Chase", paid_on=date(2026, 1, 14), amount=250.0,
            interest=170.0, principal=80.0),
    Payment(debt_name="Car", paid_on=date(2026, 1, 20), amount=445.0,
            interest=110.0, principal=335.0),
    Payment(debt_name="Chase", paid_on=date(2026, 2, 14), amount=250.0,
            interest=168.0, principal=82.0),
]


def test_totals_of_an_empty_ledger_are_zero_not_an_error():
    t = P.totals([])
    assert t["paid"] == 0.0 and t["count"] == 0 and t["first"] is None
    assert t["interest_share"] == 0.0


def test_totals_add_up():
    t = P.totals(LEDGER)
    assert t["paid"] == 945.0
    assert t["interest"] == 448.0
    assert t["principal"] == 497.0
    assert t["count"] == 3
    assert t["interest_share"] == pytest.approx(448.0 / 945.0)
    assert (t["first"], t["last"]) == (date(2026, 1, 14), date(2026, 2, 14))


def test_months_counts_calendar_months_not_payments():
    """Three payments across two months is two months of history, not three."""
    t = P.totals(LEDGER)
    assert t["months"] == 2
    assert t["avg_monthly"] == 472.5


def test_by_month_rolls_up_every_account_together():
    m = P.by_month(LEDGER)
    assert list(m["month"]) == ["Jan 2026", "Feb 2026"]
    assert list(m["payment"]) == [695.0, 250.0]
    assert list(m["interest"]) == [280.0, 168.0]


def test_by_debt_ranks_the_most_expensive_account_first():
    d = P.by_debt(LEDGER)
    assert list(d["debt"]) == ["Chase", "Car"]
    assert d.iloc[0]["paid"] == 500.0
    assert d.iloc[0]["interest"] == 338.0
    assert d.iloc[0]["payments"] == 2
    assert d.iloc[0]["interest_share"] == pytest.approx(0.676)
    assert d.iloc[0]["last"] == date(2026, 2, 14)


def test_cumulative_runs_the_totals_forward():
    c = P.cumulative(LEDGER)
    assert list(c["cum_interest"]) == [170.0, 280.0, 448.0]
    assert list(c["cum_paid"]) == [250.0, 695.0, 945.0]


def test_empty_frames_have_the_right_columns_so_charts_do_not_crash():
    for frame in (P.by_month([]), P.by_debt([]), P.cumulative([])):
        assert frame.empty
    assert "cum_interest" in P.cumulative([]).columns


def test_streak_counts_consecutive_months_back_from_today():
    ledger = [pay(date(2026, 1, 5)), pay(date(2026, 2, 5)), pay(date(2026, 3, 5))]
    assert P.streak(ledger, today=date(2026, 3, 20)) == 3
    # A gap in December breaks the run.
    assert P.streak(ledger, today=date(2026, 5, 20)) == 0


def test_streak_survives_a_month_you_have_not_paid_yet():
    """It's the 2nd and this month's payment isn't due — don't zero the streak."""
    ledger = [pay(date(2026, 1, 5)), pay(date(2026, 2, 5))]
    assert P.streak(ledger, today=date(2026, 3, 2)) == 2


def test_trailing_year_interest_excludes_older_payments():
    ledger = [pay(date(2024, 1, 5)), pay(date(2026, 2, 5))]
    assert P.interest_since_tracking(ledger, 365, today=date(2026, 3, 1)) == 100.0


# ------------------------------------------------------------------ persistence

@pytest.fixture()
def store(tmp_path, monkeypatch):
    """A throwaway database, wired the same way the app wires the real one."""
    import importlib
    from conftest import fresh_db
    db = fresh_db(monkeypatch, tmp_path, "ledger.db")
    uid = db.create_user("a@b.com", "correct-horse-battery")
    yield uid, db
    importlib.reload(db)


def test_a_payment_round_trips_through_the_database(store):
    uid, db = store
    db.save_debts(uid, [card()])
    debt = db.load_debts(uid)[0]
    db.record_payment(uid, P.build_payment(debt, 250.0, date(2026, 3, 14), note="payday"))

    (loaded,) = db.load_payments(uid)
    assert loaded.debt_name == "Chase"
    assert loaded.paid_on == date(2026, 3, 14)
    assert (loaded.amount, loaded.note) == (250.0, "payday")
    assert loaded.debt_id == debt.id
    assert loaded.id is not None


def test_due_day_survives_a_save(store):
    uid, db = store
    db.save_debts(uid, [card(due_day=27)])
    assert db.load_debts(uid)[0].due_day == 27


def test_debt_ids_are_stable_across_edits(store):
    """The page autosaves constantly; churning ids would orphan the ledger."""
    uid, db = store
    db.save_debts(uid, [card(), card(name="Car", due_day=1)])
    before = [d.id for d in db.load_debts(uid)]

    debts = db.load_debts(uid)
    debts[0].balance = 7_000.0
    db.save_debts(uid, debts)

    assert [d.id for d in db.load_debts(uid)] == before
    assert db.load_debts(uid)[0].balance == 7_000.0


def test_a_new_debt_gets_an_id_assigned_in_place(store):
    uid, db = store
    fresh = card(name="New card")
    assert fresh.id is None
    db.save_debts(uid, [fresh])
    assert fresh.id == db.load_debts(uid)[0].id


def test_a_debt_arriving_without_an_id_reattaches_by_name(store):
    """The editor can hand back rows with no id; history must still follow."""
    uid, db = store
    db.save_debts(uid, [card()])
    original = db.load_debts(uid)[0].id
    db.save_debts(uid, [card(balance=100.0)])  # id is None on this one
    assert db.load_debts(uid)[0].id == original


def test_renaming_an_account_carries_its_payment_history(store):
    uid, db = store
    db.save_debts(uid, [card()])
    debt = db.load_debts(uid)[0]
    db.record_payment(uid, P.build_payment(debt, 250.0, date(2026, 3, 14)))

    debt.name = "Chase Sapphire"
    db.save_debts(uid, [debt])

    (loaded,) = db.load_payments(uid)
    assert loaded.debt_name == "Chase Sapphire"
    assert loaded.debt_id == debt.id


def test_deleting_an_account_keeps_what_it_cost_you(store):
    """Closing a card must not erase the interest it charged you."""
    uid, db = store
    db.save_debts(uid, [card()])
    debt = db.load_debts(uid)[0]
    db.record_payment(uid, P.build_payment(debt, 250.0, date(2026, 3, 14)))

    db.save_debts(uid, [])
    (loaded,) = db.load_payments(uid)
    assert loaded.debt_name == "Chase"
    assert loaded.debt_id is None       # the link is gone
    assert loaded.interest > 0          # the history is not


def test_payments_are_scoped_to_their_owner(store):
    uid, db = store
    other = db.create_user("b@c.com", "correct-horse-battery")
    db.save_debts(uid, [card()])
    db.record_payment(uid, P.build_payment(db.load_debts(uid)[0], 250.0, date(2026, 3, 14)))

    assert len(db.load_payments(uid)) == 1
    assert db.load_payments(other) == []


def test_a_payment_can_be_deleted_but_only_by_its_owner(store):
    uid, db = store
    other = db.create_user("b@c.com", "correct-horse-battery")
    db.save_debts(uid, [card()])
    pid = db.record_payment(uid, P.build_payment(db.load_debts(uid)[0], 250.0,
                                                 date(2026, 3, 14)))

    db.delete_payment(other, pid)
    assert len(db.load_payments(uid)) == 1, "another user deleted someone else's row"
    db.delete_payment(uid, pid)
    assert db.load_payments(uid) == []


def test_load_payments_can_filter_to_one_account(store):
    uid, db = store
    db.save_debts(uid, [card(), card(name="Car")])
    for d in db.load_debts(uid):
        db.record_payment(uid, P.build_payment(d, 100.0, date(2026, 3, 14)))
    assert len(db.load_payments(uid)) == 2
    assert [p.debt_name for p in db.load_payments(uid, "Car")] == ["Car"]


def test_deleting_the_user_takes_the_ledger_with_them(store):
    uid, db = store
    db.save_debts(uid, [card()])
    db.record_payment(uid, P.build_payment(db.load_debts(uid)[0], 250.0, date(2026, 3, 14)))
    db.delete_user(uid)
    assert db.load_payments(uid) == []


def test_an_existing_database_migrates_to_the_new_schema(store):
    """A user who signed up before due dates existed must not be broken by them."""
    uid, db = store
    with db._conn() as con:
        con.execute("ALTER TABLE debts DROP COLUMN due_day")
        con.execute("DROP TABLE payments")
    db.init_db()  # the migration path the app runs on every boot

    db.save_debts(uid, [card(due_day=9)])
    assert db.load_debts(uid)[0].due_day == 9
    db.record_payment(uid, P.build_payment(db.load_debts(uid)[0], 250.0, date(2026, 3, 14)))
    assert len(db.load_payments(uid)) == 1
