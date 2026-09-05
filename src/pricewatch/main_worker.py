from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta

import httpx

from pricewatch.adapters.ozon import OzonSearchAdapter
from pricewatch.adapters.wildberries import WildberriesSearchAdapter
from pricewatch.bootstrap import apply_sql_file
from pricewatch.config import Settings
from pricewatch.db import PsycopgConnectionFactory
from pricewatch.learning_persistence import PostgresLearningStateStore
from pricewatch.runtime_repository import RuntimeRepository
from pricewatch.transport import HttpJsonFetcher
from pricewatch.verified_store import VerifiedOfferStore
from pricewatch.worker import PriceWorker
from pricewatch.worker_repository import PostgresWorkerRepository

LOGGER = logging.getLogger(__name__)


async def run_worker(settings: Settings) -> None:
    connection_factory = PsycopgConnectionFactory(settings.database_url)
    await apply_sql_file(connection_factory, "sql/001_runtime.sql")
    learning_store = PostgresLearningStateStore(connection_factory)
    await learning_store.initialize()

    runtime_repository = RuntimeRepository(connection_factory)
    worker_repository = PostgresWorkerRepository(connection_factory)
    verified_store = VerifiedOfferStore(connection_factory)

    timeout = httpx.Timeout(settings.marketplace_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as http:
        fetcher = HttpJsonFetcher(http)
        adapters = {
            "ozon": OzonSearchAdapter(fetcher),
            "wildberries": WildberriesSearchAdapter(fetcher),
        }
        worker = PriceWorker(
            repository=worker_repository,
            verified_store=verified_store,
            learning_store=learning_store,
            adapters=adapters,
            worker_id=settings.worker_id,
            batch_size=settings.worker_batch_size,
            lease_seconds=settings.worker_lease_seconds,
            interval_seconds=settings.scan_interval_seconds,
        )

        next_maintenance = datetime.now(UTC)
        while True:
            now = datetime.now(UTC)
            try:
                processed = await worker.run_once(now)
            except Exception:
                LOGGER.exception("worker pass failed")
                processed = 0

            if now >= next_maintenance:
                try:
                    deleted = await runtime_repository.prune_price_events(now=now)
                    if deleted:
                        LOGGER.info("pruned old price events", extra={"deleted": deleted})
                except Exception:
                    LOGGER.exception("price-event maintenance failed")
                next_maintenance = now + timedelta(hours=1)

            await asyncio.sleep(1 if processed else 5)


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    asyncio.run(run_worker(settings))


if __name__ == "__main__":
    main()
