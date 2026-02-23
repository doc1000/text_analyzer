-- Migration: 017_cluster_unique_attach_document.sql
-- Description: Enforce per-vault document uniqueness across cluster nodes.
--
-- Policy:
-- - If attaching to a cluster node, remove existing cluster-node attachments
--   for the same (vault_id, document_id) before inserting.
-- - Manual/pinned attachments may coexist and are not removed.

BEGIN;

CREATE OR REPLACE FUNCTION semantic_tree_v2.attach_document(
  p_user_id UUID,
  p_vault_id UUID,
  p_node_id UUID,
  p_document_id UUID
) RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
  v_target_node_type TEXT;
BEGIN
  IF NOT semantic_tree_v2.user_has_vault_role(p_user_id, p_vault_id, 'editor') THEN
    RAISE EXCEPTION 'insufficient_privilege';
  END IF;

  SELECT node_type
  INTO v_target_node_type
  FROM semantic_tree_v2.tree_node
  WHERE id = p_node_id
    AND vault_id = p_vault_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'node_not_found';
  END IF;

  -- Cluster-only uniqueness: a doc can belong to at most one cluster node per vault.
  IF v_target_node_type = 'cluster' THEN
    WITH removed AS (
      DELETE FROM semantic_tree_v2.node_document nd
      USING semantic_tree_v2.tree_node tn
      WHERE nd.vault_id = p_vault_id
        AND nd.document_id = p_document_id
        AND nd.node_id = tn.id
        AND tn.vault_id = p_vault_id
        AND tn.node_type = 'cluster'
        AND nd.node_id <> p_node_id
      RETURNING nd.node_id
    )
    UPDATE semantic_tree_v2.tree_node_stats s
    SET doc_count = GREATEST(0, s.doc_count - 1),
        updated_at = now()
    WHERE s.vault_id = p_vault_id
      AND s.node_id IN (SELECT node_id FROM removed);
  END IF;

  WITH inserted AS (
    INSERT INTO semantic_tree_v2.node_document
      (vault_id, node_id, document_id, created_by)
    VALUES
      (p_vault_id, p_node_id, p_document_id, p_user_id)
    ON CONFLICT DO NOTHING
    RETURNING node_id
  )
  UPDATE semantic_tree_v2.tree_node_stats
  SET doc_count = doc_count + 1,
      updated_at = now()
  WHERE node_id IN (SELECT node_id FROM inserted)
    AND vault_id = p_vault_id;
END $$;

COMMIT;
