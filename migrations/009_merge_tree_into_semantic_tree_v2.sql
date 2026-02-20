-- Migration: 009_merge_tree_into_semantic_tree_v2.sql
-- Description: Merge 007 clustering tree into semantic_tree_v2. Single canonical tree per vault.
--
-- This migration:
-- 1. Extends semantic_tree_v2.tree_node with clustering fields
-- 2. Adds recompute_node_centroids for semantic_tree_v2
-- 3. Adds create_cluster_node for clustering flow
-- 4. Syncs vault_memberships -> vault_user_role (so existing vaults work)
-- 5. Drops public.tree_nodes and public.node_documents (007 structures)
--
-- Run with: psql -d your_database -f migrations/009_merge_tree_into_semantic_tree_v2.sql
--
-- Or pipe from host into Docker:
--   docker compose exec -T db psql -U badger -d badgerdb < migrations/009_merge_tree_into_semantic_tree_v2.sql

BEGIN;

-- ============================================================================
-- STEP 1: Ensure pgvector extension
-- ============================================================================

CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- STEP 2: Extend semantic_tree_v2.tree_node with clustering fields
-- ============================================================================

ALTER TABLE semantic_tree_v2.tree_node
  ADD COLUMN IF NOT EXISTS node_type TEXT NOT NULL DEFAULT 'manual'
    CHECK (node_type IN ('manual', 'cluster')),
  ADD COLUMN IF NOT EXISTS centroid vector(30) NULL,
  ADD COLUMN IF NOT EXISTS size INTEGER NOT NULL DEFAULT 0,
  ADD COLUMN IF NOT EXISTS distance_from_parent FLOAT NULL,
  ADD COLUMN IF NOT EXISTS auto_generated BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS locked BOOLEAN NOT NULL DEFAULT FALSE;

COMMENT ON COLUMN semantic_tree_v2.tree_node.node_type IS 'manual: user-created. cluster: from clustering, modifiable only when locked=FALSE';
COMMENT ON COLUMN semantic_tree_v2.tree_node.locked IS 'When TRUE, clustering routines must never modify this node';

CREATE INDEX IF NOT EXISTS idx_tree_node_centroid
ON semantic_tree_v2.tree_node
USING ivfflat (centroid vector_cosine_ops)
WITH (lists = 100)
WHERE centroid IS NOT NULL;

-- ============================================================================
-- STEP 3: Sync vault_memberships -> vault_user_role
-- ============================================================================

INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role)
SELECT vm.vault_id, vm.user_id,
  CASE
    WHEN vm.role = 'owner' THEN 'owner'::semantic_tree_v2.vault_role
    WHEN vm.role = 'admin' THEN 'admin'::semantic_tree_v2.vault_role
    WHEN vm.role = 'editor' THEN 'editor'::semantic_tree_v2.vault_role
    WHEN vm.role = 'viewer' THEN 'viewer'::semantic_tree_v2.vault_role
    ELSE 'viewer'::semantic_tree_v2.vault_role
  END
FROM vault_memberships vm
WHERE NOT EXISTS (
  SELECT 1 FROM semantic_tree_v2.vault_user_role vur
  WHERE vur.vault_id = vm.vault_id AND vur.user_id = vm.user_id
);

-- ============================================================================
-- STEP 4: recompute_node_centroids for semantic_tree_v2
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.recompute_node_centroids(p_vault_id UUID)
RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  UPDATE semantic_tree_v2.tree_node n
  SET centroid = sub.avg_embedding,
      size = sub.doc_count
  FROM (
    SELECT nd.node_id,
           AVG(d.reduced_embedding) AS avg_embedding,
           COUNT(*)::INTEGER AS doc_count
    FROM semantic_tree_v2.node_document nd
    JOIN documents d ON d.id = nd.document_id
    WHERE d.vault_id = p_vault_id
    AND d.reduced_embedding IS NOT NULL
    GROUP BY nd.node_id
  ) sub
  WHERE n.id = sub.node_id
  AND n.vault_id = p_vault_id
  AND n.node_type = 'cluster'
  AND n.locked = FALSE;
END;
$$;

-- ============================================================================
-- STEP 5: create_cluster_node for clustering flow
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.create_cluster_node(
  p_user_id UUID,
  p_vault_id UUID,
  p_parent_id UUID
) RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
  v_id UUID := gen_random_uuid();
BEGIN
  IF NOT semantic_tree_v2.user_has_vault_role(p_user_id, p_vault_id, 'editor') THEN
    RAISE EXCEPTION 'insufficient_privilege';
  END IF;

  INSERT INTO semantic_tree_v2.tree_node
    (id, vault_id, parent_id, title, summary, sort_key, created_by, node_type, auto_generated, size, locked)
  VALUES
    (v_id, p_vault_id, p_parent_id, 'Cluster', NULL, '', p_user_id, 'cluster', TRUE, 0, FALSE);

  INSERT INTO semantic_tree_v2.tree_node_stats(node_id, vault_id)
  VALUES (v_id, p_vault_id)
  ON CONFLICT (node_id) DO NOTHING;

  RETURN v_id;
END $$;

-- ============================================================================
-- STEP 6: Drop 007 structures and their functions
-- ============================================================================

DROP FUNCTION IF EXISTS attach_document_to_node(UUID, UUID, UUID);
DROP FUNCTION IF EXISTS recompute_node_centroids(UUID);
DROP TABLE IF EXISTS node_documents;
DROP TABLE IF EXISTS tree_nodes;

COMMIT;
