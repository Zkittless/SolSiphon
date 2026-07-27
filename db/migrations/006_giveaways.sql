-- Migration 006: giveaways + giveaway_entrants
-- A "giveaway" is a timed event with a button-entry embed. When it ends,
-- one entrant is picked at random and a giveaway_codes row is generated
-- for them -- so this feeds into the exact same redemption pipeline as
-- a manually-created code. Winners still redeem via /redeem like anyone else.

CREATE TABLE IF NOT EXISTS giveaways (
    id              BIGSERIAL PRIMARY KEY,
    amount_usd      NUMERIC(12, 2) NOT NULL CHECK (amount_usd > 0),
    created_by      TEXT NOT NULL,               -- Discord user ID of streamer/mod
    channel_id      TEXT NOT NULL,
    message_id      TEXT,                         -- set once the embed is posted
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    ends_at         TIMESTAMPTZ NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                        CHECK (status IN ('open', 'ended', 'cancelled')),
    winner_user_id  TEXT,
    winning_code_id BIGINT REFERENCES giveaway_codes(id),
    ended_at        TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_giveaways_status ON giveaways(status);

CREATE TABLE IF NOT EXISTS giveaway_entrants (
    id              BIGSERIAL PRIMARY KEY,
    giveaway_id     BIGINT NOT NULL REFERENCES giveaways(id),
    discord_user_id TEXT NOT NULL,
    entered_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

    -- One entry per user per giveaway.
    CONSTRAINT uq_giveaway_entrant UNIQUE (giveaway_id, discord_user_id)
);

CREATE INDEX IF NOT EXISTS idx_giveaway_entrants_giveaway ON giveaway_entrants(giveaway_id);
