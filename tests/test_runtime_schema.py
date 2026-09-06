from pathlib import Path


def test_runtime_migration_contains_required_tables_and_constraints() -> None:
    sql = Path("sql/001_runtime.sql").read_text(encoding="utf-8").lower()

    for table in (
        "telegram_user",
        "tracked_product",
        "subscription",
        "marketplace_listing",
        "listing_state",
        "price_event",
        "notification_outbox",
        "worker_lease",
        "pending_product_confirmation",
        "taxonomy_evidence",
    ):
        assert f"create table if not exists {table}" in sql

    assert "identity_fingerprint text not null unique" in sql
    assert "unique (user_id, tracked_product_id)" in sql
    assert "dedup_key text not null unique" in sql
    assert "unique (tracked_product_id, marketplace, listing_id, variation_id" in sql


def test_runtime_schema_keeps_public_and_conditional_prices_separate() -> None:
    sql = Path("sql/001_runtime.sql").read_text(encoding="utf-8").lower()

    assert "public_price numeric" in sql
    assert "conditional_prices jsonb" in sql


def test_quality_migration_adds_status_and_observation_table() -> None:
    sql = Path("sql/002_offer_quality.sql").read_text(encoding="utf-8").lower()

    assert "quality_status" in sql
    assert "offer_quality_observation" in sql
    assert "add column if not exists" in sql
    assert "default 'legacy'" in sql
    assert "tracked_product_id, marketplace, observed_at desc" in sql
