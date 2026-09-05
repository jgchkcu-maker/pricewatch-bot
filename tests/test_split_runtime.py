import asyncio
from datetime import UTC, datetime

import pytest

from pricewatch.scheduled_runtime import run_scheduled_pass
from pricewatch.webhook_runtime import TelegramWebhookService, WebhookUnauthorized

NOW = datetime(2026, 9, 5, 12, 0, tzinfo=UTC)


class FakeWorker:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def run_once(self, now: datetime) -> int:
        assert now == NOW
        self.calls.append("worker")
        return 3


class FakeDispatcher:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def run_once(self, *, now: datetime, limit: int = 50) -> int:
        assert now == NOW
        assert limit == 17
        self.calls.append("outbox")
        return 2


class FakeRuntimeRepository:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls

    async def prune_price_events(self, *, now: datetime) -> int:
        assert now == NOW
        self.calls.append("maintenance")
        return 4


def test_scheduled_pass_runs_worker_then_outbox_then_maintenance() -> None:
    calls: list[str] = []

    result = asyncio.run(
        run_scheduled_pass(
            worker=FakeWorker(calls),
            dispatcher=FakeDispatcher(calls),
            runtime_repository=FakeRuntimeRepository(calls),
            now=NOW,
            outbox_batch_size=17,
        )
    )

    assert result.processed_products == 3
    assert result.dispatched_notifications == 2
    assert result.pruned_price_events == 4
    assert calls == ["worker", "outbox", "maintenance"]


class FakeBotApp:
    def __init__(self) -> None:
        self.updates: list[dict[str, object]] = []

    async def handle_update(self, update):
        self.updates.append(dict(update))


def test_webhook_service_requires_matching_telegram_secret() -> None:
    app = FakeBotApp()
    service = TelegramWebhookService(app=app, secret="super-secret")
    update = {"update_id": 7, "message": {"text": "/start"}}

    with pytest.raises(WebhookUnauthorized):
        asyncio.run(service.handle(update, headers={}))
    with pytest.raises(WebhookUnauthorized):
        asyncio.run(
            service.handle(
                update,
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
        )

    assert app.updates == []


def test_webhook_service_passes_verified_update_to_existing_bot_app() -> None:
    app = FakeBotApp()
    service = TelegramWebhookService(app=app, secret="super-secret")
    update = {"update_id": 8, "callback_query": {"id": "cb"}}

    asyncio.run(
        service.handle(
            update,
            headers={"x-telegram-bot-api-secret-token": "super-secret"},
        )
    )

    assert app.updates == [update]
