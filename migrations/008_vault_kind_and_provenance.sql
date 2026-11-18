-- Migration: 008_vault_kind_and_provenance.sql
-- Description: vault kinds and document provenance (batch27, collections v1).
--   vaults.kind: personal (a user's second brain), collection (isolated read-only workspace,
--   e.g. an imported article set), shared (group vault; designed for, not used yet).
--   documents.source_*: where an item came from; promoted copies point at their original.
-- Design: VB_VAULTS_SOURCES_COLLECTIONS.md (workspace root).
--
-- Run with: psql -d your_database -f migrations/008_vault_kind_and_provenance.sql

BEGIN;

ALTER TABLE vaults ADD COLUMN IF NOT EXISTS kind TEXT;
UPDATE vaults SET kind = CASE WHEN is_personal THEN 'personal' ELSE 'shared' END WHERE kind IS NULL;
ALTER TABLE vaults ALTER COLUMN kind SET DEFAULT 'personal';
ALTER TABLE vaults ALTER COLUMN kind SET NOT NULL;
DO $$ BEGIN
  ALTER TABLE vaults ADD CONSTRAINT vaults_kind_check CHECK (kind IN ('personal', 'collection', 'shared'));
EXCEPTION WHEN duplicate_object THEN NULL; END $$;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_kind TEXT;          -- extension | import | promoted | email | vault_file | todo
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_ref TEXT;           -- file path, message id, todo id ...
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_vault_id UUID;      -- promoted copies: the original's vault
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_document_id UUID;   -- promoted copies: the original document
ALTER TABLE documents ADD COLUMN IF NOT EXISTS imported_at TIMESTAMPTZ;
UPDATE documents SET source_kind = 'extension' WHERE source_kind IS NULL;
ALTER TABLE documents ALTER COLUMN source_kind SET DEFAULT 'extension';
CREATE INDEX IF NOT EXISTS idx_documents_source_kind ON documents(source_kind);

COMMIT;
