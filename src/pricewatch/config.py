from __future__ import annotations

import os
import socket
from collections.abc import Mapping
from dataclasses import dataclass

from pricewatch.search_plan_llm import DEFAULT_SEARCH_PLAN_MODEL


def _required(env: Mapping[str, str], key: str) -> str:
    value = env.get(key, "").strip()
    if not value:
        raise ValueError(f"{key} must be configured")
    return value


def _positive_int(env: Mapping[str, str], key: str, default: int) -> int:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _positive_int_with_fallback(
    env: Mapping[str, str],
    key: str,
    fallback_key: str,
    default: int,
) -> int:
    if key in env:
        return _positive_int(env, key, default)
    return _positive_int(env, fallback_key, default)


def _positive_float(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{key} must be numeric") from exc
    if value <= 0:
        raise ValueError(f"{key} must be positive")
    return value


def _worker_id(source: Mapping[str, str]) -> str:
    return source.get("WORKER_ID", "").strip() or socket.gethostname()


@dataclass(frozen=True, slots=True)
class WorkerSettings:
    database_url: str
    telegram_bot_token: str
    worker_id: str = "pricewatch-worker"
    scan_interval_seconds: int = 240
    worker_batch_size: int = 20
    worker_lease_seconds: int = 180
    marketplace_timeout_seconds: float = 20.0
    outbox_batch_size: int = 50

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> WorkerSettings:
        source = os.environ if env is None else env
        return cls(
            database_url=_required(source, "DATABASE_URL"),
            telegram_bot_token=_required(source, "TELEGRAM_BOT_TOKEN"),
            worker_id=_worker_id(source),
            scan_interval_seconds=_positive_int(source, "SCAN_INTERVAL_SECONDS", 240),
            worker_batch_size=_positive_int(source, "WORKER_BATCH_SIZE", 20),
            worker_lease_seconds=_positive_int_with_fallback(
                source,
                "LEASE_SECONDS",
                "WORKER_LEASE_SECONDS",
                180,
            ),
            marketplace_timeout_seconds=_positive_float(
                source,
                "MARKETPLACE_TIMEOUT_SECONDS",
                20.0,
            ),
            outbox_batch_size=_positive_int(source, "OUTBOX_BATCH_SIZE", 50),
        )


@dataclass(frozen=True, slots=True)
class Settings:
    database_url: str
    telegram_bot_token: str
    gemini_api_key: str
    gemini_model: str = DEFAULT_SEARCH_PLAN_MODEL
    worker_id: str = "pricewatch-worker"
    scan_interval_seconds: int = 240
    worker_batch_size: int = 20
    worker_lease_seconds: int = 180
    marketplace_timeout_seconds: float = 20.0
    outbox_batch_size: int = 50
    poll_timeout_seconds: int = 30

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        source = os.environ if env is None else env
        model = source.get("GEMINI_MODEL", DEFAULT_SEARCH_PLAN_MODEL).strip()
        if not model:
            raise ValueError("GEMINI_MODEL must not be empty")
        return cls(
            database_url=_required(source, "DATABASE_URL"),
            telegram_bot_token=_required(source, "TELEGRAM_BOT_TOKEN"),
            gemini_api_key=_required(source, "GEMINI_API_KEY"),
            gemini_model=model,
            worker_id=_worker_id(source),
            scan_interval_seconds=_positive_int(source, "SCAN_INTERVAL_SECONDS", 240),
            worker_batch_size=_positive_int(source, "WORKER_BATCH_SIZE", 20),
            worker_lease_seconds=_positive_int_with_fallback(
                source,
                "LEASE_SECONDS",
                "WORKER_LEASE_SECONDS",
                180,
            ),
            marketplace_timeout_seconds=_positive_float(
                source,
                "MARKETPLACE_TIMEOUT_SECONDS",
                20.0,
            ),
            outbox_batch_size=_positive_int(source, "OUTBOX_BATCH_SIZE", 50),
            poll_timeout_seconds=_positive_int_with_fallback(
                source,
                "TELEGRAM_POLL_TIMEOUT",
                "TELEGRAM_POLL_TIMEOUT_SECONDS",
                30,
            ),
        )
