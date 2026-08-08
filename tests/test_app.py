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


# --------------------------------------------------------------- the login screen

def _auth_screen(tmp_path, monkeypatch):
    """Render the real sign-in / sign-up / recovery page."""
    monkeypatch.setenv("DEBTMANAGER_DB", str(tmp_path / "auth_ui.db"))
    import debtapp.db as db
    importlib.reload(db)
    db.init_db()

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
