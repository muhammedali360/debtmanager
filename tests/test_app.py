"""End-to-end UI tests: drive the real Streamlit pages, assert no exceptions.

Each test gets its own throwaway database via the ``DEBTMANAGER_DB`` env var,
which ``debtapp.db`` reads at import time — hence the reload in the fixture.
"""

import importlib
from datetime import date, timedelta
from pathlib import Path

import pytest
from conftest import fresh_db
from streamlit.testing.v1 import AppTest

from debtapp.models import CREDIT_CARD, TERM_LOAN, Debt, Profile

# Due days chosen relative to the real "today" so status is deterministic on any
# day the suite happens to run: one payment is due today, one is three days late.
TODAY = date.today()
DUE_TODAY = TODAY.day
DUE_LATE = (TODAY - timedelta(days=3)).day


@pytest.fixture()
def user(tmp_path, monkeypatch):
    db = fresh_db(monkeypatch, tmp_path)
    uid = db.create_user("a@b.com", "hunter2hunter2")
    db.save_debts(uid, [
        Debt(name="Chase", kind=CREDIT_CARD, balance=8_400, apr=24.99, min_payment=35,
             min_percent=2.0, credit_limit=12_000, current_payment=250, due_day=DUE_TODAY),
        Debt(name="Store card", kind=CREDIT_CARD, balance=1_250, apr=29.99, min_payment=25,
             min_percent=3.0, credit_limit=2_000, current_payment=40, due_day=DUE_LATE),
        Debt(name="Car loan", kind=TERM_LOAN, subtype="Auto", balance=18_600, apr=7.4,
             min_payment=445, current_payment=445),
    ])
    db.save_profile(uid, Profile(monthly_budget=1_250, monthly_income=6_200,
                                 emergency_fund=1_800, strategy="avalanche"))
    yield uid, db
    importlib.reload(db)


def _page(module_name: str, uid: int, db) -> AppTest:
    """Run one page function with a logged-in session."""
    def script():
        # AppTest exec's this standalone, so it must import everything itself.
        import importlib as il

        import streamlit as st
        from debtapp.ui.common import inject_css, load_state
        mod = il.import_module(st.session_state["_mod"])
        inject_css()
        load_state(st.session_state["user_id"])
        mod.render()

    at = AppTest.from_function(script)
    at.session_state["user_id"] = uid
    at.session_state["_mod"] = module_name
    return at.run(timeout=60)


PAGES = ["debtapp.ui.plan", "debtapp.ui.debts", "debtapp.ui.account", "debtapp.ui.ledger"]


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_without_error(page, user):
    uid, db = user
    at = _page(page, uid, db)
    assert not at.exception, f"{page} raised: {[e.value for e in at.exception]}"


@pytest.mark.parametrize("page", PAGES)
def test_page_renders_with_no_debts_at_all(page, user):
    """A brand-new account must not crash any page."""
    uid, db = user
    db.save_debts(uid, [])
    at = _page(page, uid, db)
    assert not at.exception, f"{page} raised on empty state: {[e.value for e in at.exception]}"


def test_a_brand_new_account_gets_asked_for_a_debt_rather_than_redirected(user):
    """Onboarding used to be a checkbox on the signup form; untick it and the
    empty app pointed you at a different page. Four fields, answered live."""
    uid, db = user
    db.save_debts(uid, [])
    budget_before = db.load_profile(uid).monthly_budget
    at = _page("debtapp.ui.plan", uid, db)
    assert not at.exception
    assert "start with one account" in " ".join(m.value for m in at.markdown)
    assert at.button(key="ob_add").disabled, "nothing has been typed yet"

    at.text_input(key="ob_name").set_value("First card")
    at.number_input(key="ob_balance").set_value(5_000.0)
    at.number_input(key="ob_apr").set_value(19.99)
    at.number_input(key="ob_payment").set_value(200.0)
    at = at.run(timeout=60)
    assert not at.exception

    # The projection is on screen *before* the button is pressed — that is the
    # whole point of asking for four fields instead of eight.
    assert "clear this in" in " ".join(m.value for m in at.markdown)
    assert db.load_debts(uid) == [], "nothing saved until the user says so"

    at.button(key="ob_add").click().run(timeout=60)
    assert not at.exception
    (debt,) = db.load_debts(uid)
    assert (debt.name, debt.balance, debt.apr, debt.current_payment) == \
        ("First card", 5_000.0, 19.99, 200.0)
    # Onboarding must not pin the budget to this one account's payment: left
    # alone, `effective_budget` keeps deriving it from what the accounts
    # actually pay, so adding a second debt raises it by itself.
    assert db.load_profile(uid).monthly_budget == budget_before


