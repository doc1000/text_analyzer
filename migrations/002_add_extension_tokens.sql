-- Migration: 002_add_extension_tokens.sql
-- Description: Add extension_tokens and extension_verification_codes tables for OAuth-style auth
-- 
-- This migration adds support for OAuth-style extension authentication where users
-- verify their email and receive a token that the extension captures automatically.
--
-- Run with: psql -d your_database -f migrations/002_add_extension_tokens.sql

BEGIN;

-- ============================================================================
-- STEP 1: Create extension_tokens table
-- ============================================================================

CREATE TABLE IF NOT EXISTS extension_tokens (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    -- SHA256 hash of (pepper + raw_token). Never store raw tokens.
    token_hash VARCHAR(64) NOT NULL UNIQUE,
    -- Device/browser name for display, e.g., "Chrome on Doster-PC"
    name TEXT,
    -- Permission scopes (default: ingest only)
    scopes TEXT[] DEFAULT ARRAY['ingest'],
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_extension_tokens_user_id ON extension_tokens(user_id);
CREATE INDEX IF NOT EXISTS idx_extension_tokens_token_hash ON extension_tokens(token_hash);

-- ============================================================================
-- STEP 2: Create extension_verification_codes table
-- ============================================================================

CREATE TABLE IF NOT EXISTS extension_verification_codes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL,
    -- 6-digit verification code
    code VARCHAR(6) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    -- Code expires after 10 minutes
    expires_at TIMESTAMPTZ NOT NULL,
    -- Set when code is used (prevents reuse)
    used_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_verification_codes_email ON extension_verification_codes(email);
CREATE INDEX IF NOT EXISTS idx_verification_codes_code ON extension_verification_codes(code);

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (run manually after migration)
-- ============================================================================

-- Check table creation:
-- SELECT table_name FROM information_schema.tables WHERE table_name IN ('extension_tokens', 'extension_verification_codes');

-- Check extension_tokens structure:
-- \d extension_tokens

-- Check extension_verification_codes structure:
-- \d extension_verification_codes
