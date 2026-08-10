"""Sign in / sign up / recover.

Session tokens live in the URL query string because Streamlit has no
first-party way to set a cookie. That is a real tradeoff — a URL can leak via
history, referrers, or a shared link — so it is mitigated rather than ignored:
only the token's *hash* is stored server-side, sessions expire on both an
absolute and an idle clock, "keep me signed in" is opt-in and short by default,
and signing out revokes the token immediately.
"""

from __future__ import annotations

import streamlit as st

from .. import db, security
from ..models import CREDIT_CARD, TERM_LOAN, Debt, Profile
from ..theme import palette
from .common import is_dark

SESSION_QS = "s"  # query-param key holding the session token
_STRENGTH_TONE = {0: "🔴", 1: "🔴", 2: "🟡", 3: "🟢", 4: "🟢"}

# The pitch. Three claims, each one something the app actually does — a landing
# page that oversells is the fastest way to lose the first session.
_POINTS = [
    ("principal", "Every number is yours",
     "Projections run off your balances, APRs and minimums — not a generic calculator."),
    ("interest", "See what the interest really costs",
     "Down to the cent per day, and the share of every dollar your lender keeps."),
    ("good", "Know what to do next",
     "Ranked actions, each one priced in the dollars and months it saves you."),
]


def restore_session() -> None:
    """Log the user back in from the session token in the URL."""
    if st.session_state.get("user_id"):
        # Switching pages rewrites the URL without its query string, dropping the
        # token — so a refresh anywhere but the default page lands on the sign-in
        # screen even though the session is still valid. Put it back on every run.
        live = st.session_state.get("token")
        if live and st.query_params.get(SESSION_QS) != live:
            st.query_params[SESSION_QS] = live
        return
    token = st.query_params.get(SESSION_QS)
    if not token:
        return
    uid = db.resolve_session(token)
    if uid:
        st.session_state.user_id = uid
        st.session_state.token = token
    else:
        # Expired or revoked — clear it so the stale token stops being echoed.
        del st.query_params[SESSION_QS]


def _sign_in(uid: int, remember: bool = True) -> None:
    token = db.start_session(uid, remember=remember)
    st.session_state.user_id = uid
    st.session_state.token = token
    st.query_params[SESSION_QS] = token
    for k in ("debts", "profile", "_saved_sig"):
        st.session_state.pop(k, None)
    st.rerun()


def sign_out() -> None:
    if st.session_state.get("token"):
        db.end_session(st.session_state.token)
    st.query_params.clear()
    st.session_state.clear()
    st.rerun()


def _seed_demo(uid: int) -> None:
    """A realistic starting portfolio so the app has something to say on day one."""
    db.save_debts(uid, [
        Debt(name="Chase Sapphire", kind=CREDIT_CARD, balance=8_400, apr=24.99,
             min_payment=35, min_percent=2.0, credit_limit=12_000, current_payment=250),
        Debt(name="Citi Double Cash", kind=CREDIT_CARD, balance=3_150, apr=21.49,
             min_payment=25, min_percent=2.0, credit_limit=6_000, current_payment=95),
        Debt(name="Store card", kind=CREDIT_CARD, balance=1_250, apr=29.99,
             min_payment=25, min_percent=3.0, credit_limit=2_000, current_payment=40),
        Debt(name="Car loan", kind=TERM_LOAN, subtype="Auto", balance=18_600, apr=7.4,
             min_payment=445, term_months=48, current_payment=445),
        Debt(name="Student loan", kind=TERM_LOAN, subtype="Student", balance=21_800,
             apr=5.5, min_payment=232, term_months=120, current_payment=232),
    ])
    db.save_profile(uid, Profile(monthly_budget=1_250, monthly_income=6_200,
                                 emergency_fund=1_800, strategy="avalanche"))


def _password_feedback(pw: str, email: str = "") -> None:
    """Strength meter, so a rejected password comes with a reason to fix it."""
    if not pw:
        return
    score, label = security.password_strength(pw)
    st.progress(score / 4, text=f"{_STRENGTH_TONE[score]} {label}")
    for problem in security.password_problems(pw, email)[:2]:
        st.caption(f"• {problem}")


def _show_recovery_codes(codes: list[str]) -> None:
    st.session_state.fresh_codes = codes


def render_recovery_codes_gate() -> bool:
    """After signup, make the user acknowledge their codes before continuing.

    Returns True if the gate is showing (caller should render nothing else).
    """
    codes = st.session_state.get("fresh_codes")
    if not codes:
        return False

    st.success("Account created.")
    st.markdown("### Save your recovery codes")
    st.warning(
        "There is no email server here, so **these codes are the only way back into your "
        "account** if you forget your password. Each one works once. Store them somewhere "
        "other than this browser — you will not see them again."
    )
    st.code("\n".join(codes), language=None)
    st.download_button("Download codes", "\n".join(codes),
                       file_name="debt-manager-recovery-codes.txt", mime="text/plain")
    if st.checkbox("I've saved these somewhere safe"):
        if st.button("Continue to my plan", type="primary"):
            del st.session_state.fresh_codes
            st.rerun()
    return True