def test_plan_shows_the_headline_numbers(user):
    uid, db = user
    at = _page("debtapp.ui.plan", uid, db)
    body = " ".join(m.value for m in at.markdown)
    assert "Total owed" in body and "Debt-free" in body
    assert "$28,250" in body  # 8,400 + 1,250 + 18,600


def test_plan_leads_with_one_suggestion_and_folds_the_rest_away(user):
    """The suggestions are ranked by dollars at stake, which is worth nothing if
    the page shows twenty of them at once."""
    uid, db = user
    at = _page("debtapp.ui.plan", uid, db)
    body = " ".join(m.value for m in at.markdown)
    assert "What to do next" in body

    blocks = [m for m in at.markdown if 'class="ins ' in m.value]
    assert blocks, "no suggestions rendered at all"
    assert blocks[0].value.count('class="ins ') == 1, "the lead card must stand alone"
    assert any(e.label.startswith("More suggestions") for e in at.expander), \
        "the rest of the ranking has to stay reachable"


def test_the_extra_payment_slider_prices_a_bigger_payment(user):
    uid, db = user
    at = _page("debtapp.ui.plan", uid, db)
    before = " ".join(m.value for m in at.markdown)
    at.slider[0].set_value(500).run(timeout=60)  # extra per month
    assert not at.exception
    after = " ".join(m.value for m in at.markdown)
    assert before != after, "moving the extra-payment slider changed nothing"
    assert "Interest saved" in after and "Time saved" in after


def test_debts_page_edits_persist_to_the_database(user):
    """The whole point of the login: change a number, come back, still there."""
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    assert not at.exception
    at.number_input[0].set_value(2_000.0).run(timeout=60)  # monthly budget
    assert not at.exception
    assert db.load_profile(uid).monthly_budget == 2_000.0


def _balance(db, uid: int, name: str) -> float:
    return next(d for d in db.load_debts(uid) if d.name == name).balance


def _typed(at, key: str, delta: dict, added=None):
    """Post a grid's accumulated edits the way the browser does.

    The browser holds one delta per grid and grows it as you move across the
    row, so each call passes everything typed so far — which is the point: if
    the grid is handed data that changes identity mid-edit, the whole delta
    lands on a widget that no longer exists and is dropped.
    """
    at.session_state[key] = {"edited_rows": delta, "added_rows": added or [],
                             "deleted_rows": []}
    return at.run(timeout=60)


def test_a_blank_minimum_percent_falls_back_to_the_model_default(user):
    """A cell left empty means "I didn't say", not "zero".

    `min_percent` is the whole minimum-payment model for a card — the required
    payment is `max(min_payment, min_percent% of balance)`. Coercing a blank to
    0.0 quietly deleted the percentage floor, so a card added without opening
    the payment details projected against a minimum no lender would accept.
    """
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    at = _typed(at, "ed_debts_0_basic", {},
                [{"name": "New card", "type": "Credit card", "balance": 4_000.0, "apr": 22.0}])
    assert not at.exception

    added = next(d for d in db.load_debts(uid) if d.name == "New card")
    assert added.min_percent == 2.0, "a blank percentage silently removed the minimum floor"
    # 2% of the balance plus this month's interest, not $0.
    assert added.required_payment(4_000.0) == pytest.approx(80.0)


