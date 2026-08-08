"""Process-wide Postgres connection pools, keyed by DSN.

Deliberately a separate module from ``db``: the test suite calls
``importlib.reload(debtapp.db)`` for every test, and a pool held in ``db``
would be orphaned — still holding open connections — on each reload. This
module is never reloaded, so one DSN means one pool for the life of the
process.
"""

from __future__ import annotations

from typing import Any, Dict

_POOLS: Dict[str, Any] = {}


def get(dsn: str) -> Any:
    """Return the pool for ``dsn``, creating it on first use.

    ``max_idle`` sits under Neon's five-minute autosuspend so we retire
    connections before the server does, and ``check`` revalidates anything that
    slipped through — otherwise the first request after an idle spell gets
    handed a socket the database already closed.
    """
    pool = _POOLS.get(dsn)
    if pool is None:
        from psycopg.rows import dict_row
        from psycopg_pool import ConnectionPool

        pool = ConnectionPool(
            dsn,
            min_size=0,
            max_size=5,
            max_idle=240,
            check=ConnectionPool.check_connection,
            kwargs={"row_factory": dict_row},
            open=True,
        )
        _POOLS[dsn] = pool
    return pool