def _pitch() -> None:
    """The left half of the sign-in screen."""
    p = palette(is_dark())
    points = "".join(
        f'<li><span class="dot" style="background:{p[role]}"></span>'
        f'<span><b>{head}</b>{body}</span></li>'
        for role, head, body in _POINTS
    )
    st.markdown(
        '<div class="auth-wrap">'
        '<div class="auth-lockup"><span class="auth-mark">DM</span>Debt Manager</div>'
        '<h1 class="auth-title">Know exactly what<br>your debt costs you.</h1>'
        '<p class="auth-sub">Enter your cards and loans once. See where every dollar '
        'goes, when the balance hits zero, and what it would take to get there '
        'sooner.</p>'
        f'<ul class="auth-points">{points}</ul>'
        '</div>',
        unsafe_allow_html=True,
    )


def render() -> None:
    st.markdown('<div style="height:6vh"></div>', unsafe_allow_html=True)
    left, _, right = st.columns([1, 0.1, 1], vertical_alignment="center")
    with left:
        _pitch()
    with right:
        with st.container(key="authcard"):
            _forms()
        st.markdown(
            '<p class="auth-foot">Your data stays in this app\'s database and is only used to '
            'compute your projections. Passwords are hashed with bcrypt, session tokens are '
            'stored hashed, and nothing is sent anywhere else.</p>',
            unsafe_allow_html=True,
        )


def _forms() -> None:
    tab_in, tab_up, tab_reset = st.tabs(["Sign in", "Create account", "Forgot password"])

    # -------------------------------------------------------------- sign in
    with tab_in:
        with st.form("signin"):
            email = st.text_input("Email", key="in_email", autocomplete="username")
            pw = st.text_input("Password", type="password", key="in_pw",
                               autocomplete="current-password")
            remember = st.checkbox(
                "Keep me signed in on this device", value=False,
                help="Leave this off on a shared computer — the sign-in link is held in "
                     "the page URL.",
            )
            if st.form_submit_button("Sign in", type="primary", width="stretch"):
                try:
                    _sign_in(db.verify_user(email, pw), remember=remember)
                except db.RateLimited as e:
                    st.error(str(e))
                except db.AuthError as e:
                    st.error(str(e))
                    left = db.attempts_remaining(email)
                    if 0 < left <= 2:
                        st.warning(f"{left} attempt{'s' if left != 1 else ''} left before "
                                   "this email is locked for 15 minutes.")

    # --------------------------------------------------------- create account
    with tab_up:
        # A form, not loose widgets: clicking a button while a text input still
        # has focus can hand the callback the *previous* value of that input, so
        # a correctly typed confirmation would come back empty and the account
        # would be refused for a mismatch that never happened. A form submits
        # every field's current value in one message, which is also why the two
        # tabs either side of this one never had the problem. The cost is that
        # the strength meter refreshes on submit rather than on each keystroke.
        with st.form("signup"):
            email_up = st.text_input("Email", key="up_email", autocomplete="username")
            pw_up = st.text_input("Password", type="password", key="up_pw",
                                  autocomplete="new-password",
                                  help=f"At least {security.MIN_LENGTH} characters. A memorable "
                                       "passphrase of a few words beats a short cryptic one.")
            _password_feedback(pw_up, email_up)
            pw2 = st.text_input("Confirm password", type="password", key="up_pw2",
                                autocomplete="new-password")
            demo = st.checkbox("Explore with clearly labeled sample debts", value=False)

            if st.form_submit_button("Create account", type="primary", width="stretch",
                                     key="do_signup"):
                if pw_up != pw2:
                    st.error("The two passwords don't match.")
                else:
                    try:
                        uid = db.create_user(email_up, pw_up)
                        if demo:
                            _seed_demo(uid)
                        _show_recovery_codes(db.issue_recovery_codes(uid))
                        _sign_in(uid, remember=False)
                    except db.AuthError as e:
                        st.error(str(e))

    # ------------------------------------------------------------- recovery
    with tab_reset:
        st.caption("Use one of the recovery codes you saved when you signed up. "
                   "Each code works once.")
        with st.form("reset"):
            email_r = st.text_input("Email", key="rs_email", autocomplete="username")
            code = st.text_input("Recovery code", key="rs_code", placeholder="ABCDE-12345")
            new_pw = st.text_input("New password", type="password", key="rs_pw",
                                   autocomplete="new-password")
            new_pw2 = st.text_input("Confirm new password", type="password", key="rs_pw2",
                                    autocomplete="new-password")
            if st.form_submit_button("Reset password", type="primary", width="stretch"):
                if new_pw != new_pw2:
                    st.error("The two passwords don't match.")
                else:
                    try:
                        uid = db.reset_password_with_code(email_r, code, new_pw)
                        st.success("Password reset. You can sign in now — every other "
                                   "session has been signed out.")
                        st.info(f"You have **{db.unused_recovery_code_count(uid)}** unused "
                                "recovery codes left. Generate a fresh set from the Account "
                                "page once you're back in.")
                    except db.AuthError as e:
                        st.error(str(e))