def test_the_grid_takes_cards_and_loans_in_one_table(user):
    """The card/loan split is a modelling distinction, not a question the user
    should have to answer before typing anything."""
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    at = _typed(at, "ed_debts_0_basic", {},
                [{"name": "Bike loan", "type": "Personal", "balance": 3_000.0, "apr": 9.0}])
    assert not at.exception

    added = next(d for d in db.load_debts(uid) if d.name == "Bike loan")
    assert added.kind == TERM_LOAN and added.subtype == "Personal"
    assert not added.is_card


def test_hiding_the_payment_details_does_not_wipe_them(user):
    """The detail columns are hidden by config, not dropped from the frame. If
    they were dropped, every autosave from the basic view would zero a minimum
    the user set months ago."""
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    at = _typed(at, "ed_debts_0_basic", {0: {"balance": 7_000.0}})
    assert not at.exception

    chase = next(d for d in db.load_debts(uid) if d.name == "Chase")
    assert chase.balance == 7_000.0
    assert (chase.min_payment, chase.min_percent, chase.credit_limit) == (35.0, 2.0, 12_000.0)
    assert chase.due_day == DUE_TODAY


def test_editing_one_cell_does_not_throw_away_the_next_one(user):
    """Each edit is autosaved back into the rows the grid was built from. If the
    grid is rebuilt from those rows it becomes a different widget, and the cell
    the user has moved on to is edited against a widget that has already gone."""
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)

    at = _typed(at, "ed_debts_0_basic", {0: {"balance": 9_000.0}})
    assert _balance(db, uid, "Chase") == 9_000.0

    at = _typed(at, "ed_debts_0_basic", {0: {"balance": 9_000.0, "apr": 19.99}})
    assert not at.exception

    chase = next(d for d in db.load_debts(uid) if d.name == "Chase")
    assert chase.apr == 19.99, "the second cell's edit was dropped"
    assert chase.balance == 9_000.0, "the first cell's edit was lost on the way"


def test_a_payment_logged_on_the_ledger_survives_a_return_to_my_debts(user):
    """The grids hold their edits as a delta, so an edit made before the payment
    would otherwise be replayed over the balance the payment just reduced —
    writing the old figure back and silently undoing it."""
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    at = _typed(at, "ed_debts_0_basic", {0: {"balance": 8_000.0}})
    assert _balance(db, uid, "Chase") == 8_000.0

    chase_id = next(d for d in db.load_debts(uid) if d.name == "Chase").id
    at.session_state["_mod"] = "debtapp.ui.ledger"
    at.run(timeout=60)
    at.button(key=f"due_btn_{chase_id}_Chase").click().run(timeout=60)
    assert not at.exception
    paid_down = _balance(db, uid, "Chase")
    assert paid_down < 8_000.0

    at.session_state["_mod"] = "debtapp.ui.debts"
    at.run(timeout=60)
    assert not at.exception
    assert _balance(db, uid, "Chase") == pytest.approx(paid_down)


# ------------------------------------------------- acting on whole accounts

def _pick(at, name: str):
    """Select one account in the debts page's account picker."""
    ms = at.multiselect(key="manage_pick_0")
    i = next(n for n, o in enumerate(ms.options) if o.startswith(name))
    return ms.set_value([i])


def test_removing_an_account_needs_a_confirmation_first(user):
    """One click must not be able to delete an account outright — that is the
    behaviour of the table's own trash icon, and the reason this exists."""
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    _pick(at, "Store card").run(timeout=60)
    at.button(key="ask_remove").click().run(timeout=60)
    assert not at.exception

    assert any("cannot be undone" in w.value for w in at.warning), "no confirmation shown"
    assert {d.name for d in db.load_debts(uid)} == {"Chase", "Store card", "Car loan"}

    at.button(key="cancel_remove").click().run(timeout=60)
    assert {d.name for d in db.load_debts(uid)} == {"Chase", "Store card", "Car loan"}


