-- Migration 002: kick_rewards
-- Maps Kick channel-point reward IDs to a fixed USD payout value.

CREATE TABLE IF NOT EXISTS kick_rewards (
    id              BIGSERIAL PRIMARY KEY,
    kick_reward_id  TEXT NOT NULL UNIQUE,       -- reward ID from Kick's API
    title           TEXT NOT NULL,
    point_cost      INTEGER NOT NULL CHECK (point_cost > 0),
    amount_usd      NUMERIC(12, 2) NOT NULL CHECK (amount_usd > 0),
    is_active       BOOLEAN NOT NULL DEFAULT true,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_kick_rewards_active ON kick_rewards(is_active);
