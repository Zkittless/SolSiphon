-- Migration 007: allow 'giveaway' as an audit_log entity_type
-- (Migration 005 already ran and locked in the old constraint, so this
-- widens it rather than editing the applied file.)

ALTER TABLE audit_log DROP CONSTRAINT audit_log_entity_type_check;

ALTER TABLE audit_log ADD CONSTRAINT audit_log_entity_type_check
    CHECK (entity_type IN ('code', 'kick_reward', 'redemption', 'payout', 'giveaway'));