def test_confirming_a_removal_deletes_the_account_but_keeps_its_payments(user):
    uid, db = user
    _log(db, uid, "Store card", 40.0, TODAY)

    at = _page("debtapp.ui.debts", uid, db)
    _pick(at, "Store card").run(timeout=60)
    at.button(key="ask_remove").click().run(timeout=60)
    at.button(key="do_remove").click().run(timeout=60)
    assert not at.exception

    assert {d.name for d in db.load_debts(uid)} == {"Chase", "Car loan"}
    # The ledger is history, not projection: closing the account must not erase
    # what it already cost.
    (payment,) = db.load_payments(uid)
    assert payment.debt_name == "Store card"
    assert payment.debt_id is None


def test_removing_an_account_drops_it_from_a_custom_payoff_order(user):
    uid, db = user
    db.save_profile(uid, Profile(monthly_budget=1_250, strategy="custom",
                                 custom_order=["Store card", "Chase", "Car loan"]))
    at = _page("debtapp.ui.debts", uid, db)
    _pick(at, "Store card").run(timeout=60)
    at.button(key="ask_remove").click().run(timeout=60)
    at.button(key="do_remove").click().run(timeout=60)
    assert not at.exception
    assert db.load_profile(uid).custom_order == ["Chase", "Car loan"]


def test_marking_an_account_paid_off_zeroes_the_payment_too(user):
    """A zero balance still carrying a payment would keep spending budget on an
    account that no longer exists."""
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    _pick(at, "Store card").run(timeout=60)
    at.button(key="mark_paid").click().run(timeout=60)
    assert not at.exception

    after = next(d for d in db.load_debts(uid) if d.name == "Store card")
    assert after.balance == 0.0
    assert after.current_payment == 0.0
    assert len(db.load_debts(uid)) == 3, "paying off must not delete the account"


def test_the_account_actions_stay_out_of_the_way_until_something_is_picked(user):
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    idle = {b.key: b.disabled for b in at.button if b.key in ("mark_paid", "ask_remove")}
    assert idle == {"mark_paid": True, "ask_remove": True}
    assert not any(b.key in ("do_remove", "cancel_remove") for b in at.button)

    _pick(at, "Chase").run(timeout=60)
    live = {b.key: b.disabled for b in at.button if b.key in ("mark_paid", "ask_remove")}
    assert live == {"mark_paid": False, "ask_remove": False}


def test_a_never_paying_plan_does_not_crash_any_page(user):
    """Negative amortization is the nastiest input; every page must survive it."""
    uid, db = user
    db.save_debts(uid, [Debt(name="Trap", kind=TERM_LOAN, balance=10_000, apr=29.99,
                             min_payment=100, current_payment=100)])
    db.save_profile(uid, Profile(monthly_budget=100, strategy="avalanche"))
    for page in PAGES:
        at = _page(page, uid, db)
        assert not at.exception, f"{page} crashed on a non-terminating plan"


def test_zero_apr_and_zero_balance_rows_are_survivable(user):
    uid, db = user
    db.save_debts(uid, [
        Debt(name="Paid off", kind=CREDIT_CARD, balance=0, apr=0, min_payment=0),
        Debt(name="Zero interest", kind=TERM_LOAN, balance=1_200, apr=0.0, min_payment=100),
    ])
    for page in PAGES:
        at = _page(page, uid, db)
        assert not at.exception, f"{page} crashed on zero-APR / zero-balance input"


# ------------------------------------------------------- due dates and the ledger

def _log(db, uid, name: str, amount: float, when: date):
    from debtapp import payments as P
    debt = next(d for d in db.load_debts(uid) if d.name == name)
    return db.record_payment(uid, P.build_payment(debt, amount, when))


def test_the_plan_asks_about_a_payment_that_is_due(user):
    uid, db = user
    at = _page("debtapp.ui.plan", uid, db)
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Payments coming up" in body
    assert "Store card" in body and "3 days late" in body
    assert "Chase" in body and "due today" in body
    assert any(b.label == "I paid this" for b in at.button)


