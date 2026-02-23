-- Migration: 014_hybrid_node_labeling.sql
-- Description: Hybrid node labeling schema + guarded update function.
--
-- Adds:
-- 1. label_signals, label_signature_hash, label_status to semantic_tree_v2.tree_node
-- 2. update_node_label function for atomic, guarded node label updates
-- 3. Extended fetch_tree_flat to return new label fields

BEGIN;

-- ============================================================================
-- STEP 1: Extend semantic_tree_v2.tree_node label metadata
-- ============================================================================

ALTER TABLE semantic_tree_v2.tree_node
  ADD COLUMN IF NOT EXISTS label_signals JSONB,
  ADD COLUMN IF NOT EXISTS label_signature_hash TEXT,
  ADD COLUMN IF NOT EXISTS label_status TEXT NOT NULL DEFAULT 'auto';

-- Ensure title_source supports llm (auto|pinned|manual|llm)
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'tree_node_title_source_check'
      AND conrelid = 'semantic_tree_v2.tree_node'::regclass
  ) THEN
    ALTER TABLE semantic_tree_v2.tree_node
      DROP CONSTRAINT tree_node_title_source_check;
  END IF;

  ALTER TABLE semantic_tree_v2.tree_node
    ADD CONSTRAINT tree_node_title_source_check
    CHECK (title_source IN ('auto', 'pinned', 'manual', 'llm'));
END $$;

-- Add/refresh label_status check constraint
DO $$
BEGIN
  IF EXISTS (
    SELECT 1
    FROM pg_constraint
    WHERE conname = 'tree_node_label_status_check'
      AND conrelid = 'semantic_tree_v2.tree_node'::regclass
  ) THEN
    ALTER TABLE semantic_tree_v2.tree_node
      DROP CONSTRAINT tree_node_label_status_check;
  END IF;

  ALTER TABLE semantic_tree_v2.tree_node
    ADD CONSTRAINT tree_node_label_status_check
    CHECK (label_status IN ('auto', 'needs_llm', 'final', 'manual'));
END $$;

COMMENT ON COLUMN semantic_tree_v2.tree_node.title_source IS 'auto: deterministic relabel. pinned/manual: user protected. llm: async refined title.';
COMMENT ON COLUMN semantic_tree_v2.tree_node.label_signals IS 'Content and tag signals used for deterministic/LLM labeling.';
COMMENT ON COLUMN semantic_tree_v2.tree_node.label_signature_hash IS 'Stable hash of normalized label signals. Used to skip unchanged updates.';
COMMENT ON COLUMN semantic_tree_v2.tree_node.label_status IS 'auto -> needs_llm -> final. manual and locked are terminal/protected states.';

-- ============================================================================
-- STEP 2: Guarded atomic label updater
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.update_node_label(
  p_vault_id UUID,
  p_node_id UUID,
  p_label TEXT,
  p_signals JSONB,
  p_signature_hash TEXT,
  p_status TEXT,
  p_title_source TEXT DEFAULT NULL
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
  v_locked BOOLEAN;
  v_title_source TEXT;
  v_existing_hash TEXT;
BEGIN
  SELECT locked, title_source, label_signature_hash
  INTO v_locked, v_title_source, v_existing_hash
  FROM semantic_tree_v2.tree_node
  WHERE vault_id = p_vault_id
    AND id = p_node_id;

  IF NOT FOUND THEN
    RETURN;
  END IF;

  -- Protected states: never mutate.
  IF v_locked OR v_title_source IN ('pinned', 'manual') THEN
    RETURN;
  END IF;

  -- Skip unnecessary writes when signature is unchanged.
  IF v_existing_hash IS NOT DISTINCT FROM p_signature_hash THEN
    RETURN;
  END IF;

  UPDATE semantic_tree_v2.tree_node
  SET title = p_label,
      title_evidence = p_signals,
      label_signals = p_signals,
      label_signature_hash = p_signature_hash,
      label_status = p_status,
      title_source = CASE
        WHEN p_title_source IS NULL THEN 'auto'
        WHEN p_title_source = 'llm' THEN 'llm'
        ELSE 'auto'
      END,
      updated_at = now()
  WHERE vault_id = p_vault_id
    AND id = p_node_id;
END;
$$;

-- ============================================================================
-- STEP 3: Extend fetch_tree_flat payload
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.fetch_tree_flat(
  p_user_id UUID,
  p_vault_id UUID
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
  result JSONB;
BEGIN
  IF NOT semantic_tree_v2.user_has_vault_role(p_user_id, p_vault_id, 'viewer') THEN
    RAISE EXCEPTION 'insufficient_privilege';
  END IF;

  SELECT jsonb_build_object(
    'vault_id', p_vault_id,
    'nodes', COALESCE(
      jsonb_agg(
        jsonb_build_object(
          'id', n.id,
          'parent_id', n.parent_id,
          'title', n.title,
          'summary', n.summary,
          'sort_key', n.sort_key,
          'doc_count', COALESCE(s.doc_count, 0),
          'updated_at', n.updated_at,
          'title_source', COALESCE(n.title_source, 'auto'),
          'title_evidence', n.title_evidence,
          'label_signals', n.label_signals,
          'label_signature_hash', n.label_signature_hash,
          'label_status', COALESCE(n.label_status, 'auto')
        )
        ORDER BY n.sort_key, n.created_at
      ),
      '[]'::jsonb
    )
  )
  INTO result
  FROM semantic_tree_v2.tree_node n
  LEFT JOIN semantic_tree_v2.tree_node_stats s
    ON s.node_id = n.id
   AND s.vault_id = n.vault_id
  WHERE n.vault_id = p_vault_id;

  RETURN result;
END $$;

COMMIT;
