-- Migration: 016_fetch_tree_flat_compression_metadata.sql
-- Description: Include node metadata required for fetch-layer compression.

BEGIN;

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
          'node_type', n.node_type,
          'node_role', n.node_role,
          'locked', n.locked,
          'auto_generated', n.auto_generated,
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