def test_confirming_a_payment_records_it_and_moves_the_balance(user):
    """The whole loop: the app asks, you say yes, and every number updates."""
    uid, db = user
    debt = next(d for d in db.load_debts(uid) if d.name == "Store card")
    at = _page("debtapp.ui.ledger", uid, db)

    at.button(key=f"due_btn_{debt.id}_Store card").click().run(timeout=60)
    assert not at.exception

    (payment,) = db.load_payments(uid)
    assert payment.debt_name == "Store card"
    assert payment.amount == 40.0
    assert payment.paid_on == TODAY
    # $1,250 at 29.99% accrues $31.24/mo, so $40 buys only $8.76 of principal.
    assert payment.interest == pytest.approx(31.24, abs=0.01)
    assert payment.principal == pytest.approx(8.76, abs=0.01)

    after = next(d for d in db.load_debts(uid) if d.name == "Store card")
    assert after.balance == pytest.approx(1_241.24, abs=0.01)
    assert after.balance == payment.balance_after


def test_backfilling_an_old_payment_does_not_touch_the_current_balance(user):
    """The balance a user typed in already reflects payments they made months
    ago. Deducting a backfilled payment again understates the debt and corrupts
    every projection downstream."""
    uid, db = user
    at = _page("debtapp.ui.ledger", uid, db)
    assert not at.exception
    before = next(d for d in db.load_debts(uid) if d.name == "Chase").balance

    at.date_input(key="log_when").set_value(TODAY - timedelta(days=60))
    at = at.run(timeout=60)
    # Backdating flips the default: this is history, not new money.
    assert at.checkbox[0].value is False

    at.button(key="FormSubmitter:log_payment_form-Log this payment").click()
    at = at.run(timeout=60)
    assert not at.exception

    assert next(d for d in db.load_debts(uid) if d.name == "Chase").balance == before
    (row,) = db.load_payments(uid)
    assert row.paid_on == TODAY - timedelta(days=60)
    assert row.balance_after is None  # no balance claimed for a date we can't know
    assert row.interest > 0           # but it still counts toward what the debt cost


def test_logging_todays_payment_does_reduce_the_balance(user):
    """The other half of the same choice — the default must flip back."""
    uid, db = user
    at = _page("debtapp.ui.ledger", uid, db)
    before = next(d for d in db.load_debts(uid) if d.name == "Chase").balance
    assert at.checkbox[0].value is True

    at.button(key="FormSubmitter:log_payment_form-Log this payment").click()
    at = at.run(timeout=60)
    assert not at.exception
    after = next(d for d in db.load_debts(uid) if d.name == "Chase").balance
    assert after < before


def test_a_logged_payment_stops_the_app_asking_again(user):
    uid, db = user
    _log(db, uid, "Store card", 40.0, TODAY)
    at = _page("debtapp.ui.plan", uid, db)
    assert not at.exception
    assert "3 days late" not in " ".join(m.value for m in at.markdown)

    # Asserted against the due panel's own buttons rather than against the page
    # text: the Plan page's suggestions discuss accounts by name, so "Store card
    # is not mentioned anywhere" stopped meaning "the app isn't asking about it".
    # Chase is still due and unpaid, which is what makes this a real check.
    ids = {d.name: d.id for d in db.load_debts(uid)}
    keys = {b.key for b in at.button}
    assert f"due_btn_{ids['Store card']}_Store card" not in keys, \
        "still asking for a payment that was already logged"
    assert f"due_btn_{ids['Chase']}_Chase" in keys


def test_the_ledger_counts_what_the_debt_has_actually_cost(user):
    uid, db = user
    _log(db, uid, "Chase", 250.0, TODAY - timedelta(days=40))
    _log(db, uid, "Chase", 250.0, TODAY - timedelta(days=10))
    _log(db, uid, "Car loan", 445.0, TODAY - timedelta(days=10))

    at = _page("debtapp.ui.ledger", uid, db)
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Total paid" in body and "$945" in body
    assert "Gone to interest" in body
    assert "Off your balance" in body
    assert "never touched your balance" in body


