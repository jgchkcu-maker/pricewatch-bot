from __future__ import annotations

from pathlib import Path

from pricewatch.runtime_repository import ConnectionFactory


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
