import json
from pathlib import Path

from pricewatch.config import WorkerSettings

ROOT = Path(__file__).resolve().parents[1]


def test_worker_settings_do_not_require_gemini_for_github_runtime() -> None:
    settings = WorkerSettings.from_env(
        {
            "DATABASE_URL": "postgresql://example",
            "TELEGRAM_BOT_TOKEN": "token",
            "WORKER_ID": "gha-test",
            "SCAN_INTERVAL_SECONDS": "240",
            "WORKER_BATCH_SIZE": "23",
            "LEASE_SECONDS": "180",
            "MARKETPLACE_TIMEOUT_SECONDS": "19",
            "OUTBOX_BATCH_SIZE": "31",
        }
    )

    assert settings.database_url == "postgresql://example"
    assert settings.telegram_bot_token == "token"
    assert settings.worker_id == "gha-test"
    assert settings.worker_batch_size == 23
    assert settings.outbox_batch_size == 31


def test_github_actions_runs_one_shot_worker_every_five_minutes() -> None:
    workflow = (ROOT / ".github/workflows/pricewatch-scheduled.yml").read_text()

    assert "*/5 * * * *" in workflow
    assert "workflow_dispatch:" in workflow
    assert "pricewatch-scheduled" in workflow
    assert "DATABASE_URL: ${{ secrets.DATABASE_URL }}" in workflow
    assert "TELEGRAM_BOT_TOKEN: ${{ secrets.TELEGRAM_BOT_TOKEN }}" in workflow
    assert "GEMINI_API_KEY" not in workflow


def test_vercel_only_exposes_telegram_webhook_function() -> None:
    config = json.loads((ROOT / "vercel.json").read_text())
    api_source = (ROOT / "api/telegram.py").read_text()

    assert "api/telegram.py" in config["functions"]
    assert "crons" not in config
    assert "TelegramWebhookService" in api_source
    assert "TELEGRAM_WEBHOOK_SECRET" in api_source