def test_the_ledger_page_survives_having_no_payments(user):
    uid, db = user
    at = _page("debtapp.ui.ledger", uid, db)
    assert not at.exception
    assert any("No payments logged yet" in i.value for i in at.info)


def test_insights_escalate_an_overdue_payment_above_everything_else(user):
    uid, db = user
    at = _page("debtapp.ui.plan", uid, db)
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "past due" in body
    assert "30 days" in body  # the credit-report threshold, not just the fee


def test_insights_report_real_interest_once_there_is_history(user):
    uid, db = user
    _log(db, uid, "Chase", 250.0, TODAY - timedelta(days=40))
    _log(db, uid, "Chase", 250.0, TODAY - timedelta(days=10))
    at = _page("debtapp.ui.plan", uid, db)
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "handed over" in body and "in interest since" in body


def test_pages_survive_an_account_that_was_deleted_after_being_paid(user):
    """Orphaned ledger rows (debt_id NULL) must not break rendering."""
    uid, db = user
    _log(db, uid, "Chase", 250.0, TODAY - timedelta(days=10))
    db.save_debts(uid, [d for d in db.load_debts(uid) if d.name != "Chase"])
    for page in PAGES:
        at = _page(page, uid, db)
        assert not at.exception, f"{page} crashed on an orphaned payment"


def test_debts_with_no_due_day_are_nudged_to_add_one(user):
    uid, db = user
    debts = db.load_debts(uid)
    for d in debts:
        d.due_day = 0
    db.save_debts(uid, debts)
    at = _page("debtapp.ui.plan", uid, db)
    assert not at.exception
    assert "due day" in " ".join(m.value for m in at.markdown)
    assert not any(b.label == "I paid this" for b in at.button)


# --------------------------------------------------------------- the login screen

def _auth_screen(tmp_path, monkeypatch):
    """Render the real sign-in / sign-up / recovery page."""
    db = fresh_db(monkeypatch, tmp_path, "auth_ui.db")

    def script():
        import streamlit as st
        from debtapp import db as d
        from debtapp.ui import auth
        from debtapp.ui.common import inject_css
        d.init_db()
        inject_css()
        auth.restore_session()
        if st.session_state.get("user_id"):
            if not auth.render_recovery_codes_gate():
                st.markdown("SIGNED_IN")
        else:
            auth.render()

    return AppTest.from_function(script).run(timeout=60), db


def test_login_screen_renders(tmp_path, monkeypatch):
    at, _ = _auth_screen(tmp_path, monkeypatch)
    assert not at.exception
    body = " ".join(m.value for m in at.markdown)
    assert "Debt Manager" in body


def test_signup_creates_the_account_and_gates_on_recovery_codes(tmp_path, monkeypatch):
    at, db = _auth_screen(tmp_path, monkeypatch)
    at.text_input(key="up_email").set_value("new@user.com")
    at.text_input(key="up_pw").set_value("correct-horse-battery")
    at.text_input(key="up_pw2").set_value("correct-horse-battery")
    at.button(key="do_signup").click().run(timeout=60)

    assert not at.exception
    assert db.verify_user("new@user.com", "correct-horse-battery")
    # The user must see their codes before reaching the app.
    body = " ".join(m.value for m in at.markdown)
    assert "recovery codes" in body.lower()
    assert "SIGNED_IN" not in body


def test_signup_rejects_a_weak_password_in_the_ui(tmp_path, monkeypatch):
    at, db = _auth_screen(tmp_path, monkeypatch)
    at.text_input(key="up_email").set_value("weak@user.com")
    at.text_input(key="up_pw").set_value("password123")
    at.text_input(key="up_pw2").set_value("password123")
    at.button(key="do_signup").click().run(timeout=60)

    assert not at.exception
    assert at.error, "expected a visible error"
    with pytest.raises(db.AuthError):
        db.verify_user("weak@user.com", "password123")


