"""Persistence and authentication — SQLite locally, Postgres in production.

``DEBTMANAGER_DB`` picks the backend by URL scheme: a ``postgresql://`` URL uses
Postgres, anything else is a SQLite file path. Unset means ``data/debt.db``, so
tests and ``streamlit run`` work with no configuration.

The split exists because hosted Streamlit has an ephemeral filesystem — a SQLite
file there is wiped whenever the container sleeps or redeploys, silently losing
every account. Point ``DEBTMANAGER_DB`` at a hosted Postgres in production; this
is the only module that touches storage.

Timestamps are stored as ISO-8601 UTC *text* on both backends. They are only
compared to each other, and a fixed-offset ISO string sorts correctly
lexicographically, so this sidesteps the driver-specific datetime conversions
that make SQLite-to-Postgres ports go wrong.

Passwords are bcrypt-hashed. Sessions are opaque random tokens stored in the
database and echoed into the browser URL, so a refresh (or coming back
tomorrow) keeps you logged in without ever putting a credential in the URL.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterator, Optional

import bcrypt

from . import _pools, security
from .models import Debt, Payment, Profile


def _configured_dsn() -> str:
    """Env var wins; fall back to Streamlit secrets so the Cloud UI works too."""
    dsn = os.environ.get("DEBTMANAGER_DB", "")
    if dsn:
        return dsn
    try:  # outside a Streamlit runtime this raises — that is fine, it means SQLite
        import streamlit as st

        return str(st.secrets.get("DEBTMANAGER_DB", "") or "")
    except Exception:
        return ""


_DSN = _configured_dsn()
IS_POSTGRES = _DSN.startswith(("postgresql://", "postgres://"))
DB_PATH = Path(_DSN or Path(__file__).parent.parent / "data" / "debt.db")

SESSION_DAYS = 30       # absolute lifetime, even if you use it every day
SESSION_IDLE_DAYS = 7   # go quiet this long and the session dies
SHORT_SESSION_HOURS = 12  # when "keep me signed in" is unchecked

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_WINDOW = timedelta(minutes=15)  # failures are counted within this window
LOCKOUT_DURATION = timedelta(minutes=15)

_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Raised for any credential problem — message is safe to show the user."""


class RateLimited(AuthError):
    """Too many failed attempts. Carries the seconds remaining."""

    def __init__(self, seconds: int):
        self.seconds = seconds
        mins = max(1, round(seconds / 60))
        super().__init__(
            f"Too many failed sign-in attempts. Try again in {mins} minute"
            f"{'s' if mins != 1 else ''}."
        )


class _Cx:
    """Uniform ``execute``/``executemany`` over sqlite3 and psycopg.

    Queries are written once with ``?`` placeholders and rewritten for Postgres.
    No query in this module contains a literal ``?`` outside a placeholder, so
    the substitution is safe.
    """

    def __init__(self, raw: Any, postgres: bool) -> None:
        self._raw = raw
        self._pg = postgres

    def _q(self, sql: str) -> str:
        return sql.replace("?", "%s") if self._pg else sql

    def execute(self, sql: str, params=()) -> Any:
        if self._pg:
            cur = self._raw.cursor()
            cur.execute(self._q(sql), params)
            return cur
        return self._raw.execute(sql, params)

    def executemany(self, sql: str, seq) -> Any:
        if self._pg:
            cur = self._raw.cursor()
            cur.executemany(self._q(sql), list(seq))
            return cur
        return self._raw.executemany(sql, seq)


@contextmanager
def _conn() -> Iterator[_Cx]:
    # NOTE: raising inside a `with _conn()` block discards every write made in
    # it, on both backends. Anything that must survive an error — a failed-login
    # record, say — has to be committed before the raise, not after.
    if IS_POSTGRES:
        with _pools.get(_DSN).connection() as raw:  # commits on exit, rolls back on error
            yield _Cx(raw, True)
        return
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")  # Postgres enforces these by default
    try:
        yield _Cx(con, False)
        con.commit()
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _integrity_errors() -> tuple:
    if not IS_POSTGRES:
        return (sqlite3.IntegrityError,)
    import psycopg

    return (sqlite3.IntegrityError, psycopg.errors.IntegrityError)


def _insert_id(con: _Cx, sql: str, params: tuple) -> int:
    """INSERT and return the new row's id. Postgres has no ``lastrowid``."""
    if IS_POSTGRES:
        return int(con.execute(sql + " RETURNING id", params).fetchone()["id"])
    return int(con.execute(sql, params).lastrowid)


