CREATE TABLE IF NOT EXISTS telegram_user (
    id BIGSERIAL PRIMARY KEY,
    telegram_user_id BIGINT NOT NULL UNIQUE,
    chat_id BIGINT NOT NULL,
    delivery_enabled BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS tracked_product (
    id BIGSERIAL PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    product_type TEXT,
    identity_fingerprint TEXT NOT NULL UNIQUE,
    search_plan JSONB NOT NULL,
    lifecycle_state TEXT NOT NULL DEFAULT 'active',
    subscriber_count INTEGER NOT NULL DEFAULT 0 CHECK (subscriber_count >= 0),
    next_scan_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_successful_scan_at TIMESTAMPTZ,
    marketplace_health JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_tracked_product_due
ON tracked_product (next_scan_at)
WHERE lifecycle_state = 'active' AND subscriber_count > 0;

CREATE TABLE IF NOT EXISTS subscription (
    id BIGSERIAL PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES telegram_user(id) ON DELETE CASCADE,
    tracked_product_id BIGINT NOT NULL REFERENCES tracked_product(id) ON DELETE CASCADE,
    status TEXT NOT NULL DEFAULT 'active',
    target_price NUMERIC,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (user_id, tracked_product_id)
);

CREATE INDEX IF NOT EXISTS idx_subscription_product_active
ON subscription (tracked_product_id)
WHERE status = 'active';

CREATE TABLE IF NOT EXISTS marketplace_listing (
    id BIGSERIAL PRIMARY KEY,
    tracked_product_id BIGINT NOT NULL REFERENCES tracked_product(id) ON DELETE CASCADE,
    marketplace TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    variation_id TEXT NOT NULL DEFAULT '',
    seller_id TEXT,
    seller_name TEXT,
    canonical_url TEXT,
    title TEXT NOT NULL,
    attributes JSONB NOT NULL DEFAULT '{}'::jsonb,
    taxonomy JSONB,
    active BOOLEAN NOT NULL DEFAULT TRUE,
    first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    UNIQUE (tracked_product_id, marketplace, listing_id, variation_id)
);

CREATE TABLE IF NOT EXISTS listing_state (
    marketplace_listing_id BIGINT PRIMARY KEY REFERENCES marketplace_listing(id) ON DELETE CASCADE,
    public_price NUMERIC,
    conditional_prices JSONB NOT NULL DEFAULT '{}'::jsonb,
    original_price NUMERIC,
    available BOOLEAN,
    verified_at TIMESTAMPTZ NOT NULL,
    verification_meta JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS price_event (
    id BIGSERIAL PRIMARY KEY,
    tracked_product_id BIGINT NOT NULL REFERENCES tracked_product(id) ON DELETE CASCADE,
    marketplace_listing_id BIGINT NOT NULL REFERENCES marketplace_listing(id) ON DELETE CASCADE,
    public_price NUMERIC,
    conditional_prices JSONB NOT NULL DEFAULT '{}'::jsonb,
    available BOOLEAN,
    verified_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_price_event_product_verified
ON price_event (tracked_product_id, verified_at DESC);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id BIGSERIAL PRIMARY KEY,
    dedup_key TEXT NOT NULL UNIQUE,
    user_id BIGINT NOT NULL REFERENCES telegram_user(id) ON DELETE CASCADE,
    subscription_id BIGINT NOT NULL REFERENCES subscription(id) ON DELETE CASCADE,
    tracked_product_id BIGINT NOT NULL REFERENCES tracked_product(id) ON DELETE CASCADE,
    notification_type TEXT NOT NULL,
    payload JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    claimed_until TIMESTAMPTZ,
    sent_at TIMESTAMPTZ,
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_notification_outbox_due
ON notification_outbox (next_attempt_at)
WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS worker_lease (
    tracked_product_id BIGINT PRIMARY KEY REFERENCES tracked_product(id) ON DELETE CASCADE,
    worker_id TEXT NOT NULL,
    lease_until TIMESTAMPTZ NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS pending_product_confirmation (
    id TEXT PRIMARY KEY,
    user_id BIGINT NOT NULL REFERENCES telegram_user(id) ON DELETE CASCADE,
    raw_input TEXT NOT NULL,
    search_plan JSONB NOT NULL,
    expires_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_pending_confirmation_expiry
ON pending_product_confirmation (expires_at);

CREATE TABLE IF NOT EXISTS taxonomy_evidence (
    id BIGSERIAL PRIMARY KEY,
    tracked_product_id BIGINT REFERENCES tracked_product(id) ON DELETE CASCADE,
    product_type TEXT NOT NULL,
    marketplace TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    subject_id TEXT,
    parent_id TEXT,
    entity TEXT,
    category_path TEXT,
    strength TEXT NOT NULL,
    verified_label BOOLEAN NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_taxonomy_evidence_type_marketplace
ON taxonomy_evidence (product_type, marketplace, created_at DESC);