def test_signup_rejects_mismatched_passwords(tmp_path, monkeypatch):
    at, _ = _auth_screen(tmp_path, monkeypatch)
    at.text_input(key="up_email").set_value("mm@user.com")
    at.text_input(key="up_pw").set_value("correct-horse-battery")
    at.text_input(key="up_pw2").set_value("different-horse-battery")
    at.button(key="do_signup").click().run(timeout=60)
    assert any("don't match" in e.value for e in at.error)


def test_signing_in_with_a_bad_password_shows_an_error(tmp_path, monkeypatch):
    at, db = _auth_screen(tmp_path, monkeypatch)
    db.create_user("real@user.com", "correct-horse-battery")
    at.text_input(key="in_email").set_value("real@user.com")
    at.text_input(key="in_pw").set_value("wrong-horse-battery")
    at.button[0].click().run(timeout=60)
    assert not at.exception
    assert any("Incorrect" in e.value for e in at.error)


def test_a_valid_session_token_in_the_url_restores_the_login(tmp_path, monkeypatch):
    """The whole point of persistence: refresh the page, stay signed in."""
    at, db = _auth_screen(tmp_path, monkeypatch)
    uid = db.create_user("back@user.com", "correct-horse-battery")
    token = db.start_session(uid)

    at.query_params["s"] = token
    at.run(timeout=60)
    assert not at.exception
    assert "user_id" in at.session_state and at.session_state["user_id"] == uid


def test_a_revoked_session_token_does_not_restore_the_login(tmp_path, monkeypatch):
    at, db = _auth_screen(tmp_path, monkeypatch)
    uid = db.create_user("gone@user.com", "correct-horse-battery")
    token = db.start_session(uid)
    db.end_session(token)

    at.query_params["s"] = token
    at.run(timeout=60)
    assert not at.exception
    assert "user_id" not in at.session_state
    assert "s" not in at.query_params  # stale token cleared from the URL


def test_recovery_flow_resets_the_password_from_the_ui(tmp_path, monkeypatch):
    at, db = _auth_screen(tmp_path, monkeypatch)
    uid = db.create_user("lost@user.com", "correct-horse-battery")
    code = db.issue_recovery_codes(uid)[0]

    at.text_input(key="rs_email").set_value("lost@user.com")
    at.text_input(key="rs_code").set_value(code)
    at.text_input(key="rs_pw").set_value("brand-new-passphrase")
    at.text_input(key="rs_pw2").set_value("brand-new-passphrase")
    at.button[2].click().run(timeout=60)  # the reset form's submit

    assert not at.exception
    assert db.verify_user("lost@user.com", "brand-new-passphrase") == uid


# ------------------------------------------------------------ staying signed in

def test_the_session_token_survives_a_page_switch(user):
    """Streamlit rewrites the URL without its query string when you change
    pages, which drops the session token. If it isn't re-asserted, refreshing on
    any page but the default silently signs the user out."""
    uid, db = user
    token = db.start_session(uid)

    def script():
        from debtapp.ui import auth
        auth.restore_session()

    at = AppTest.from_function(script)
    at.session_state["user_id"] = uid
    at.session_state["token"] = token
    at.run(timeout=60)  # no query param, exactly as after a page switch

    assert not at.exception
    assert at.query_params.get("s") == [token]  # AppTest reports params as lists


def test_the_real_entry_point_boots_signed_in(user):
    """Every other test calls a page's ``render()`` directly, so nothing covers
    app.py's own wiring — the one place a bad call reaches every single user."""
    uid, db = user
    token = db.start_session(uid)

    at = AppTest.from_file(str(Path(__file__).parent.parent / "app.py"))
    at.query_params["s"] = token
    at.run(timeout=90)

    assert not at.exception, f"app.py raised: {[e.value for e in at.exception]}"
