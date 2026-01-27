-- Migration: 004_verification_code_purpose.sql
-- Description: Add purpose field to verification codes for reviewer/tester support
--
-- This migration adds a 'purpose' column to distinguish:
-- - 'user': Normal codes (10 min, single use) - default
-- - 'reviewer': Long-lived codes (2 weeks, multi-use) for extension reviewers/testers
--
-- Run with: psql -d your_database -f migrations/004_verification_code_purpose.sql

BEGIN;

-- Add purpose column: 'user' (default) or 'reviewer'
ALTER TABLE extension_verification_codes 
ADD COLUMN IF NOT EXISTS purpose TEXT NOT NULL DEFAULT 'user';

-- Add index for querying by purpose
CREATE INDEX IF NOT EXISTS idx_verification_codes_purpose 
ON extension_verification_codes(purpose);

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (run manually after migration)
-- ============================================================================

-- Check migration status:
-- SELECT purpose, COUNT(*) FROM extension_verification_codes GROUP BY purpose;

-- View reviewer codes:
-- SELECT email, code, purpose, created_at, expires_at, used_at 
-- FROM extension_verification_codes 
-- WHERE purpose = 'reviewer';
