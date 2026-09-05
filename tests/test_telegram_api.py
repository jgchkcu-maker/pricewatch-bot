import asyncio
import json

import httpx

from pricewatch.telegram_api import (
    TelegramApiError,
    TelegramClient,
    TelegramPermanentError,
    TelegramRateLimitError,
)


def test_get_updates_and_send_message_use_bot_api_without_internal_retry() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content) if request.content else {}
        calls.append((request.url.path, payload))
        if request.url.path.endswith("/getUpdates"):
            return httpx.Response(200, json={"ok": True, "result": [{"update_id": 10}]})
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 99}})

    http = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    client = TelegramClient(token="123:secret", client=http)

    updates = asyncio.run(client.get_updates(offset=7, timeout=20))
    message = asyncio.run(
        client.send_message(
            100,
            "hello",
            reply_markup={"inline_keyboard": [[{"text": "x", "callback_data": "x"}]]},
        )
    )
    asyncio.run(http.aclose())

    assert updates == [{"update_id": 10}]
    assert message["message_id"] == 99
    assert calls[0][1]["offset"] == 7
    assert calls[0][1]["timeout"] == 20
    assert calls[1][1]["chat_id"] == 100


def test_rate_limit_and_blocked_chat_are_typed_errors() -> None:
    def rate_limited(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "ok": False,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 17},
            },
        )

    http = httpx.AsyncClient(transport=httpx.MockTransport(rate_limited))
    client = TelegramClient(token="123:secret", client=http)
    try:
        asyncio.run(client.send_message(1, "x"))
    except TelegramRateLimitError as exc:
        assert exc.retry_after_seconds == 17
    else:
        raise AssertionError("429 must be typed as TelegramRateLimitError")
    asyncio.run(http.aclose())

    def blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, json={"ok": False, "description": "bot was blocked"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(blocked))
    client = TelegramClient(token="123:secret", client=http)
    try:
        asyncio.run(client.send_message(1, "x"))
    except TelegramPermanentError as exc:
        assert "blocked" in str(exc).lower()
    else:
        raise AssertionError("403 must be permanent")
    asyncio.run(http.aclose())


def test_malformed_success_payload_fails_closed() -> None:
    def malformed(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "bad request"})

    http = httpx.AsyncClient(transport=httpx.MockTransport(malformed))
    client = TelegramClient(token="123:secret", client=http)
    try:
        asyncio.run(client.get_updates())
    except TelegramApiError as exc:
        assert "bad request" in str(exc).lower()
    else:
        raise AssertionError("ok=false must fail")
    asyncio.run(http.aclose())
