import asyncio

from pricewatch.config import Settings
from pricewatch.main_railway import run_railway


class FakeSettings(Settings):
    pass


def test_run_railway_starts_bot_and_worker(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_bot(settings: Settings) -> None:
        calls.append("bot")

    async def fake_worker(settings: Settings) -> None:
        calls.append("worker")

    monkeypatch.setattr("pricewatch.main_railway.run_bot", fake_bot)
    monkeypatch.setattr("pricewatch.main_railway.run_worker", fake_worker)

    settings = Settings(
        database_url="postgresql://example/db",
        telegram_bot_token="telegram-token",
        gemini_api_key="gemini-key",
    )
    asyncio.run(run_railway(settings))

    assert calls == ["bot", "worker"]
