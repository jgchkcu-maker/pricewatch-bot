from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


class WorkerPass(Protocol):
    async def run_once(self, now: datetime) -> int: ...


class OutboxPass(Protocol):
    async def run_once(self, *, now: datetime, limit: int = 50) -> int: ...


class RuntimeMaintenance(Protocol):
    async def prune_price_events(self, *, now: datetime) -> int: ...


@dataclass(frozen=True, slots=True)
class ScheduledPassResult:
    processed_products: int
    dispatched_notifications: int
    pruned_price_events: int


async def run_scheduled_pass(
    *,
    worker: WorkerPass,
    dispatcher: OutboxPass,
    runtime_repository: RuntimeMaintenance,
    now: datetime,
    outbox_batch_size: int,
) -> ScheduledPassResult:
    if outbox_batch_size <= 0:
        raise ValueError("outbox_batch_size must be positive")

    processed = await worker.run_once(now)
    dispatched = await dispatcher.run_once(now=now, limit=outbox_batch_size)
    pruned = await runtime_repository.prune_price_events(now=now)
    return ScheduledPassResult(
        processed_products=processed,
        dispatched_notifications=dispatched,
        pruned_price_events=pruned,
    )
