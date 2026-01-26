-- Migration: 003_separate_captured_extracted.sql
-- Description: Separate captured text from extracted text
-- 
-- This migration adds separate columns for:
-- - captured_text: What the extension captured (displayed to user)
-- - extracted_text: Trafilatura output (for NLP/embeddings, optional)
--
-- Run with: psql -d your_database -f migrations/003_separate_captured_extracted.sql

BEGIN;

-- ============================================================================
-- STEP 1: Add new columns
-- ============================================================================

-- captured_text: What the extension sent (for display)
ALTER TABLE documents ADD COLUMN IF NOT EXISTS captured_text TEXT;

-- extracted_text: Trafilatura extraction (for NLP, nullable)
ALTER TABLE documents ADD COLUMN IF NOT EXISTS extracted_text TEXT;

-- ============================================================================
-- STEP 2: Migrate existing data
-- Copy full_text to captured_text for existing documents
-- ============================================================================

UPDATE documents 
SET captured_text = full_text 
WHERE captured_text IS NULL AND full_text IS NOT NULL;

-- ============================================================================
-- STEP 3: Make captured_text NOT NULL after migration
-- Only if all rows have been migrated
-- ============================================================================

DO $$
DECLARE
    null_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO null_count FROM documents WHERE captured_text IS NULL;
    
    IF null_count > 0 THEN
        RAISE NOTICE 'WARNING: % documents still have NULL captured_text. Skipping NOT NULL constraint.', null_count;
    ELSE
        -- All documents have captured_text, safe to add constraint
        ALTER TABLE documents ALTER COLUMN captured_text SET NOT NULL;
        RAISE NOTICE 'Successfully made captured_text NOT NULL.';
    END IF;
END $$;

COMMIT;

-- ============================================================================
-- NOTE: full_text column is kept for backward compatibility
-- After verifying the migration works, you can drop it with:
-- ALTER TABLE documents DROP COLUMN full_text;
-- ============================================================================

-- ============================================================================
-- VERIFICATION QUERIES (run manually after migration)
-- ============================================================================

-- Check migration status:
-- SELECT COUNT(*) as total, 
--        COUNT(captured_text) as has_captured, 
--        COUNT(extracted_text) as has_extracted,
--        COUNT(full_text) as has_full_text
-- FROM documents;

-- Sample comparison:
-- SELECT id, 
--        LEFT(captured_text, 100) as captured_preview,
--        LEFT(extracted_text, 100) as extracted_preview
-- FROM documents LIMIT 5;
