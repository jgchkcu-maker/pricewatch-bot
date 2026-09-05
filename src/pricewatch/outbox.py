from __future__ import annotations

import json
from collections.abc import Mapping
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol

from pricewatch.telegram_api import (
    TelegramApiError,
    TelegramPermanentError,
    TelegramRateLimitError,
)
from pricewatch.telegram_views import render_new_low


class _Cursor(Protocol):
    async def fetchall(self) -> list[tuple[object, ...]]: ...


class _Connection(Protocol):
    async def execute(
        self,
        query: str,
        params: tuple[object, ...] | None = None,
    ) -> _Cursor: ...

    async def commit(self) -> None: ...


class ConnectionFactory(Protocol):
    def __call__(self) -> AbstractAsyncContextManager[_Connection]: ...


@dataclass(frozen=True, slots=True)
class OutboxItem:
    id: int
    user_id: int
    subscription_id: int
    tracked_product_id: int
    notification_type: str
    payload: Mapping[str, Any]
    attempt_count: int


def _payload(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        decoded = json.loads(value)
    elif isinstance(value, Mapping):
        decoded = dict(value)
    else:
        raise ValueError("outbox payload must be a JSON object")
    if not isinstance(decoded, dict):
        raise ValueError("outbox payload must be a JSON object")
    return decoded


class PostgresOutboxStore:
    def __init__(self, connection_factory: ConnectionFactory, *, lease_seconds: int = 60) -> None:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds must be positive")
        self._connection_factory = connection_factory
        self._lease_seconds = lease_seconds

    async def claim_due(
        self,
        *,
        now: datetime,
        limit: int,
    ) -> tuple[OutboxItem, ...]:
        if limit <= 0:
            raise ValueError("limit must be positive")
        claimed_until = now + timedelta(seconds=self._lease_seconds)
        async with self._connection_factory() as connection:
            cursor = await connection.execute(
                """
                SELECT id, user_id, subscription_id, tracked_product_id,
                       notification_type, payload, attempt_count
                FROM notification_outbox
                WHERE status = 'pending'
                  AND next_attempt_at <= %s
                  AND (claimed_until IS NULL OR claimed_until <= %s)
                ORDER BY id ASC
                FOR UPDATE SKIP LOCKED
                LIMIT %s
                """,
                (now, now, limit),
            )
            rows = await cursor.fetchall()
            items = tuple(
                OutboxItem(
                    id=int(row[0]),
                    user_id=int(row[1]),
                    subscription_id=int(row[2]),
                    tracked_product_id=int(row[3]),
                    notification_type=str(row[4]),
                    payload=_payload(row[5]),
                    attempt_count=int(row[6]),
                )
                for row in rows
            )
            for item in items:
                await connection.execute(
                    """
                    UPDATE notification_outbox
                    SET claimed_until = %s
                    WHERE id = %s
                    """,
                    (claimed_until, item.id),
                )
            await connection.commit()
        return items

    async def mark_sent(self, item_id: int, *, now: datetime) -> None:
        async with self._connection_factory() as connection:
            await connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'sent', sent_at = %s, claimed_until = NULL,
                    last_error = NULL
                WHERE id = %s
                """,
                (now, item_id),
            )
            await connection.commit()

    async def mark_retry(
        self,
        item: OutboxItem,
        *,
        now: datetime,
        error: str,
        retry_after_seconds: int | None = None,
    ) -> None:
        delay = retry_after_seconds
        if delay is None:
            delay = min(3600, 30 * (2 ** min(item.attempt_count, 6)))
        delay = max(1, delay)
        next_attempt = now + timedelta(seconds=delay)
        async with self._connection_factory() as connection:
            await connection.execute(
                """
                UPDATE notification_outbox
                SET attempt_count = attempt_count + 1,
                    next_attempt_at = %s,
                    claimed_until = NULL,
                    last_error = %s
                WHERE id = %s AND status = 'pending'
                """,
                (next_attempt, error[:1000], item.id),
            )
            await connection.commit()

    async def mark_permanent_failure(self, item: OutboxItem, *, error: str) -> None:
        async with self._connection_factory() as connection:
            await connection.execute(
                """
                UPDATE notification_outbox
                SET status = 'failed', claimed_until = NULL,
                    attempt_count = attempt_count + 1,
                    last_error = %s
                WHERE id = %s
                """,
                (error[:1000], item.id),
            )
            await connection.execute(
                """
                UPDATE telegram_user
                SET delivery_enabled = FALSE, updated_at = NOW()
                WHERE id = %s
                """,
                (item.user_id,),
            )
            await connection.commit()


class OutboxStore(Protocol):
    async def claim_due(self, *, now: datetime, limit: int) -> tuple[OutboxItem, ...]: ...

    async def mark_sent(self, item_id: int, *, now: datetime) -> None: ...

    async def mark_retry(
        self,
        item: OutboxItem,
        *,
        now: datetime,
        error: str,
        retry_after_seconds: int | None = None,
    ) -> None: ...

    async def mark_permanent_failure(self, item: OutboxItem, *, error: str) -> None: ...


class TelegramSender(Protocol):
    async def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


class OutboxDispatcher:
    def __init__(self, *, store: OutboxStore, telegram: TelegramSender) -> None:
        self._store = store
        self._telegram = telegram

    async def run_once(self, *, now: datetime, limit: int = 50) -> int:
        items = await self._store.claim_due(now=now, limit=limit)
        sent = 0
        for item in items:
            if item.notification_type != "new_low":
                await self._store.mark_permanent_failure(
                    item,
                    error=f"unknown notification type: {item.notification_type}",
                )
                continue
            view = render_new_low(item.payload)
            chat_id = item.payload.get("chat_id")
            if not isinstance(chat_id, int):
                await self._store.mark_permanent_failure(
                    item,
                    error="outbox payload contains no integer chat_id",
                )
                continue
            try:
                await self._telegram.send_message(
                    chat_id,
                    view.text,
                    reply_markup=view.reply_markup,
                )
            except TelegramRateLimitError as exc:
                await self._store.mark_retry(
                    item,
                    now=now,
                    error=str(exc),
                    retry_after_seconds=exc.retry_after_seconds,
                )
            except TelegramPermanentError as exc:
                await self._store.mark_permanent_failure(item, error=str(exc))
            except TelegramApiError as exc:
                await self._store.mark_retry(item, now=now, error=str(exc))
            else:
                await self._store.mark_sent(item.id, now=now)
                sent += 1
        return sent
