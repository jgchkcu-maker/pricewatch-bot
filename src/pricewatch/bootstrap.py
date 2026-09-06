from __future__ import annotations

from pathlib import Path

from pricewatch.runtime_repository import ConnectionFactory

_RUNTIME_MIGRATIONS = (
    "sql/001_runtime.sql",
    "sql/002_offer_quality.sql",
)


async def apply_sql_file(connection_factory: ConnectionFactory, path: str | Path) -> None:
    """Apply an idempotent SQL file using one transaction."""

    sql = Path(path).read_text(encoding="utf-8")
    statements = [statement.strip() for statement in sql.split(";") if statement.strip()]
    if not statements:
        raise ValueError("SQL migration file is empty")
    async with connection_factory() as connection:
        for statement in statements:
            await connection.execute(statement)
        await connection.commit()


async def apply_runtime_schema(connection_factory: ConnectionFactory) -> None:
    """Apply every additive runtime migration in deterministic order."""

    for migration in _RUNTIME_MIGRATIONS:
        await apply_sql_file(connection_factory, migration)
