from pricewatch.config import Settings
from pricewatch.search_plan_llm import DEFAULT_SEARCH_PLAN_MODEL


def base_env() -> dict[str, str]:
    return {
        "DATABASE_URL": "postgresql://pricewatch:pw@postgres/pricewatch",
        "TELEGRAM_BOT_TOKEN": "123:secret",
        "GEMINI_API_KEY": "gemini-secret",
    }


def test_settings_require_core_secrets_and_keep_conservative_defaults() -> None:
    settings = Settings.from_env(base_env())

    assert settings.database_url.startswith("postgresql://")
    assert settings.gemini_model == DEFAULT_SEARCH_PLAN_MODEL
    assert settings.scan_interval_seconds == 240
    assert settings.worker_batch_size == 20
    assert settings.marketplace_timeout_seconds == 20.0
    assert settings.outbox_batch_size == 50


def test_settings_accept_runtime_overrides() -> None:
    env = base_env()
    env.update(
        {
            "GEMINI_MODEL": "gemini-custom",
            "SCAN_INTERVAL_SECONDS": "300",
            "WORKER_BATCH_SIZE": "7",
            "WORKER_ID": "worker-a",
        }
    )
    settings = Settings.from_env(env)

    assert settings.gemini_model == "gemini-custom"
    assert settings.scan_interval_seconds == 300
    assert settings.worker_batch_size == 7
    assert settings.worker_id == "worker-a"


def test_settings_fail_fast_on_missing_or_invalid_required_values() -> None:
    for missing in ("DATABASE_URL", "TELEGRAM_BOT_TOKEN", "GEMINI_API_KEY"):
        env = base_env()
        env.pop(missing)
        try:
            Settings.from_env(env)
        except ValueError as exc:
            assert missing in str(exc)
        else:
            raise AssertionError(f"missing {missing} must fail")

    env = base_env()
    env["SCAN_INTERVAL_SECONDS"] = "0"
    try:
        Settings.from_env(env)
    except ValueError as exc:
        assert "SCAN_INTERVAL_SECONDS" in str(exc)
    else:
        raise AssertionError("non-positive scan cadence must fail")