# Only the id columns, float type and email collation differ between backends.
# Emails are stored lowercased by _normalize_email, so Postgres needs no
# case-insensitive collation to match SQLite's NOCASE.
_SCHEMA = """
            CREATE TABLE IF NOT EXISTS users (
                id            {pk},
                email         TEXT NOT NULL UNIQUE{nocase},
                password_hash TEXT NOT NULL,
                created_at    TEXT NOT NULL,
                last_login    TEXT
            );

            CREATE TABLE IF NOT EXISTS sessions (
                token      TEXT PRIMARY KEY,
                user_id    INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS profiles (
                user_id        INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                monthly_budget {real} NOT NULL DEFAULT 0,
                monthly_income {real} NOT NULL DEFAULT 0,
                emergency_fund {real} NOT NULL DEFAULT 0,
                strategy       TEXT NOT NULL DEFAULT 'avalanche',
                custom_order   TEXT NOT NULL DEFAULT '',
                updated_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS debts (
                id              {pk},
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                kind            TEXT NOT NULL,
                balance         {real} NOT NULL DEFAULT 0,
                apr             {real} NOT NULL DEFAULT 0,
                min_payment     {real} NOT NULL DEFAULT 0,
                min_percent     {real} NOT NULL DEFAULT 2,
                credit_limit    {real} NOT NULL DEFAULT 0,
                term_months     INTEGER NOT NULL DEFAULT 0,
                subtype         TEXT NOT NULL DEFAULT 'Other',
                current_payment {real} NOT NULL DEFAULT 0,
                due_day         INTEGER NOT NULL DEFAULT 0,
                position        INTEGER NOT NULL DEFAULT 0
            );

            -- Every save appends a row, so the user can see their trajectory.
            CREATE TABLE IF NOT EXISTS snapshots (
                id            {pk},
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                taken_at      TEXT NOT NULL,
                total_balance {real} NOT NULL,
                total_minimum {real} NOT NULL,
                blended_apr   {real} NOT NULL,
                debt_count    INTEGER NOT NULL
            );

            -- Failed and successful sign-ins, keyed by email rather than user_id
            -- so attempts against addresses that don't exist are throttled too.
            -- Throttling only unregistered emails would itself leak which
            -- addresses are registered.
            CREATE TABLE IF NOT EXISTS login_events (
                id      {pk},
                email   TEXT NOT NULL,
                at      TEXT NOT NULL,
                ok      INTEGER NOT NULL,
                note    TEXT
            );

            -- Payments the user actually made. History, not projection: these
            -- rows are never recomputed, which is what lets the ledger state a
            -- lifetime interest figure that doesn't move when an APR is edited.
            -- debt_id goes NULL rather than cascading when an account is
            -- deleted, so closing a card doesn't erase what it cost you.
            CREATE TABLE IF NOT EXISTS payments (
                id            {pk},
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                debt_id       INTEGER REFERENCES debts(id) ON DELETE SET NULL,
                debt_name     TEXT NOT NULL,
                paid_on       TEXT NOT NULL,
                amount        {real} NOT NULL,
                interest      {real} NOT NULL DEFAULT 0,
                principal     {real} NOT NULL DEFAULT 0,
                balance_after {real},
                note          TEXT NOT NULL DEFAULT '',
                created_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS recovery_codes (
                id        {pk},
                user_id   INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                code_hash TEXT NOT NULL,
                used_at   TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_debts_user ON debts(user_id, position);
            CREATE INDEX IF NOT EXISTS idx_payments_user ON payments(user_id, paid_on);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_user ON snapshots(user_id, taken_at);
            CREATE INDEX IF NOT EXISTS idx_login_events ON login_events(email, at);
            CREATE INDEX IF NOT EXISTS idx_recovery_user ON recovery_codes(user_id);
"""


def init_db() -> None:
    """Create the schema and run migrations. Idempotent, and deliberately not
    memoized: it must re-run migrations against a database that already exists,
    which is the whole point of ``_migrate``."""
    if IS_POSTGRES:
        ddl = _SCHEMA.format(pk="INTEGER GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY",
                             nocase="", real="DOUBLE PRECISION")
        with _conn() as con:
            for stmt in (x.strip() for x in ddl.split(";")):
                if stmt:
                    con.execute(stmt)
            _migrate(con)
    else:
        ddl = _SCHEMA.format(pk="INTEGER PRIMARY KEY AUTOINCREMENT",
                             nocase=" COLLATE NOCASE", real="REAL")
        with _conn() as con:
            con._raw.executescript(ddl)
            _migrate(con)


