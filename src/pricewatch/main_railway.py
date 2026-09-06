from __future__ import annotations

import asyncio
import logging

from pricewatch.bootstrap import apply_sql_file
from pricewatch.config import Settings
from pricewatch.db import PsycopgConnectionFactory
from pricewatch.main_bot import run_bot
from pricewatch.main_worker import run_worker


async def run_railway(settings: Settings) -> None:
    connection_factory = PsycopgConnectionFactory(settings.database_url)
    await apply_sql_file(connection_factory, "sql/001_runtime.sql")

    await asyncio.gather(
        run_bot(settings, bootstrap_schema=False),
        run_worker(settings, bootstrap_schema=False),
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    asyncio.run(run_railway(settings))


if __name__ == "__main__":
    main()
