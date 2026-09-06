from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

import httpx


class TelegramApiError(RuntimeError):
    pass


class TelegramPermanentError(TelegramApiError):
    pass


class TelegramRateLimitError(TelegramApiError):
    def __init__(self, message: str, *, retry_after_seconds: int | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class _SecretRedactionFilter(logging.Filter):
    def __init__(self, secret: str) -> None:
        super().__init__()
        self.secret = secret

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            rendered = record.getMessage()
        except Exception:
            return True
        if self.secret in rendered:
            record.msg = rendered.replace(self.secret, "[REDACTED]")
            record.args = ()
        return True


def _install_secret_redaction(secret: str) -> None:
    for logger_name in ("httpx", "httpcore"):
        logger = logging.getLogger(logger_name)
        if any(
            isinstance(item, _SecretRedactionFilter) and item.secret == secret
            for item in logger.filters
        ):
            continue
        logger.addFilter(_SecretRedactionFilter(secret))


class TelegramClient:
    def __init__(
        self,
        *,
        token: str,
        client: httpx.AsyncClient | None = None,
        base_url: str = "https://api.telegram.org",
    ) -> None:
        normalized = token.strip()
        if not normalized:
            raise ValueError("token must not be empty")
        _install_secret_redaction(normalized)
        self._token = normalized
        self._base_url = base_url.rstrip("/")
        self._client = client or httpx.AsyncClient(timeout=35.0)
        self._owns_client = client is None

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _call(self, method: str, payload: Mapping[str, Any]) -> Any:
        url = f"{self._base_url}/bot{self._token}/{method}"
        try:
            response = await self._client.post(url, json=dict(payload))
        except httpx.TimeoutException as exc:
            raise TelegramApiError("Telegram API request timed out") from exc
        except httpx.RequestError as exc:
            raise TelegramApiError("Telegram API network request failed") from exc

        try:
            body = response.json()
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise TelegramApiError("Telegram API returned invalid JSON") from exc
        if not isinstance(body, dict):
            raise TelegramApiError("Telegram API response root must be an object")

        description = str(body.get("description") or f"HTTP {response.status_code}")
        parameters = body.get("parameters")
        retry_after: int | None = None
        if isinstance(parameters, dict):
            raw_retry = parameters.get("retry_after")
            if isinstance(raw_retry, int) and raw_retry >= 0:
                retry_after = raw_retry

        if response.status_code == 429:
            raise TelegramRateLimitError(description, retry_after_seconds=retry_after)
        if response.status_code == 403:
            raise TelegramPermanentError(description)
        if response.status_code >= 400 or body.get("ok") is not True:
            raise TelegramApiError(description)
        if "result" not in body:
            raise TelegramApiError("Telegram API response contained no result")
        return body["result"]

    async def get_updates(
        self,
        *,
        offset: int | None = None,
        timeout: int = 30,
    ) -> list[dict[str, Any]]:
        if timeout < 0:
            raise ValueError("timeout must be non-negative")
        payload: dict[str, Any] = {
            "timeout": timeout,
            "allowed_updates": ["message", "callback_query"],
        }
        if offset is not None:
            payload["offset"] = offset
        result = await self._call("getUpdates", payload)
        if not isinstance(result, list):
            raise TelegramApiError("getUpdates result must be an array")
        return [item for item in result if isinstance(item, dict)]

    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not text.strip():
            raise ValueError("text must not be empty")
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = dict(reply_markup)
        result = await self._call("sendMessage", payload)
        if not isinstance(result, dict):
            raise TelegramApiError("sendMessage result must be an object")
        return result

    async def answer_callback_query(
        self,
        callback_query_id: str,
        *,
        text: str | None = None,
    ) -> None:
        payload: dict[str, Any] = {"callback_query_id": callback_query_id}
        if text:
            payload["text"] = text
        await self._call("answerCallbackQuery", payload)
