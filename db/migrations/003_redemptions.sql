-- Migration 003: redemptions
-- One row per claim, regardless of source (Discord code or Kick reward).
-- This is the convergence point that both intake paths feed into.

CREATE TABLE IF NOT EXISTS redemptions (
    id                  BIGSERIAL PRIMARY KEY,
    source              TEXT NOT NULL CHECK (source IN ('discord_code', 'kick_reward')),
    source_ref_id       BIGINT NOT NULL,        -- FK to giveaway_codes.id OR kick_rewards.id
    kick_redemption_id  TEXT,                    -- Kick's own redemption ID, for reconciliation
    discord_user_id     TEXT,
    kick_user_id        TEXT,
    solana_address      TEXT NOT NULL,
    amount_usd          NUMERIC(12, 2) NOT NULL CHECK (amount_usd > 0),
    claimed_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    status              TEXT NOT NULL DEFAULT 'pending_validation'
                            CHECK (status IN ('pending_validation', 'validated', 'rejected')),
    rejection_reason    TEXT,

    -- Exactly one of discord_user_id / kick_user_id should be set, matching `source`.
    CONSTRAINT chk_claimant_matches_source CHECK (
        (source = 'discord_code' AND discord_user_id IS NOT NULL AND kick_user_id IS NULL) OR
        (source = 'kick_reward'  AND kick_user_id IS NOT NULL AND discord_user_id IS NULL)
    )
);

-- One-time-use enforcement at the DB level (paired with an app-level row lock on redeem).
-- NULL columns don't collide under a plain UNIQUE constraint in Postgres, so this is
-- split into two partial unique indexes rather than one composite UNIQUE constraint.

-- A given giveaway_codes.id can only ever produce one redemption.
CREATE UNIQUE INDEX IF NOT EXISTS uq_redemption_discord_code
    ON redemptions (source_ref_id)
    WHERE source = 'discord_code';

-- A given Kick redemption event can only ever produce one redemption row.
CREATE UNIQUE INDEX IF NOT EXISTS uq_redemption_kick
    ON redemptions (kick_redemption_id)
    WHERE source = 'kick_reward';

CREATE INDEX IF NOT EXISTS idx_redemptions_status ON redemptions(status);
CREATE INDEX IF NOT EXISTS idx_redemptions_source ON redemptions(source, source_ref_id);
