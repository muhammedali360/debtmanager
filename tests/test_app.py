"""End-to-end UI tests: drive the real Streamlit pages, assert no exceptions.

Each test gets its own throwaway database via the ``DEBTMANAGER_DB`` env var,
which ``debtapp.db`` reads at import time — hence the reload in the fixture.
"""

import importlib

import pytest
from streamlit.testing.v1 import AppTest

from debtapp.models import CREDIT_CARD, TERM_LOAN, Debt, Profile


@pytest.fixture()
def user(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBTMANAGER_DB", str(tmp_path / "t.db"))
    import debtapp.db as db
    importlib.reload(db)
    db.init_db()
    uid = db.create_user("a@b.com", "hunter2hunter2")
    db.save_debts(uid, [
        Debt(name="Chase", kind=CREDIT_CARD, balance=8_400, apr=24.99, min_payment=35,
             min_percent=2.0, credit_limit=12_000, current_payment=250),
        Debt(name="Store card", kind=CREDIT_CARD, balance=1_250, apr=29.99, min_payment=25,
             min_percent=3.0, credit_limit=2_000, current_payment=40),
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


PAGES = ["debtapp.ui.dashboard", "debtapp.ui.debts", "debtapp.ui.insights_page",
         "debtapp.ui.scenarios", "debtapp.ui.account"]


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


def test_dashboard_shows_the_headline_numbers(user):
    uid, db = user
    at = _page("debtapp.ui.dashboard", uid, db)
    body = " ".join(m.value for m in at.markdown)
    assert "Total owed" in body and "Debt-free" in body
    assert "$28,250" in body  # 8,400 + 1,250 + 18,600


def test_insights_page_produces_ranked_cards(user):
    uid, db = user
    at = _page("debtapp.ui.insights_page", uid, db)
    body = " ".join(m.value for m in at.markdown)
    assert 'class="ins ' in body
    assert "Identified savings" in body


def test_scenarios_slider_changes_the_projection(user):
    uid, db = user
    at = _page("debtapp.ui.scenarios", uid, db)
    before = " ".join(m.value for m in at.markdown)
    at.slider[0].set_value(500).run(timeout=60)  # extra per month
    assert not at.exception
    after = " ".join(m.value for m in at.markdown)
    assert before != after, "moving the extra-payment slider changed nothing"


def test_debts_page_edits_persist_to_the_database(user):
    """The whole point of the login: change a number, come back, still there."""
    uid, db = user
    at = _page("debtapp.ui.debts", uid, db)
    assert not at.exception
    at.number_input[0].set_value(2_000.0).run(timeout=60)  # monthly budget
    assert not at.exception
    assert db.load_profile(uid).monthly_budget == 2_000.0


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
