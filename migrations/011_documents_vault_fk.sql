-- Migration: 011_documents_vault_fk.sql
-- Description: documents.vault_id had no foreign key in the live schema, so deleting a vault left its
--   documents behind as invisible orphans (52 found on 2026-11-19: February e2e test notes and
--   onboarding-seed copies from deleted test vaults; backup badgerdb_pre_batch30_20261119.dump).
--   Remove orphans, then add the key with ON DELETE CASCADE (as models.Document declares).
--
-- Run with: psql -d your_database -f migrations/011_documents_vault_fk.sql

BEGIN;

DELETE FROM documents d WHERE d.vault_id IS NOT NULL
  AND NOT EXISTS (SELECT 1 FROM vaults v WHERE v.id = d.vault_id);

DO $$ BEGIN
  ALTER TABLE documents ADD CONSTRAINT documents_vault_id_fkey
    FOREIGN KEY (vault_id) REFERENCES vaults(id) ON DELETE CASCADE;
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

COMMIT;
