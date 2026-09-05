from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler
from typing import Any

import httpx

from pricewatch.bootstrap import apply_sql_file
from pricewatch.bot import TelegramBotApp
from pricewatch.config import Settings
from pricewatch.db import PsycopgConnectionFactory
from pricewatch.runtime_repository import RuntimeRepository
from pricewatch.search_plan_llm import GeminiSearchPlanProvider
from pricewatch.telegram_api import TelegramClient
from pricewatch.webhook_runtime import TelegramWebhookService

LOGGER = logging.getLogger(__name__)
MAX_BODY_BYTES = 1_000_000


def _webhook_secret() -> str:
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not secret:
        raise RuntimeError("TELEGRAM_WEBHOOK_SECRET must be configured")
    return secret


async def _process_update(
    update: dict[str, Any],
    *,
    headers: dict[str, str],
    secret: str,
) -> None:
    settings = Settings.from_env()
    connection_factory = PsycopgConnectionFactory(settings.database_url)
    await apply_sql_file(connection_factory, "sql/001_runtime.sql")

    async with httpx.AsyncClient(timeout=40.0) as http:
        telegram = TelegramClient(token=settings.telegram_bot_token, client=http)
        provider = GeminiSearchPlanProvider(
            api_key=settings.gemini_api_key,
            model=settings.gemini_model,
            client=http,
        )
        app = TelegramBotApp(
            repository=RuntimeRepository(connection_factory),
            plan_provider=provider,
            telegram=telegram,
        )
        service = TelegramWebhookService(app=app, secret=secret)
        await service.handle(update, headers=headers)


class handler(BaseHTTPRequestHandler):
    def _json_response(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        self._json_response(200, {"ok": True, "runtime": "vercel-telegram-webhook"})

    def do_POST(self) -> None:
        try:
            secret = _webhook_secret()
        except RuntimeError:
            LOGGER.exception("Telegram webhook secret is not configured")
            self._json_response(500, {"ok": False})
            return

        provided = self.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
        if not hmac.compare_digest(provided, secret):
            self._json_response(401, {"ok": False})
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json_response(400, {"ok": False})
            return
        if content_length <= 0 or content_length > MAX_BODY_BYTES:
            self._json_response(400, {"ok": False})
            return

        try:
            payload = json.loads(self.rfile.read(content_length))
        except (json.JSONDecodeError, UnicodeDecodeError):
            self._json_response(400, {"ok": False})
            return
        if not isinstance(payload, dict):
            self._json_response(400, {"ok": False})
            return

        headers = {str(key): str(value) for key, value in self.headers.items()}
        try:
            asyncio.run(_process_update(payload, headers=headers, secret=secret))
        except Exception:
            LOGGER.exception("Telegram webhook update failed")
            self._json_response(500, {"ok": False})
            return

        self._json_response(200, {"ok": True})