def _migrate(con: _Cx) -> None:
    """Add columns that came after the first release. CREATE TABLE IF NOT EXISTS
    won't touch a table that already exists, so new columns need this."""
    def add(table: str, column: str, ddl: str) -> None:
        if IS_POSTGRES:  # no PRAGMA; the catalog is a normal queryable table
            rows = con.execute(
                "SELECT column_name AS name FROM information_schema.columns "
                "WHERE table_name = ?", (table,)).fetchall()
        else:
            rows = con.execute(f"PRAGMA table_info({table})").fetchall()
        if column not in {r["name"] for r in rows}:
            con.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")

    add("sessions", "last_seen_at", "last_seen_at TEXT")
    add("debts", "due_day", "due_day INTEGER NOT NULL DEFAULT 0")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------------ auth

_dummy_hash: Optional[bytes] = None


def _timing_decoy() -> bytes:
    """A real bcrypt hash to check against when the account doesn't exist.

    Without this, a missing email returns in microseconds while a real one takes
    the full bcrypt cost — a timing oracle that enumerates registered users.
    """
    global _dummy_hash
    if _dummy_hash is None:
        _dummy_hash = bcrypt.hashpw(b"timing-equalisation-decoy", bcrypt.gensalt())
    return _dummy_hash


def _hash_token(token: str) -> str:
    """Session and recovery tokens are 50+ bits of CSPRNG output, so a fast hash
    is the right tool — there is nothing to brute-force. Storing the hash means
    a leaked database still can't be replayed as live sessions."""
    return hashlib.sha256(token.encode()).hexdigest()


def _normalize_email(email: str) -> str:
    return (email or "").strip().lower()


def _validate(email: str, password: str) -> str:
    email = _normalize_email(email)
    if not _EMAIL_RE.match(email):
        raise AuthError("That doesn't look like a valid email address.")
    problems = security.password_problems(password, email)
    if problems:
        raise AuthError(problems[0])
    return email


# --------------------------------------------------------------- rate limiting

def _seconds_locked_out(con: _Cx, email: str) -> int:
    """How long this email must wait, based on recent failures. 0 = go ahead."""
    since = (datetime.now(timezone.utc) - LOCKOUT_WINDOW).isoformat()
    row = con.execute(
        "SELECT COUNT(*) AS n, MAX(at) AS last FROM login_events "
        "WHERE email = ? AND ok = 0 AND at >= ?",
        (email, since),
    ).fetchone()
    if not row or row["n"] < MAX_FAILED_ATTEMPTS:
        return 0
    unlock = datetime.fromisoformat(row["last"]) + LOCKOUT_DURATION
    remaining = (unlock - datetime.now(timezone.utc)).total_seconds()
    return max(0, int(remaining))


def _record_attempt(con: _Cx, email: str, ok: bool, note: str = "") -> None:
    con.execute("INSERT INTO login_events (email, at, ok, note) VALUES (?, ?, ?, ?)",
                (email, _now(), 1 if ok else 0, note))
    if ok:
        # A correct password clears the strikes so a legitimate user isn't
        # locked out by their own earlier typos.
        con.execute("DELETE FROM login_events WHERE email = ? AND ok = 0", (email,))
    # Keep the audit table from growing forever.
    con.execute("DELETE FROM login_events WHERE at < ?",
                ((datetime.now(timezone.utc) - timedelta(days=90)).isoformat(),))


def attempts_remaining(email: str) -> int:
    """For the UI warning. Not authoritative — the check at sign-in time is."""
    with _conn() as con:
        since = (datetime.now(timezone.utc) - LOCKOUT_WINDOW).isoformat()
        row = con.execute(
            "SELECT COUNT(*) AS n FROM login_events WHERE email = ? AND ok = 0 AND at >= ?",
            (_normalize_email(email), since),
        ).fetchone()
    return max(0, MAX_FAILED_ATTEMPTS - (row["n"] if row else 0))


# ---------------------------------------------------------------------- users

