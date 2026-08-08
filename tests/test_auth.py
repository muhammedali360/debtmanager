"""Authentication: password policy, throttling, sessions, and recovery."""

import importlib
import time

import pytest

from debtapp import security

GOOD_PW = "correct-horse-battery"
OTHER_PW = "purple-monsoon-ladder"


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("DEBTMANAGER_DB", str(tmp_path / "auth.db"))
    import debtapp.db as _db
    importlib.reload(_db)
    _db.init_db()
    yield _db
    importlib.reload(_db)


# --------------------------------------------------------------- password policy

@pytest.mark.parametrize("pw", [
    "short", "password123", "Password1", "aaaaaaaaaaaa", "abcdefghijkl",
    "123456789012", "qwertyuiop12",
])
def test_weak_passwords_are_rejected(pw):
    assert security.password_problems(pw), f"{pw!r} should have been rejected"


@pytest.mark.parametrize("pw", [
    "correct-horse-battery", "Tr0ub4dor&3xkcd!", "my dog ate the homework",
])
def test_reasonable_passwords_are_accepted(pw):
    assert security.password_problems(pw) == []


def test_password_containing_the_email_is_rejected():
    assert any("email" in p for p in
               security.password_problems("muhammed-secret-99", "muhammed@risely.ai"))


def test_password_over_bcrypt_byte_limit_is_rejected():
    """bcrypt silently truncates past 72 bytes — a longer password would give a
    false sense of strength, so reject it rather than accept it quietly."""
    assert any("bytes" in p for p in security.password_problems("a-good-passphrase " * 10))


def test_strength_scores_rise_with_length():
    weak = security.password_strength("short")[0]
    ok = security.password_strength("correct-horse-battery")[0]
    great = security.password_strength("correct horse battery staple mango")[0]
    assert weak < ok <= great
    assert great == 4


def test_unicode_normalization_is_stable():
    # Same string, different Unicode encodings, must hash the same.
    assert security.normalize("café") == security.normalize("café")


def test_recovery_codes_are_distinct_and_well_formed():
    codes = security.generate_recovery_codes()
    assert len(codes) == security.RECOVERY_CODE_COUNT
    assert len(set(codes)) == len(codes)
    assert all(len(c) == 11 and c[5] == "-" for c in codes)
    # Ambiguous glyphs are excluded so codes survive being written down.
    assert not set("".join(codes)) & set("IO01")


def test_recovery_codes_are_case_and_format_insensitive():
    assert security.canonical_code("abcde-12345") == security.canonical_code("ABCDE12345")
    assert security.canonical_code(" ab cde-123 45 ") == "ABCDE12345"


# ------------------------------------------------------------------- accounts

