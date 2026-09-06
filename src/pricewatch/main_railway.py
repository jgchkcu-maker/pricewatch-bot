from __future__ import annotations

import asyncio
import logging

from pricewatch.config import Settings
from pricewatch.main_bot import run_bot
from pricewatch.main_worker import run_worker


async def run_railway(settings: Settings) -> None:
    await asyncio.gather(
        run_bot(settings),
        run_worker(settings),
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