def create_user(email: str, password: str) -> int:
    email = _validate(email, password)
    pw = bcrypt.hashpw(security.normalize(password).encode(), bcrypt.gensalt())
    with _conn() as con:
        try:
            uid = _insert_id(
                con,
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, pw.decode(), _now()),
            )
        except _integrity_errors():
            raise AuthError("An account with that email already exists. Try signing in.")
        con.execute("INSERT INTO profiles (user_id, updated_at) VALUES (?, ?)", (uid, _now()))
    return uid


def verify_user(email: str, password: str) -> int:
    email = _normalize_email(email)
    with _conn() as con:
        locked = _seconds_locked_out(con, email)
        if locked:
            raise RateLimited(locked)
        row = con.execute("SELECT id, password_hash FROM users WHERE email = ?",
                          (email,)).fetchone()

    # Always spend the same bcrypt time, whether or not the account exists.
    supplied = security.normalize(password).encode()
    if row:
        ok = bcrypt.checkpw(supplied, row["password_hash"].encode())
    else:
        bcrypt.checkpw(supplied, _timing_decoy())
        ok = False

    # Commit the attempt first: raising inside the block would roll it back and
    # silently disable throttling altogether.
    with _conn() as con:
        _record_attempt(con, email, ok)
        if ok:
            con.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), row["id"]))

    if not ok:
        # One message for both cases — never reveal which emails are registered.
        raise AuthError("Incorrect email or password.")
    return int(row["id"])


def change_password(user_id: int, old: str, new: str) -> None:
    with _conn() as con:
        row = con.execute("SELECT email, password_hash FROM users WHERE id = ?",
                          (user_id,)).fetchone()
    if not row or not bcrypt.checkpw(security.normalize(old).encode(),
                                     row["password_hash"].encode()):
        raise AuthError("Current password is incorrect.")
    _set_password(user_id, row["email"], new)


def _set_password(user_id: int, email: str, new: str) -> None:
    problems = security.password_problems(new, email)
    if problems:
        raise AuthError(problems[0])
    pw = bcrypt.hashpw(security.normalize(new).encode(), bcrypt.gensalt()).decode()
    with _conn() as con:
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw, user_id))
        # Changing the password kills every session, including the current one.
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


# ------------------------------------------------------------- recovery codes

def issue_recovery_codes(user_id: int) -> list[str]:
    """Replace this user's codes and return the new plaintext — the only time
    it is ever available. There is no email service here, so these codes are
    the entire account-recovery story."""
    codes = security.generate_recovery_codes()
    with _conn() as con:
        con.execute("DELETE FROM recovery_codes WHERE user_id = ?", (user_id,))
        con.executemany(
            "INSERT INTO recovery_codes (user_id, code_hash) VALUES (?, ?)",
            [(user_id, _hash_token(security.canonical_code(c))) for c in codes],
        )
    return codes


def unused_recovery_code_count(user_id: int) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM recovery_codes WHERE user_id = ? AND used_at IS NULL",
            (user_id,),
        ).fetchone()
    return int(row["n"]) if row else 0


def reset_password_with_code(email: str, code: str, new_password: str) -> int:
    """Consume a recovery code to set a new password. Rate-limited like sign-in."""
    email = _normalize_email(email)
    canon = security.canonical_code(code)
    with _conn() as con:
        locked = _seconds_locked_out(con, email)
        if locked:
            raise RateLimited(locked)
        row = con.execute("SELECT id FROM users WHERE email = ?", (email,)).fetchone()
        match = None
        if row:
            match = con.execute(
                "SELECT id FROM recovery_codes "
                "WHERE user_id = ? AND code_hash = ? AND used_at IS NULL",
                (row["id"], _hash_token(canon)),
            ).fetchone()
    # Same ordering rule as verify_user: persist the attempt, then raise.
    if not row or not match:
        with _conn() as con:
            _record_attempt(con, email, False, "recovery")
        raise AuthError("That recovery code isn't valid.")

    uid = int(row["id"])
    _set_password(uid, email, new_password)  # validates, rehashes, drops sessions
    with _conn() as con:
        con.execute("UPDATE recovery_codes SET used_at = ? WHERE id = ?", (_now(), match["id"]))
        _record_attempt(con, email, True, "recovery")
    return uid


