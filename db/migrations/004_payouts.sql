-- Migration 004: payouts
-- One row per payout attempt, tied 1:1 to a redemption.
-- Squads/split-transfer fields are defined now so the schema doesn't need
-- reshaping in Phase 2 -- they stay unused/null until wallet integration lands.

CREATE TABLE IF NOT EXISTS payouts (
    id                      BIGSERIAL PRIMARY KEY,
    redemption_id           BIGINT NOT NULL UNIQUE REFERENCES redemptions(id),
    amount_usd              NUMERIC(12, 2) NOT NULL CHECK (amount_usd > 0),
    amount_sol              NUMERIC(20, 9),          -- set at proposal time
    sol_usd_rate            NUMERIC(12, 4),          -- rate used for the conversion, for audit
    status                  TEXT NOT NULL DEFAULT 'pending'
                                CHECK (status IN (
                                    'pending', 'proposed', 'approved',
                                    'executed', 'failed', 'cancelled'
                                )),

    -- Split (test + remainder) transfer tracking
    split_type              TEXT NOT NULL DEFAULT 'single'
                                CHECK (split_type IN ('single', 'test_and_remainder')),
    test_amount_usd         NUMERIC(12, 2),
    test_tx_status          TEXT CHECK (test_tx_status IN ('proposed', 'approved', 'executed')),
    test_tx_hash            TEXT,
    remainder_tx_status     TEXT CHECK (remainder_tx_status IN
                                ('proposed', 'approved', 'held', 'released', 'executed')),
    remainder_tx_hash       TEXT,
    receipt_confirmed_at    TIMESTAMPTZ,
    receipt_confirmed_by    TEXT,                    -- must match redemption claimant

    -- Squads proposal tracking
    squads_proposal_id      TEXT,
    proposer                TEXT,
    approver_1              TEXT,
    approver_2              TEXT,

    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    approved_at             TIMESTAMPTZ,
    executed_at             TIMESTAMPTZ,
    failure_reason          TEXT
);

CREATE INDEX IF NOT EXISTS idx_payouts_status ON payouts(status);
