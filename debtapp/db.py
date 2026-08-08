"""SQLite persistence and authentication.

One file, no server. ``DEBTMANAGER_DB`` overrides the location; on a hosted
Streamlit instance point it at a mounted volume so sessions survive restarts.

Passwords are bcrypt-hashed. Sessions are opaque random tokens stored in the
database and echoed into the browser URL, so a refresh (or coming back
tomorrow) keeps you logged in without ever putting a credential in the URL.
"""

from __future__ import annotations

import os
import re
import secrets
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Optional

import bcrypt

from .models import Debt, Profile

DB_PATH = Path(os.environ.get("DEBTMANAGER_DB", Path(__file__).parent.parent / "data" / "debt.db"))
SESSION_DAYS = 30
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthError(Exception):
    """Raised for any credential problem — message is safe to show the user."""


@contextmanager
def _conn() -> Iterator[sqlite3.Connection]:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    try:
        yield con
        con.commit()
    finally:
        con.close()


def init_db() -> None:
    with _conn() as con:
        con.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT NOT NULL UNIQUE COLLATE NOCASE,
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
                monthly_budget REAL NOT NULL DEFAULT 0,
                monthly_income REAL NOT NULL DEFAULT 0,
                emergency_fund REAL NOT NULL DEFAULT 0,
                strategy       TEXT NOT NULL DEFAULT 'avalanche',
                custom_order   TEXT NOT NULL DEFAULT '',
                updated_at     TEXT
            );

            CREATE TABLE IF NOT EXISTS debts (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id         INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                name            TEXT NOT NULL,
                kind            TEXT NOT NULL,
                balance         REAL NOT NULL DEFAULT 0,
                apr             REAL NOT NULL DEFAULT 0,
                min_payment     REAL NOT NULL DEFAULT 0,
                min_percent     REAL NOT NULL DEFAULT 2,
                credit_limit    REAL NOT NULL DEFAULT 0,
                term_months     INTEGER NOT NULL DEFAULT 0,
                subtype         TEXT NOT NULL DEFAULT 'Other',
                current_payment REAL NOT NULL DEFAULT 0,
                position        INTEGER NOT NULL DEFAULT 0
            );

            -- Every save appends a row, so the user can see their trajectory.
            CREATE TABLE IF NOT EXISTS snapshots (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id       INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                taken_at      TEXT NOT NULL,
                total_balance REAL NOT NULL,
                total_minimum REAL NOT NULL,
                blended_apr   REAL NOT NULL,
                debt_count    INTEGER NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_debts_user ON debts(user_id, position);
            CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_user ON snapshots(user_id, taken_at);
            """
        )


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ------------------------------------------------------------------------ auth

def _validate(email: str, password: str) -> str:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        raise AuthError("That doesn't look like a valid email address.")
    if len(password or "") < 8:
        raise AuthError("Password must be at least 8 characters.")
    if len(password.encode()) > 72:  # bcrypt truncates past 72 bytes
        raise AuthError("Password must be 72 bytes or fewer.")
    return email


def create_user(email: str, password: str) -> int:
    email = _validate(email, password)
    pw = bcrypt.hashpw(password.encode(), bcrypt.gensalt())
    with _conn() as con:
        try:
            cur = con.execute(
                "INSERT INTO users (email, password_hash, created_at) VALUES (?, ?, ?)",
                (email, pw.decode(), _now()),
            )
        except sqlite3.IntegrityError:
            raise AuthError("An account with that email already exists. Try signing in.")
        uid = int(cur.lastrowid)
        con.execute("INSERT INTO profiles (user_id, updated_at) VALUES (?, ?)", (uid, _now()))
    return uid


def verify_user(email: str, password: str) -> int:
    email = (email or "").strip().lower()
    with _conn() as con:
        row = con.execute("SELECT id, password_hash FROM users WHERE email = ?", (email,)).fetchone()
    # Same message either way — don't leak which emails are registered.
    if not row or not bcrypt.checkpw((password or "").encode(), row["password_hash"].encode()):
        raise AuthError("Incorrect email or password.")
    with _conn() as con:
        con.execute("UPDATE users SET last_login = ? WHERE id = ?", (_now(), row["id"]))
    return int(row["id"])


def change_password(user_id: int, old: str, new: str) -> None:
    with _conn() as con:
        row = con.execute("SELECT email, password_hash FROM users WHERE id = ?", (user_id,)).fetchone()
    if not row or not bcrypt.checkpw((old or "").encode(), row["password_hash"].encode()):
        raise AuthError("Current password is incorrect.")
    _validate(row["email"], new)
    pw = bcrypt.hashpw(new.encode(), bcrypt.gensalt()).decode()
    with _conn() as con:
        con.execute("UPDATE users SET password_hash = ? WHERE id = ?", (pw, user_id))
        con.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))  # log out everywhere


def get_email(user_id: int) -> Optional[str]:
    with _conn() as con:
        row = con.execute("SELECT email FROM users WHERE id = ?", (user_id,)).fetchone()
    return row["email"] if row else None


def delete_user(user_id: int) -> None:
    with _conn() as con:
        con.execute("DELETE FROM users WHERE id = ?", (user_id,))


# -------------------------------------------------------------------- sessions

def start_session(user_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = (datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS)).isoformat()
    with _conn() as con:
        con.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?, ?, ?, ?)",
            (token, user_id, _now(), expires),
        )
        con.execute("DELETE FROM sessions WHERE expires_at < ?", (_now(),))
    return token


def resolve_session(token: str) -> Optional[int]:
    if not token:
        return None
    with _conn() as con:
        row = con.execute(
            "SELECT user_id, expires_at FROM sessions WHERE token = ?", (token,)
        ).fetchone()
    if not row:
        return None
    if row["expires_at"] < _now():
        end_session(token)
        return None
    return int(row["user_id"])


def end_session(token: str) -> None:
    with _conn() as con:
        con.execute("DELETE FROM sessions WHERE token = ?", (token,))


# ----------------------------------------------------------------------- debts

_DEBT_COLS = ("name", "kind", "balance", "apr", "min_payment", "min_percent",
              "credit_limit", "term_months", "subtype", "current_payment")


def load_debts(user_id: int) -> list[Debt]:
    with _conn() as con:
        rows = con.execute(
            "SELECT * FROM debts WHERE user_id = ? ORDER BY position, id", (user_id,)
        ).fetchall()
    return [Debt(id=r["id"], **{c: r[c] for c in _DEBT_COLS}) for r in rows]


def save_debts(user_id: int, debts: list[Debt]) -> None:
    """Replace the user's debt set wholesale — simplest thing that stays correct."""
    with _conn() as con:
        con.execute("DELETE FROM debts WHERE user_id = ?", (user_id,))
        con.executemany(
            f"INSERT INTO debts (user_id, position, {', '.join(_DEBT_COLS)}) "
            f"VALUES (?, ?, {', '.join('?' * len(_DEBT_COLS))})",
            [(user_id, i, *(getattr(d, c) for c in _DEBT_COLS)) for i, d in enumerate(debts)],
        )


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
