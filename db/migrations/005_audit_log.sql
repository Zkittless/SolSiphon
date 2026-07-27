-- Migration 005: audit_log
-- Append-only. Every meaningful state change writes here.
-- No UPDATE/DELETE path is exposed to the app layer for this table.

CREATE TABLE IF NOT EXISTS audit_log (
    id              BIGSERIAL PRIMARY KEY,
    entity_type     TEXT NOT NULL CHECK (entity_type IN
                        ('code', 'kick_reward', 'redemption', 'payout')),
    entity_id       BIGINT NOT NULL,
    action          TEXT NOT NULL,               -- created/redeemed/proposed/approved/executed/failed/rejected/voided
    actor           TEXT NOT NULL,                -- Discord ID, Kick user ID, or 'system'
    metadata        JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_audit_log_entity ON audit_log(entity_type, entity_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at);

-- Revoke update/delete at the DB role level once you've set up a dedicated
-- app role (not the migration/owner role) -- e.g.:
--   REVOKE UPDATE, DELETE ON audit_log FROM app_role;
