from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime

import httpx

from pricewatch.bootstrap import apply_sql_file
from pricewatch.bot import TelegramBotApp
from pricewatch.config import Settings
from pricewatch.db import PsycopgConnectionFactory
from pricewatch.outbox import OutboxDispatcher, PostgresOutboxStore
from pricewatch.runtime_repository import RuntimeRepository
from pricewatch.search_plan_llm import GeminiSearchPlanProvider
from pricewatch.telegram_api import TelegramApiError, TelegramClient

LOGGER = logging.getLogger(__name__)


async def run_bot(settings: Settings) -> None:
    connection_factory = PsycopgConnectionFactory(settings.database_url)
    await apply_sql_file(connection_factory, "sql/001_runtime.sql")

    async with httpx.AsyncClient(timeout=40.0) as http:
        telegram = TelegramClient(token=settings.telegram_bot_token, client=http)
        provider = GeminiSearchPlanProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            client=http,
        )
        repository = RuntimeRepository(connection_factory)
        app = TelegramBotApp(
            repository=repository,
            plan_provider=provider,
            telegram=telegram,
        )
        dispatcher = OutboxDispatcher(
            store=PostgresOutboxStore(connection_factory),
            telegram=telegram,
        )

        offset: int | None = None
        while True:
            try:
                updates = await telegram.get_updates(
                    offset=offset,
                    timeout=settings.poll_timeout_seconds,
                )
            except TelegramApiError:
                LOGGER.exception("Telegram getUpdates failed")
                await asyncio.sleep(3)
                continue

            for update in updates:
                update_id = update.get("update_id")
                try:
                    await app.handle_update(update)
                except Exception:
                    LOGGER.exception("Telegram update handling failed", extra={"update": update_id})
                    break
                if isinstance(update_id, int):
                    offset = update_id + 1

            try:
                await dispatcher.run_once(
                    now=datetime.now(UTC),
                    limit=settings.outbox_batch_size,
                )
            except Exception:
                LOGGER.exception("outbox dispatch pass failed")


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    settings = Settings.from_env()
    asyncio.run(run_bot(settings))


if __name__ == "__main__":
    main()
