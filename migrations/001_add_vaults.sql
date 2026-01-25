-- Migration: 001_add_vaults.sql
-- Description: Add vaults and vault_memberships tables, migrate existing documents
-- 
-- This migration should be run on existing databases to add multi-vault support.
-- It is safe to run multiple times (uses IF NOT EXISTS / IF EXISTS checks).
--
-- Run with: psql -d your_database -f migrations/001_add_vaults.sql

BEGIN;

-- ============================================================================
-- STEP 1: Create new tables (safe if they already exist)
-- ============================================================================

CREATE TABLE IF NOT EXISTS vaults (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    is_personal BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_vaults_owner ON vaults(owner_id);

CREATE TABLE IF NOT EXISTS vault_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_id UUID NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'owner',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vault_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_vault_memberships_user ON vault_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_vault_memberships_vault ON vault_memberships(vault_id);

-- ============================================================================
-- STEP 2: Add new columns to documents (if they don't exist)
-- ============================================================================

-- Add vault_id column (nullable initially for migration)
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'documents' AND column_name = 'vault_id'
    ) THEN
        ALTER TABLE documents ADD COLUMN vault_id UUID;
        CREATE INDEX idx_documents_vault ON documents(vault_id);
    END IF;
END $$;

-- Add created_by column
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns 
        WHERE table_name = 'documents' AND column_name = 'created_by'
    ) THEN
        ALTER TABLE documents ADD COLUMN created_by UUID REFERENCES users(id) ON DELETE SET NULL;
        CREATE INDEX idx_documents_created_by ON documents(created_by);
    END IF;
END $$;

-- ============================================================================
-- STEP 3: Create personal vault for each user (if they don't have one)
-- ============================================================================

INSERT INTO vaults (id, name, owner_id, is_personal, created_at)
SELECT 
    gen_random_uuid(), 
    'Personal Vault', 
    u.id, 
    true, 
    now()
FROM users u
WHERE NOT EXISTS (
    SELECT 1 FROM vaults v WHERE v.owner_id = u.id AND v.is_personal = true
);

-- ============================================================================
-- STEP 4: Create owner memberships for personal vaults (if they don't exist)
-- ============================================================================

INSERT INTO vault_memberships (id, vault_id, user_id, role, created_at)
SELECT 
    gen_random_uuid(),
    v.id,
    v.owner_id,
    'owner',
    now()
FROM vaults v
WHERE v.owner_id IS NOT NULL
AND NOT EXISTS (
    SELECT 1 FROM vault_memberships vm 
    WHERE vm.vault_id = v.id AND vm.user_id = v.owner_id
);

-- ============================================================================
-- STEP 5: Assign orphan documents to the first user's personal vault
-- This handles existing documents that have no vault_id set.
-- If you have a specific user ID to assign documents to, replace this logic.
-- ============================================================================

-- First, try to assign documents to the first user's personal vault
UPDATE documents d
SET vault_id = (
    SELECT v.id 
    FROM vaults v 
    WHERE v.is_personal = true 
    ORDER BY v.created_at ASC 
    LIMIT 1
)
WHERE d.vault_id IS NULL;

-- ============================================================================
-- STEP 6: Add foreign key constraint (only after all documents have vault_id)
-- ============================================================================

-- Check if all documents have vault_id before adding constraint
DO $$
DECLARE
    orphan_count INTEGER;
BEGIN
    SELECT COUNT(*) INTO orphan_count FROM documents WHERE vault_id IS NULL;
    
    IF orphan_count > 0 THEN
        RAISE NOTICE 'WARNING: % documents still have NULL vault_id. Skipping NOT NULL constraint.', orphan_count;
    ELSE
        -- Make vault_id NOT NULL
        ALTER TABLE documents ALTER COLUMN vault_id SET NOT NULL;
        
        -- Add foreign key constraint if it doesn't exist
        IF NOT EXISTS (
            SELECT 1 FROM information_schema.table_constraints 
            WHERE constraint_name = 'fk_documents_vault' AND table_name = 'documents'
        ) THEN
            ALTER TABLE documents 
            ADD CONSTRAINT fk_documents_vault 
            FOREIGN KEY (vault_id) REFERENCES vaults(id) ON DELETE CASCADE;
        END IF;
        
        RAISE NOTICE 'Successfully made vault_id NOT NULL and added foreign key constraint.';
    END IF;
END $$;

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (run manually after migration)
-- ============================================================================

-- Check vault counts:
-- SELECT COUNT(*) as vault_count FROM vaults;

-- Check membership counts:
-- SELECT COUNT(*) as membership_count FROM vault_memberships;

-- Check documents per vault:
-- SELECT v.name, COUNT(d.id) as doc_count 
-- FROM vaults v 
-- LEFT JOIN documents d ON d.vault_id = v.id 
-- GROUP BY v.id, v.name;

-- Check for orphan documents:
-- SELECT COUNT(*) as orphan_docs FROM documents WHERE vault_id IS NULL;
