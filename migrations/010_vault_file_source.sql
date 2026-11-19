-- Migration: 010_vault_file_source.sql
-- Description: the user's vault folder (e.g. ~/Vault/inbox) as a source into the personal vault (batch30).
--   documents.source_hash: sha256 of the file bytes at the last import (detects edits and moves)
--   documents.source_status: ok | missing (file moved or deleted outside the watched view; the VB
--   document and its text are kept - a soft broken link, never an erase)
--   source_tombstones: files whose VB document the user deleted, so the scan does not re-add them
--   (an edited file - different hash - comes back as new content)
--
-- Run with: psql -d your_database -f migrations/010_vault_file_source.sql

BEGIN;

ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_hash TEXT;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_status TEXT;
CREATE INDEX IF NOT EXISTS idx_documents_vault_source ON documents(vault_id, source_kind);

CREATE TABLE IF NOT EXISTS source_tombstones (
    vault_id    UUID NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
    source_kind TEXT NOT NULL,
    source_ref  TEXT NOT NULL,
    source_hash TEXT,
    deleted_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (vault_id, source_kind, source_ref)
);

COMMIT;