def test_create_and_verify_round_trip(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    assert db.verify_user("a@b.com", GOOD_PW) == uid


def test_email_is_normalized(db):
    uid = db.create_user("A@B.com", GOOD_PW)
    assert db.verify_user("  a@b.COM ", GOOD_PW) == uid


def test_duplicate_email_is_refused(db):
    db.create_user("a@b.com", GOOD_PW)
    with pytest.raises(db.AuthError, match="already exists"):
        db.create_user("a@b.com", OTHER_PW)


def test_weak_password_is_refused_at_signup(db):
    with pytest.raises(db.AuthError):
        db.create_user("a@b.com", "password123")


def test_bad_email_is_refused(db):
    with pytest.raises(db.AuthError, match="email"):
        db.create_user("not-an-email", GOOD_PW)


def test_wrong_password_is_refused(db):
    db.create_user("a@b.com", GOOD_PW)
    with pytest.raises(db.AuthError, match="Incorrect"):
        db.verify_user("a@b.com", OTHER_PW)


def test_unknown_and_wrong_password_give_identical_messages(db):
    """User enumeration: the two failures must be indistinguishable."""
    db.create_user("a@b.com", GOOD_PW)
    with pytest.raises(db.AuthError) as wrong:
        db.verify_user("a@b.com", OTHER_PW)
    with pytest.raises(db.AuthError) as missing:
        db.verify_user("nobody@b.com", OTHER_PW)
    assert str(wrong.value) == str(missing.value)


def test_unknown_account_still_costs_bcrypt_time(db):
    """The timing decoy: a missing email must not return faster than a real one."""
    db.create_user("a@b.com", GOOD_PW)
    db.verify_user("a@b.com", GOOD_PW)  # warm the decoy hash

    t0 = time.perf_counter()
    with pytest.raises(db.AuthError):
        db.verify_user("a@b.com", OTHER_PW)
    real = time.perf_counter() - t0

    t0 = time.perf_counter()
    with pytest.raises(db.AuthError):
        db.verify_user("ghost@b.com", OTHER_PW)
    missing = time.perf_counter() - t0

    # Generous bound — we only need to kill the microseconds-vs-milliseconds tell.
    assert missing > real * 0.5, f"missing={missing:.4f}s vs real={real:.4f}s"


# ------------------------------------------------------------------ throttling

def test_account_locks_after_repeated_failures(db):
    db.create_user("a@b.com", GOOD_PW)
    for _ in range(db.MAX_FAILED_ATTEMPTS):
        with pytest.raises(db.AuthError):
            db.verify_user("a@b.com", OTHER_PW)
    with pytest.raises(db.RateLimited):
        db.verify_user("a@b.com", OTHER_PW)


def test_lockout_blocks_even_the_correct_password(db):
    """Otherwise an attacker who eventually guesses right walks straight in."""
    db.create_user("a@b.com", GOOD_PW)
    for _ in range(db.MAX_FAILED_ATTEMPTS):
        with pytest.raises(db.AuthError):
            db.verify_user("a@b.com", OTHER_PW)
    with pytest.raises(db.RateLimited):
        db.verify_user("a@b.com", GOOD_PW)


def test_unregistered_emails_are_throttled_too(db):
    """Throttling only real accounts would itself leak which emails exist."""
    for _ in range(db.MAX_FAILED_ATTEMPTS):
        with pytest.raises(db.AuthError):
            db.verify_user("ghost@b.com", OTHER_PW)
    with pytest.raises(db.RateLimited):
        db.verify_user("ghost@b.com", OTHER_PW)


def test_a_successful_sign_in_clears_the_strikes(db):
    db.create_user("a@b.com", GOOD_PW)
    for _ in range(db.MAX_FAILED_ATTEMPTS - 1):
        with pytest.raises(db.AuthError):
            db.verify_user("a@b.com", OTHER_PW)
    assert db.attempts_remaining("a@b.com") == 1
    db.verify_user("a@b.com", GOOD_PW)
    assert db.attempts_remaining("a@b.com") == db.MAX_FAILED_ATTEMPTS


def test_lockout_is_scoped_to_one_email(db):
    db.create_user("a@b.com", GOOD_PW)
    db.create_user("c@d.com", OTHER_PW)
    for _ in range(db.MAX_FAILED_ATTEMPTS + 1):
        with pytest.raises(db.AuthError):
            db.verify_user("a@b.com", "wrong-password-here")
    assert db.verify_user("c@d.com", OTHER_PW)  # unaffected


def test_rate_limit_error_reports_remaining_time(db):
    for _ in range(db.MAX_FAILED_ATTEMPTS):
        with pytest.raises(db.AuthError):
            db.verify_user("ghost@b.com", OTHER_PW)
    with pytest.raises(db.RateLimited) as e:
        db.verify_user("ghost@b.com", OTHER_PW)
    assert 0 < e.value.seconds <= db.LOCKOUT_DURATION.total_seconds()
    assert "minute" in str(e.value)


# -------------------------------------------------------------------- sessions

def test_session_round_trip(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    token = db.start_session(uid)
    assert db.resolve_session(token) == uid


def test_session_tokens_are_stored_hashed(db):
    """A stolen database read must not yield replayable sessions."""
    uid = db.create_user("a@b.com", GOOD_PW)
    token = db.start_session(uid)
    with db._conn() as con:
        stored = [r["token"] for r in con.execute("SELECT token FROM sessions")]
    assert token not in stored
    assert len(stored) == 1 and len(stored[0]) == 64  # sha256 hex


def test_signing_out_revokes_the_token(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    token = db.start_session(uid)
    db.end_session(token)
    assert db.resolve_session(token) is None


def test_garbage_and_empty_tokens_are_rejected(db):
    assert db.resolve_session("") is None
    assert db.resolve_session("not-a-real-token") is None
    assert db.resolve_session(None) is None


def test_expired_session_is_rejected_and_deleted(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    token = db.start_session(uid)
    with db._conn() as con:
        con.execute("UPDATE sessions SET expires_at = ?", ("2000-01-01T00:00:00+00:00",))
    assert db.resolve_session(token) is None
    assert db.active_session_count(uid) == 0


def test_idle_session_expires_even_inside_its_absolute_life(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    token = db.start_session(uid)
    with db._conn() as con:  # last used long ago, but not yet past expires_at
        con.execute("UPDATE sessions SET last_seen_at = ?", ("2000-01-01T00:00:00+00:00",))
    assert db.resolve_session(token) is None


def test_using_a_session_refreshes_its_idle_clock(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    token = db.start_session(uid)
    with db._conn() as con:
        before = con.execute("SELECT last_seen_at FROM sessions").fetchone()["last_seen_at"]
    time.sleep(0.01)
    db.resolve_session(token)
    with db._conn() as con:
        after = con.execute("SELECT last_seen_at FROM sessions").fetchone()["last_seen_at"]
    assert after > before


def test_short_sessions_expire_sooner_than_remembered_ones(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    short, long = db.start_session(uid, remember=False), db.start_session(uid, remember=True)
    with db._conn() as con:
        rows = {r["token"]: r["expires_at"] for r in
                con.execute("SELECT token, expires_at FROM sessions")}
    assert rows[db._hash_token(short)] < rows[db._hash_token(long)]


def test_changing_the_password_kills_every_session(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    t1, t2 = db.start_session(uid), db.start_session(uid)
    db.change_password(uid, GOOD_PW, OTHER_PW)
    assert db.resolve_session(t1) is None and db.resolve_session(t2) is None
    assert db.verify_user("a@b.com", OTHER_PW) == uid


def test_change_password_requires_the_old_one(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    with pytest.raises(db.AuthError, match="Current password"):
        db.change_password(uid, "not-the-old-password", OTHER_PW)


def test_change_password_enforces_the_policy(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    with pytest.raises(db.AuthError):
        db.change_password(uid, GOOD_PW, "password123")


def test_sign_out_everywhere(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    tokens = [db.start_session(uid) for _ in range(3)]
    assert db.active_session_count(uid) == 3
    db.end_all_sessions(uid)
    assert all(db.resolve_session(t) is None for t in tokens)


def test_sessions_die_with_the_account(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    token = db.start_session(uid)
    db.delete_user(uid)
    assert db.resolve_session(token) is None


# ------------------------------------------------------------------- recovery

def test_recovery_code_resets_the_password(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    codes = db.issue_recovery_codes(uid)
    assert db.reset_password_with_code("a@b.com", codes[0], OTHER_PW) == uid
    assert db.verify_user("a@b.com", OTHER_PW) == uid


def test_a_recovery_code_works_only_once(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    codes = db.issue_recovery_codes(uid)
    db.reset_password_with_code("a@b.com", codes[0], OTHER_PW)
    with pytest.raises(db.AuthError, match="isn't valid"):
        db.reset_password_with_code("a@b.com", codes[0], "third-password-here")


def test_recovery_accepts_sloppy_formatting(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    code = db.issue_recovery_codes(uid)[0]
    messy = code.lower().replace("-", " ")
    assert db.reset_password_with_code("a@b.com", messy, OTHER_PW) == uid


def test_bad_recovery_code_is_refused(db):
    db.create_user("a@b.com", GOOD_PW)
    db.issue_recovery_codes(1)
    with pytest.raises(db.AuthError, match="isn't valid"):
        db.reset_password_with_code("a@b.com", "AAAAA-BBBBB", OTHER_PW)


def test_recovery_codes_do_not_work_across_accounts(db):
    db.create_user("a@b.com", GOOD_PW)
    other = db.create_user("c@d.com", OTHER_PW)
    stolen = db.issue_recovery_codes(other)[0]
    with pytest.raises(db.AuthError):
        db.reset_password_with_code("a@b.com", stolen, "brand-new-passphrase")


def test_recovery_reset_enforces_the_password_policy(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    code = db.issue_recovery_codes(uid)[0]
    with pytest.raises(db.AuthError):
        db.reset_password_with_code("a@b.com", code, "password123")


def test_failed_recovery_attempts_are_throttled(db):
    db.create_user("a@b.com", GOOD_PW)
    for _ in range(db.MAX_FAILED_ATTEMPTS):
        with pytest.raises(db.AuthError):
            db.reset_password_with_code("a@b.com", "AAAAA-BBBBB", OTHER_PW)
    with pytest.raises(db.RateLimited):
        db.reset_password_with_code("a@b.com", "AAAAA-BBBBB", OTHER_PW)


def test_recovery_reset_signs_out_existing_sessions(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    token = db.start_session(uid)
    code = db.issue_recovery_codes(uid)[0]
    db.reset_password_with_code("a@b.com", code, OTHER_PW)
    assert db.resolve_session(token) is None


def test_issuing_new_codes_invalidates_the_old_set(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    old = db.issue_recovery_codes(uid)
    db.issue_recovery_codes(uid)
    with pytest.raises(db.AuthError):
        db.reset_password_with_code("a@b.com", old[0], OTHER_PW)


def test_unused_code_count_tracks_consumption(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    codes = db.issue_recovery_codes(uid)
    assert db.unused_recovery_code_count(uid) == len(codes)
    db.reset_password_with_code("a@b.com", codes[0], OTHER_PW)
    assert db.unused_recovery_code_count(uid) == len(codes) - 1


def test_recovery_codes_are_stored_hashed(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    codes = db.issue_recovery_codes(uid)
    with db._conn() as con:
        stored = [r["code_hash"] for r in con.execute("SELECT code_hash FROM recovery_codes")]
    assert not set(codes) & set(stored)


# --------------------------------------------------------------------- audit

def test_login_history_records_success_and_failure(db):
    uid = db.create_user("a@b.com", GOOD_PW)
    with pytest.raises(db.AuthError):
        db.verify_user("a@b.com", OTHER_PW)
    db.verify_user("a@b.com", GOOD_PW)
    events = db.recent_logins(uid)
    assert events and events[0]["ok"] == 1


def test_migration_adds_new_columns_to_an_existing_database(db):
    """An older database on disk must survive an upgrade."""
    with db._conn() as con:
        con.execute("ALTER TABLE sessions DROP COLUMN last_seen_at")
    db.init_db()  # re-run migrations
    uid = db.create_user("a@b.com", GOOD_PW)
    assert db.resolve_session(db.start_session(uid)) == uid
