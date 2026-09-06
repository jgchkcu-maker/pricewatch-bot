from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from pricewatch.adapters.ozon import OzonSearchAdapter
from pricewatch.adapters.wildberries import WildberriesSearchAdapter
from pricewatch.bootstrap import apply_runtime_schema
from pricewatch.config import WorkerSettings
from pricewatch.db import PsycopgConnectionFactory
from pricewatch.learning_persistence import PostgresLearningStateStore
from pricewatch.outbox import OutboxDispatcher, PostgresOutboxStore
from pricewatch.runtime_repository import RuntimeRepository
from pricewatch.scheduled_runtime import ScheduledPassResult, run_scheduled_pass
from pricewatch.telegram_api import TelegramClient
from pricewatch.transport import HttpJsonFetcher
from pricewatch.verified_store import VerifiedOfferStore
from pricewatch.worker import PriceWorker
from pricewatch.worker_repository import PostgresWorkerRepository

LOGGER = logging.getLogger(__name__)


async def run_scheduled(settings: WorkerSettings) -> ScheduledPassResult:
    connection_factory = PsycopgConnectionFactory(settings.database_url)
    await apply_runtime_schema(connection_factory)

    learning_store = PostgresLearningStateStore(connection_factory)
    await learning_store.initialize()
    runtime_repository = RuntimeRepository(connection_factory)
    worker_repository = PostgresWorkerRepository(connection_factory)
    verified_store = VerifiedOfferStore(connection_factory)

    timeout = httpx.Timeout(settings.marketplace_timeout_seconds)
    async with httpx.AsyncClient(timeout=timeout) as http:
        fetcher = HttpJsonFetcher(http)
        worker = PriceWorker(
            repository=worker_repository,
            verified_store=verified_store,
            learning_store=learning_store,
            adapters={
                "ozon": OzonSearchAdapter(fetcher),
                "wildberries": WildberriesSearchAdapter(fetcher),
            },
            worker_id=settings.worker_id,
            batch_size=settings.worker_batch_size,
            lease_seconds=settings.worker_lease_seconds,
            interval_seconds=settings.scan_interval_seconds,
        )
        telegram = TelegramClient(token=settings.telegram_bot_token, client=http)
        dispatcher = OutboxDispatcher(
            store=PostgresOutboxStore(connection_factory),
            telegram=telegram,
        )
        result = await run_scheduled_pass(
            worker=worker,
            dispatcher=dispatcher,
            runtime_repository=runtime_repository,
            now=datetime.now(UTC),
            outbox_batch_size=settings.outbox_batch_size,
        )

    LOGGER.info(
        "scheduled pass completed",
        extra={
            "processed_products": result.processed_products,
            "dispatched_notifications": result.dispatched_notifications,
            "pruned_price_events": result.pruned_price_events,
        },
    )
    return result


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run_scheduled(WorkerSettings.from_env()))


if __name__ == "__main__":
    main()
