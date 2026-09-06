from __future__ import annotations

import asyncio

import pricewatch.main_railway as main_railway
from pricewatch.config import Settings


def _settings() -> Settings:
    return Settings.from_env(
        {
            "DATABASE_URL": "postgresql://example",
            "TELEGRAM_BOT_TOKEN": "token",
            "GEMINI_API_KEY": "gemini",
        }
    )


def test_railway_bootstraps_schema_once_before_starting_bot_and_worker(monkeypatch) -> None:
    events: list[object] = []

    class DummyConnectionFactory:
        def __init__(self, database_url: str) -> None:
            events.append(("factory", database_url))

    async def fake_apply_sql_file(connection_factory, path: str) -> None:
        events.append(("bootstrap", path))

    async def fake_run_bot(settings: Settings, *, bootstrap_schema: bool) -> None:
        events.append(("bot", bootstrap_schema))

    async def fake_run_worker(settings: Settings, *, bootstrap_schema: bool) -> None:
        events.append(("worker", bootstrap_schema))

    monkeypatch.setattr(main_railway, "PsycopgConnectionFactory", DummyConnectionFactory)
    monkeypatch.setattr(main_railway, "apply_sql_file", fake_apply_sql_file)
    monkeypatch.setattr(main_railway, "run_bot", fake_run_bot)
    monkeypatch.setattr(main_railway, "run_worker", fake_run_worker)

    asyncio.run(main_railway.run_railway(_settings()))

    assert events[0][0] == "factory"
    assert events[1] == ("bootstrap", "sql/001_runtime.sql")
    assert events.count(("bootstrap", "sql/001_runtime.sql")) == 1
    assert ("bot", False) in events
    assert ("worker", False) in events
