ALTER TABLE marketplace_listing
    ADD COLUMN IF NOT EXISTS quality_status TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS quality_reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    ADD COLUMN IF NOT EXISTS quality_observation_count INTEGER NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS quality_checked_at TIMESTAMPTZ;

ALTER TABLE listing_state
    ADD COLUMN IF NOT EXISTS quality_status TEXT NOT NULL DEFAULT 'legacy',
    ADD COLUMN IF NOT EXISTS quality_reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb;

ALTER TABLE price_event
    ADD COLUMN IF NOT EXISTS quality_status TEXT NOT NULL DEFAULT 'legacy';

CREATE TABLE IF NOT EXISTS offer_quality_observation (
    id BIGSERIAL PRIMARY KEY,
    tracked_product_id BIGINT NOT NULL REFERENCES tracked_product(id) ON DELETE CASCADE,
    marketplace_listing_id BIGINT REFERENCES marketplace_listing(id) ON DELETE SET NULL,
    marketplace TEXT NOT NULL,
    listing_id TEXT NOT NULL,
    variation_id TEXT,
    seller_id TEXT,
    status TEXT NOT NULL,
    reason_codes JSONB NOT NULL DEFAULT '[]'::jsonb,
    observed_price NUMERIC,
    reference_price NUMERIC,
    price_ratio NUMERIC,
    confirmation_count INTEGER NOT NULL DEFAULT 0,
    observed_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_offer_quality_observation_product_marketplace_observed
    ON offer_quality_observation (tracked_product_id, marketplace, observed_at DESC);

CREATE INDEX IF NOT EXISTS idx_price_event_trusted_reference
    ON price_event (tracked_product_id, marketplace_listing_id, verified_at DESC)
    WHERE quality_status = 'trusted' AND public_price IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_listing_state_trusted_current
    ON listing_state (marketplace_listing_id, verified_at DESC)
    WHERE quality_status = 'trusted';
