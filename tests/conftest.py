"""Shared test-database setup.

Set ``DEBTMANAGER_TEST_DB`` to a Postgres URL to run the whole suite against the
backend production uses. Unset, every test gets its own throwaway SQLite file.
"""

import importlib
import os

# Every table, ordered so a plain DROP would work even without CASCADE.
_TABLES = ("snapshots, payments, debts, profiles, sessions, login_events, "
           "recovery_codes, users")


def fresh_db(monkeypatch, tmp_path, name: str = "t.db"):
    """Point ``debtapp.db`` at an empty database and return the reloaded module.

    ``debtapp.db`` resolves the backend at import time, hence the reload.
    """
    monkeypatch.setenv(
        "DEBTMANAGER_DB",
        os.environ.get("DEBTMANAGER_TEST_DB") or str(tmp_path / name),
    )
    import debtapp.db as db
    importlib.reload(db)
    if db.IS_POSTGRES:
        # tmp_path gives SQLite a fresh file per test; Postgres is one shared
        # database, so isolation means rebuilding the schema each time.
        with db._conn() as con:
            con.execute(f"DROP TABLE IF EXISTS {_TABLES} CASCADE")
    db.init_db()
    return db
