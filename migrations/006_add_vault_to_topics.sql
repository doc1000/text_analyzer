-- Migration: 006_add_vault_to_topics.sql
-- Description: Add vault_id column to all topic tables to enforce vault-level isolation
-- 
-- This migration:
-- 1. Adds vault_id column to all topic_* tables in the embedding schema
-- 2. Creates foreign key constraints to vaults(id) with CASCADE delete
-- 3. Creates indexes for efficient queries
-- 4. CLEARS all existing topics (as per plan - no migration of existing data)
-- 5. Makes vault_id NOT NULL
--
-- Run with: psql -d your_database -f migrations/006_add_vault_to_topics.sql

BEGIN;

-- ============================================================================
-- STEP 1: Add vault_id column to all topic tables
-- ============================================================================

DO $$
DECLARE
    table_rec RECORD;
    topic_table_name TEXT;
BEGIN
    -- Find all tables in embedding schema that start with 'topic_'
    FOR table_rec IN 
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'embedding' 
        AND table_name LIKE 'topic_%'
    LOOP
        topic_table_name := table_rec.table_name;
        
        RAISE NOTICE 'Processing table: embedding.%', topic_table_name;
        
        -- Check if vault_id column already exists
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.columns 
            WHERE table_schema = 'embedding' 
            AND table_name = topic_table_name 
            AND column_name = 'vault_id'
        ) THEN
            -- Add vault_id column (nullable initially)
            EXECUTE format('ALTER TABLE embedding.%I ADD COLUMN vault_id UUID', topic_table_name);
            RAISE NOTICE '  ✓ Added vault_id column to embedding.%', topic_table_name;
            
            -- Create index on vault_id
            EXECUTE format('CREATE INDEX idx_%I_vault_id ON embedding.%I(vault_id)', topic_table_name, topic_table_name);
            RAISE NOTICE '  ✓ Created index on vault_id';
            
            -- Create composite index on (vault_id, level_index) for efficient queries
            EXECUTE format('CREATE INDEX idx_%I_vault_level ON embedding.%I(vault_id, level_index)', topic_table_name, topic_table_name);
            RAISE NOTICE '  ✓ Created composite index on (vault_id, level_index)';
            
        ELSE
            RAISE NOTICE '  ⚠ vault_id column already exists in embedding.%', topic_table_name;
        END IF;
    END LOOP;
END $$;

-- ============================================================================
-- STEP 2: Clear all existing topics (as per plan - clean slate approach)
-- ============================================================================

DO $$
DECLARE
    table_rec RECORD;
    topic_table_name TEXT;
    deleted_count INTEGER;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE 'Clearing existing topics from all topic tables...';
    
    FOR table_rec IN 
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'embedding' 
        AND table_name LIKE 'topic_%'
    LOOP
        topic_table_name := table_rec.table_name;
        
        -- Delete all rows from topic table
        EXECUTE format('DELETE FROM embedding.%I', topic_table_name);
        
        -- Get count (should be 0 now)
        EXECUTE format('SELECT COUNT(*) FROM embedding.%I', topic_table_name) INTO deleted_count;
        
        RAISE NOTICE '  ✓ Cleared embedding.% (0 rows remaining)', topic_table_name;
    END LOOP;
    
    RAISE NOTICE 'All topic tables cleared successfully';
END $$;

-- ============================================================================
-- STEP 3: Add foreign key constraints and make vault_id NOT NULL
-- ============================================================================

DO $$
DECLARE
    table_rec RECORD;
    topic_table_name TEXT;
    fk_constraint_name TEXT;
BEGIN
    RAISE NOTICE '';
    RAISE NOTICE 'Adding foreign key constraints and NOT NULL...';
    
    FOR table_rec IN 
        SELECT table_name 
        FROM information_schema.tables 
        WHERE table_schema = 'embedding' 
        AND table_name LIKE 'topic_%'
    LOOP
        topic_table_name := table_rec.table_name;
        fk_constraint_name := 'fk_' || topic_table_name || '_vault';
        
        -- Make vault_id NOT NULL (safe now that table is empty)
        EXECUTE format('ALTER TABLE embedding.%I ALTER COLUMN vault_id SET NOT NULL', topic_table_name);
        RAISE NOTICE '  ✓ Made vault_id NOT NULL in embedding.%', topic_table_name;
        
        -- Add foreign key constraint if it doesn't exist
        IF NOT EXISTS (
            SELECT 1 
            FROM information_schema.table_constraints 
            WHERE constraint_schema = 'embedding'
            AND table_name = topic_table_name 
            AND constraint_name = fk_constraint_name
        ) THEN
            EXECUTE format(
                'ALTER TABLE embedding.%I ADD CONSTRAINT %I FOREIGN KEY (vault_id) REFERENCES vaults(id) ON DELETE CASCADE',
                topic_table_name,
                fk_constraint_name
            );
            RAISE NOTICE '  ✓ Added foreign key constraint to vaults(id) with CASCADE';
        ELSE
            RAISE NOTICE '  ⚠ Foreign key constraint already exists';
        END IF;
    END LOOP;
END $$;

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (run manually after migration)
-- ============================================================================

-- Check vault_id column exists in all topic tables:
-- SELECT table_name, column_name, is_nullable, data_type
-- FROM information_schema.columns
-- WHERE table_schema = 'embedding' AND table_name LIKE 'topic_%' AND column_name = 'vault_id';

-- Verify all topic tables are empty:
-- SELECT table_name, 
--        (SELECT COUNT(*) FROM embedding.topic_all_minilm_v1_384) as count
-- FROM information_schema.tables
-- WHERE table_schema = 'embedding' AND table_name LIKE 'topic_%';

-- Check indexes:
-- SELECT tablename, indexname 
-- FROM pg_indexes 
-- WHERE schemaname = 'embedding' AND tablename LIKE 'topic_%' 
-- ORDER BY tablename, indexname;

-- Check foreign key constraints:
-- SELECT conname, conrelid::regclass AS table_name, confrelid::regclass AS referenced_table
-- FROM pg_constraint
-- WHERE connamespace = 'embedding'::regnamespace 
-- AND contype = 'f' 
-- AND conrelid::regclass::text LIKE 'embedding.topic_%';