def recent_logins(user_id: int, limit: int = 10) -> list[dict]:
    """Sign-in history for the account page, so a user can spot an intrusion."""
    email = get_email(user_id)
    if not email:
        return []
    with _conn() as con:
        rows = con.execute(
            "SELECT at, ok, note FROM login_events WHERE email = ? ORDER BY at DESC LIMIT ?",
            (email, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_email(user_id: int) -> Optional[str]:
    with _conn() as con:
        row = con.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["email"] if row else None


def delete_user(user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


# -------------------------------------------------------------------- sessions

def start_session(user_id: int, remember: bool = True) -> str:
    """Mint a session. Only the hash is stored; the caller holds the secret."""
    token = secrets.token_urlsafe(32)
    life = timedelta(days=SESSION_DAYS) if remember else timedelta(hours=SHORT_SESSION_HOURS)
    expires = (datetime.now(timezone.utc) + life).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at, last_seen_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (_hash_token(token), user_id, _now(), expires, _now()),
        )
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
    return token


def resolve_session(token: str) -> Optional[int]:
    """Return the user for a session token, enforcing both expiry rules."""
    if not token:
        return None
    digest = _hash_token(token)
    with _conn() as con:
        row = con.execute(
            "SELECT user_id, expires_at, last_seen_at FROM sessions WHERE token = ?", (digest,)
        ).fetchone()
        if not row:
            return None

        now = datetime.now(timezone.utc)
        if row["expires_at"] < now.isoformat():
            con.execute("DELETE FROM sessions WHERE token = ?", (digest,))
            return None

        # Idle timeout: a token that sat unused in someone's browser history for
        # a week is not a live session, even inside its absolute lifetime.
        last_seen = row["last_seen_at"]
        if last_seen and datetime.fromisoformat(last_seen) + timedelta(days=SESSION_IDLE_DAYS) < now:
            con.execute("DELETE FROM sessions WHERE token = ?", (digest,))
            return None

        con.execute("UPDATE sessions SET last_seen_at = ? WHERE token = ?", (_now(), digest))
    return int(row["user_id"])


def end_session(token: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE token = ?", (_hash_token(token),))


def end_all_sessions(user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))


def active_session_count(user_id: int) -> int:
    with _conn() as con:
        row = con.execute(
            "SELECT COUNT(*) AS n FROM sessions WHERE user_id = ? AND expires_at >= ?",
            (user_id, _now()),
        ).fetchone()
    return int(row["n"]) if row else 0


# ----------------------------------------------------------------------- debts

_DEBT_COLS = ("name", "kind", "balance", "apr", "min_payment", "min_percent",
              "credit_limit", "term_months", "subtype", "current_payment", "due_day")


def load_debts(user_id: int) -> list[Debt]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM debts WHERE user_id = ? ORDER BY position, id", (user_id,)
        ).fetchall()
    return [Debt(id=r["id"], **{c: r[c] for c in _DEBT_COLS}) for r in rows]


def save_debts(user_id: int, debts: list[Debt]) -> None:
    """Persist the user's debt set, keeping each row's id stable.

    The page autosaves on every keystroke, so this runs constantly. It updates
    in place rather than delete-and-reinsert because ``payments.debt_id`` points
    here: churning ids would detach a user's payment history from their accounts
    every time they touched a number.

    Rows arriving without an id are matched to an existing row by (kind, name)
    before being treated as new, which keeps history attached even when the
    caller can't round-trip the id — and updates ``payments.debt_name`` on a
    rename so the ledger follows the account.
    """
    assign = f"{', '.join(f'{c} = ?' for c in _DEBT_COLS)}, position = ?"
    with _conn() as con:
        existing = {r["id"]: (r["kind"], r["name"])
                    for r in con.execute("SELECT id, kind, name FROM debts WHERE user_id = ?",
                                         (user_id,))}
        claimed: set[int] = set()
        for i, d in enumerate(debts):
            row_id = d.id if d.id in existing and d.id not in claimed else None
            if row_id is None:
                row_id = next((eid for eid, key in existing.items()
                               if eid not in claimed and key == (d.kind, d.name)), None)
            values = [getattr(d, c) for c in _DEBT_COLS]
            if row_id is None:
                d.id = _insert_id(
                    con,
                    f"INSERT INTO debts (user_id, {', '.join(_DEBT_COLS)}, position) "
                    f"VALUES (?, {', '.join('?' * len(_DEBT_COLS))}, ?)",
                    (user_id, *values, i),
                )
            else:
                con.execute(f"UPDATE debts SET {assign} WHERE id = ? AND user_id = ?",
                            (*values, i, row_id, user_id))
                if existing[row_id][1] != d.name:
                    con.execute("UPDATE payments SET debt_name = ? WHERE debt_id = ?",
                                (d.name, row_id))
                claimed.add(row_id)
                d.id = row_id

        gone = [eid for eid in existing if eid not in claimed]
        if gone:
            con.executemany("DELETE FROM debts WHERE id = ? AND user_id = ?",
                            [(eid, user_id) for eid in gone])


# ------------------------------------------------------------------- payments

def record_payment(user_id: int, payment: Payment) -> int:
    """Append one payment to the ledger and return its id."""
    with _conn() as con:
        payment.id = _insert_id(
            con,
            "INSERT INTO payments (user_id, debt_id, debt_name, paid_on, amount, interest, "
            "principal, balance_after, note, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (user_id, payment.debt_id, payment.debt_name, payment.paid_on.isoformat(),
             payment.amount, payment.interest, payment.principal, payment.balance_after,
             payment.note or "", _now()),
        )
    return payment.id


