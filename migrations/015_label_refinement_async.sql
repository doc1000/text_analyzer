-- Migration: 015_label_refinement_async.sql
-- Description: Allow LLM refinement updates to finalize labels without
-- mutating signal signature semantics.

BEGIN;

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
  v_requested_source TEXT;
BEGIN
  SELECT locked, title_source, label_signature_hash
  INTO v_locked, v_title_source, v_existing_hash
  FROM semantic_tree_v2.tree_node
  WHERE vault_id = p_vault_id
    AND id = p_node_id;

  IF NOT FOUND THEN
    RETURN;
  END IF;

  IF v_locked OR v_title_source IN ('pinned', 'manual') THEN
    RETURN;
  END IF;

  v_requested_source := CASE
    WHEN p_title_source = 'llm' THEN 'llm'
    ELSE 'auto'
  END;

  -- Keep signature no-op optimization for deterministic updates.
  -- LLM finalization can legitimately keep the same signal signature while
  -- changing title_source/status/title.
  IF v_requested_source = 'auto'
     AND v_existing_hash IS NOT DISTINCT FROM p_signature_hash THEN
    RETURN;
  END IF;

  UPDATE semantic_tree_v2.tree_node
  SET title = p_label,
      title_evidence = p_signals,
      label_signals = p_signals,
      label_signature_hash = p_signature_hash,
      label_status = p_status,
      title_source = v_requested_source,
      updated_at = now()
  WHERE vault_id = p_vault_id
    AND id = p_node_id;
END;
$$;

COMMIT;
