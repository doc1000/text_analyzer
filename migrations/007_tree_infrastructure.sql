-- Migration: 007_tree_infrastructure.sql
-- Description: SQL-driven tree structure for topic modelling
--
-- This migration:
-- 1. Creates tree_nodes table (clusters scoped by vault)
-- 2. Creates node_documents junction table
-- 3. Adds reduced_embedding column to documents (vector(30), nullable)
-- 4. Creates indexes including ivfflat for centroid similarity
-- 5. Defines SQL functions: attach_document_to_node, recompute_node_centroids
--
-- Run with: psql -d your_database -f migrations/007_tree_infrastructure.sql
--
-- Or pipe from host into Docker:
--   docker compose exec -T db psql -U badger -d badgerdb < migrations/007_tree_infrastructure.sql

BEGIN;

-- ============================================================================
-- STEP 1: Enable pgvector extension
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- STEP 2: Create tree_nodes table
-- ============================================================================

CREATE TABLE IF NOT EXISTS tree_nodes (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_id UUID NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
    parent_id UUID REFERENCES tree_nodes(id) ON DELETE CASCADE,
    name TEXT,
    node_type TEXT NOT NULL DEFAULT 'cluster' CHECK (node_type IN ('cluster')),
    distance DOUBLE PRECISION,
    size INTEGER NOT NULL DEFAULT 0,
    centroid vector(30),
    locked BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tree_nodes_vault ON tree_nodes(vault_id);
CREATE INDEX IF NOT EXISTS idx_tree_nodes_parent ON tree_nodes(parent_id);

-- IVFFlat index for centroid similarity search (cosine distance)
CREATE INDEX IF NOT EXISTS idx_tree_nodes_centroid
ON tree_nodes
USING ivfflat (centroid vector_cosine_ops)
WITH (lists = 100);

-- ============================================================================
-- STEP 3: Create node_documents junction table
-- ============================================================================

CREATE TABLE IF NOT EXISTS node_documents (
    node_id UUID NOT NULL REFERENCES tree_nodes(id) ON DELETE CASCADE,
    document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    PRIMARY KEY (node_id, document_id)
);

CREATE INDEX IF NOT EXISTS idx_node_documents_node ON node_documents(node_id);
CREATE INDEX IF NOT EXISTS idx_node_documents_document ON node_documents(document_id);

-- ============================================================================
-- STEP 4: Add reduced_embedding column to documents
-- ============================================================================

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM information_schema.columns
        WHERE table_name = 'documents' AND column_name = 'reduced_embedding'
    ) THEN
        ALTER TABLE documents ADD COLUMN reduced_embedding vector(30);
        RAISE NOTICE 'Added reduced_embedding column to documents';
    ELSE
        RAISE NOTICE 'reduced_embedding column already exists in documents';
    END IF;
END $$;

-- ============================================================================
-- STEP 5: Create attach_document_to_node function
-- ============================================================================

CREATE OR REPLACE FUNCTION attach_document_to_node(
    p_node UUID,
    p_document UUID,
    p_user UUID
)
RETURNS VOID AS $$
DECLARE
    node_vault UUID;
    doc_vault UUID;
BEGIN
    SELECT vault_id INTO node_vault
    FROM tree_nodes WHERE id = p_node;

    SELECT vault_id INTO doc_vault
    FROM documents WHERE id = p_document;

    IF node_vault IS NULL OR doc_vault IS NULL THEN
        RAISE EXCEPTION 'Node or document not found';
    END IF;

    IF node_vault != doc_vault THEN
        RAISE EXCEPTION 'Vault mismatch';
    END IF;

    IF NOT EXISTS (
        SELECT 1 FROM vault_memberships
        WHERE vault_id = node_vault
        AND user_id = p_user
        AND role IN ('owner', 'admin', 'editor')
    ) THEN
        RAISE EXCEPTION 'Insufficient permissions';
    END IF;

    INSERT INTO node_documents (node_id, document_id)
    VALUES (p_node, p_document)
    ON CONFLICT (node_id, document_id) DO NOTHING;

END;
$$ LANGUAGE plpgsql;

-- ============================================================================
-- STEP 6: Create recompute_node_centroids function
-- ============================================================================

CREATE OR REPLACE FUNCTION recompute_node_centroids(p_vault UUID)
RETURNS VOID AS $$
BEGIN
    UPDATE tree_nodes n
    SET centroid = sub.avg_embedding,
        size = sub.doc_count
    FROM (
        SELECT nd.node_id,
               AVG(d.reduced_embedding) AS avg_embedding,
               COUNT(*)::INTEGER AS doc_count
        FROM node_documents nd
        JOIN documents d ON d.id = nd.document_id
        WHERE d.vault_id = p_vault
        AND d.reduced_embedding IS NOT NULL
        GROUP BY nd.node_id
    ) sub
    WHERE n.id = sub.node_id
    AND n.vault_id = p_vault;
END;
$$ LANGUAGE plpgsql;

COMMIT;

-- ============================================================================
-- VERIFICATION QUERIES (run manually after migration)
-- ============================================================================

-- Check tree_nodes:
-- SELECT * FROM tree_nodes LIMIT 5;

-- Check node_documents:
-- SELECT * FROM node_documents LIMIT 5;

-- Check documents.reduced_embedding:
-- SELECT id, vault_id, reduced_embedding IS NOT NULL as has_reduced FROM documents LIMIT 5;