def load_payments(user_id: int, debt_name: Optional[str] = None) -> list[Payment]:
    """The ledger, oldest first. Filtered to one account when asked."""
    sql = "SELECT * FROM payments WHERE user_id = ?"
    args: list = [user_id]
    if debt_name is not None:
        sql += " AND debt_name = ?"
        args.append(debt_name)
    with _conn() as con:
        rows = con.execute(sql + " ORDER BY paid_on, id", args).fetchall()
    return [Payment(id=r["id"], debt_id=r["debt_id"], debt_name=r["debt_name"],
                    paid_on=date.fromisoformat(r["paid_on"]), amount=r["amount"],
                    interest=r["interest"], principal=r["principal"],
                    balance_after=r["balance_after"], note=r["note"] or "")
            for r in rows]


def delete_payment(user_id: int, payment_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM payments WHERE id = ? AND user_id = ?", (payment_id, user_id))


# --------------------------------------------------------------------- profile

def load_profile(user_id: int) -> Profile:
    with _conn() as con:
        row = con.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    if not row:
        return Profile()
    return Profile(
        monthly_budget=row["monthly_budget"],
        monthly_income=row["monthly_income"],
        emergency_fund=row["emergency_fund"],
        strategy=row["strategy"],
        custom_order=[s for s in (row["custom_order"] or "").split("\x1f") if s],
    )


def save_profile(user_id: int, p: Profile) -> None:
    with _conn() as con:
        con.execute(
            """INSERT INTO profiles (user_id, monthly_budget, monthly_income, emergency_fund,
                                     strategy, custom_order, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                   monthly_budget=excluded.monthly_budget,
                   monthly_income=excluded.monthly_income,
                   emergency_fund=excluded.emergency_fund,
                   strategy=excluded.strategy,
                   custom_order=excluded.custom_order,
                   updated_at=excluded.updated_at""",
            (user_id, p.monthly_budget, p.monthly_income, p.emergency_fund, p.strategy,
             "\x1f".join(p.custom_order or []), _now()),
        )


# ------------------------------------------------------------------- snapshots

def record_snapshot(user_id: int, debts: list[Debt]) -> None:
    """Append a point to the progress history, but only once a day."""
    total = sum(d.balance for d in debts)
    if not debts:
        return
    blended = sum(d.balance * d.apr for d in debts) / total if total else 0.0
    minimum = sum(d.required_payment(d.balance + d.monthly_interest) for d in debts)
    today = datetime.now(timezone.utc).date().isoformat()
    with _conn() as con:
        existing = con.execute(
            "SELECT id FROM snapshots WHERE user_id = ? AND taken_at LIKE ?",
            (user_id, f"{today}%"),
        ).fetchone()
        if existing:
            con.execute(
                "UPDATE snapshots SET total_balance=?, total_minimum=?, blended_apr=?, "
                "debt_count=?, taken_at=? WHERE id = ?",
                (total, minimum, blended, len(debts), _now(), existing["id"]),
            )
        else:
            con.execute(
                "INSERT INTO snapshots (user_id, taken_at, total_balance, total_minimum, "
                "blended_apr, debt_count) VALUES (?, ?, ?, ?, ?, ?)",
                (user_id, _now(), total, minimum, blended, len(debts)),
            )


def load_snapshots(user_id: int) -> list[dict]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM snapshots WHERE user_id = ? ORDER BY taken_at", (user_id,)
        ).fetchall()
    return [dict(r) for r in rows]
