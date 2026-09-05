from __future__ import annotations

import hmac
from collections.abc import Mapping
from typing import Any, Protocol

TELEGRAM_SECRET_HEADER = "x-telegram-bot-api-secret-token"


class TelegramUpdateApp(Protocol):
    async def handle_update(self, update: Mapping[str, Any]) -> None: ...


class WebhookUnauthorized(PermissionError):
    pass


class TelegramWebhookService:
    def __init__(self, *, app: TelegramUpdateApp, secret: str) -> None:
        normalized = secret.strip()
        if not normalized:
            raise ValueError("webhook secret must not be empty")
        self._app = app
        self._secret = normalized

    async def handle(
        self,
        update: Mapping[str, Any],
        *,
        headers: Mapping[str, str],
    ) -> None:
        normalized_headers = {str(key).casefold(): str(value) for key, value in headers.items()}
        provided = normalized_headers.get(TELEGRAM_SECRET_HEADER, "")
        if not hmac.compare_digest(provided, self._secret):
            raise WebhookUnauthorized("invalid Telegram webhook secret")
        await self._app.handle_update(update)
