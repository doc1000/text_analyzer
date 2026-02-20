-- Migration: 010_node_document_document_fk.sql
-- Add FK from node_document.document_id to documents.id ON DELETE CASCADE
-- so that when a document is deleted, its node_document links are removed.

BEGIN;

-- Remove orphan node_document rows (documents no longer exist)
DELETE FROM semantic_tree_v2.node_document
WHERE document_id NOT IN (SELECT id FROM documents);

ALTER TABLE semantic_tree_v2.node_document
  ADD CONSTRAINT fk_node_document_document
  FOREIGN KEY (document_id)
  REFERENCES documents(id)
  ON DELETE CASCADE;

COMMIT;
