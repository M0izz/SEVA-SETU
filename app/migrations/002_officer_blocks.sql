-- Migration 002: Introduce trusted block-level officer authorization table
-- Binds officer identities strictly to authorized administrative blocks.

CREATE TABLE IF NOT EXISTS officer_blocks (
    username    VARCHAR(50) PRIMARY KEY,
    block       VARCHAR(50) NOT NULL,
    full_name   VARCHAR(100),
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- Seed initial trusted assignments for pilot blocks
INSERT INTO officer_blocks (username, block, full_name)
VALUES
    ('admin', 'Sonari', 'Administrator (Sonari Block)'),
    ('officer_sonari', 'Sonari', 'Block Development Officer (Sonari)'),
    ('officer_namti', 'Namti', 'Block Development Officer (Namti)')
ON CONFLICT (username) DO UPDATE SET block = EXCLUDED.block;
