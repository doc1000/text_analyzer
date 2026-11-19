-- Migration: 012_document_source_meta.sql
-- Description: parsed source fields per document (batch31): for todo items the todo.txt fields
--   (status, priority, projects, contexts, created, completed, due, extra key:values, file, line).
--
-- Run with: psql -d your_database -f migrations/012_document_source_meta.sql

BEGIN;
ALTER TABLE documents ADD COLUMN IF NOT EXISTS source_meta JSONB;
COMMIT;
