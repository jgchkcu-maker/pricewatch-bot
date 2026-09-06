from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import psycopg


class PsycopgConnectionFactory:
    """Create short-lived async PostgreSQL connections with safe transaction semantics."""

    def __init__(self, dsn: str) -> None:
        normalized = dsn.strip()
        if not normalized:
            raise ValueError("dsn must not be empty")
        self._dsn = normalized

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[psycopg.AsyncConnection]:
        connection = await psycopg.AsyncConnection.connect(self._dsn)
        try:
            yield connection
        except Exception:
            await connection.rollback()
            raise
        finally:
            await connection.close()
