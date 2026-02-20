-- Migration: 011_semantic_tree_anchors_staging.sql
-- Description: Add node_role, staging node, document anchors for semantic_tree_v2.
--
-- This migration:
-- 1. Extends tree_node with node_role (normal/staging)
-- 2. Adds unique partial index: one staging node per vault
-- 3. Creates document_anchor table for hard placement constraints
-- 4. Adds ensure_staging_node function
--
-- Run with: psql -d your_database -f migrations/011_semantic_tree_anchors_staging.sql

BEGIN;

-- ============================================================================
-- STEP 1: Extend semantic_tree_v2.tree_node with node_role
-- ============================================================================

ALTER TABLE semantic_tree_v2.tree_node
  ADD COLUMN IF NOT EXISTS node_role TEXT NOT NULL DEFAULT 'normal'
    CHECK (node_role IN ('normal', 'staging'));

COMMENT ON COLUMN semantic_tree_v2.tree_node.node_role IS 'normal: standard node. staging: default drop for unclassified docs, one per vault';

CREATE UNIQUE INDEX IF NOT EXISTS idx_one_staging_per_vault
ON semantic_tree_v2.tree_node(vault_id)
WHERE node_role = 'staging';

-- ============================================================================
-- STEP 2: Create document_anchor table
-- ============================================================================

CREATE TABLE IF NOT EXISTS semantic_tree_v2.document_anchor (
  vault_id        UUID NOT NULL,
  document_id     UUID NOT NULL,
  anchor_node_id  UUID NOT NULL,
  created_by      UUID NOT NULL,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (vault_id, document_id),

  CONSTRAINT fk_anchor_node
    FOREIGN KEY (anchor_node_id, vault_id)
    REFERENCES semantic_tree_v2.tree_node(id, vault_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_anchor_node_lookup
ON semantic_tree_v2.document_anchor(vault_id, anchor_node_id);

-- ============================================================================
-- STEP 3: ensure_staging_node - create staging node if none exists
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.ensure_staging_node(
  p_user_id UUID,
  p_vault_id UUID
) RETURNS UUID
LANGUAGE plpgsql
AS $$
DECLARE
  v_id UUID;
BEGIN
  IF NOT semantic_tree_v2.user_has_vault_role(p_user_id, p_vault_id, 'editor') THEN
    RAISE EXCEPTION 'insufficient_privilege';
  END IF;

  SELECT id INTO v_id
  FROM semantic_tree_v2.tree_node
  WHERE vault_id = p_vault_id AND node_role = 'staging'
  LIMIT 1;

  IF v_id IS NOT NULL THEN
    RETURN v_id;
  END IF;

  v_id := gen_random_uuid();
  INSERT INTO semantic_tree_v2.tree_node
    (id, vault_id, parent_id, title, summary, sort_key, created_by, node_type, node_role, auto_generated, size, locked)
  VALUES
    (v_id, p_vault_id, NULL, 'Staging', 'Unclassified documents', '', p_user_id, 'cluster', 'staging', TRUE, 0, FALSE);

  INSERT INTO semantic_tree_v2.tree_node_stats(node_id, vault_id)
  VALUES (v_id, p_vault_id)
  ON CONFLICT (node_id) DO NOTHING;

  RETURN v_id;
END $$;

COMMIT;
