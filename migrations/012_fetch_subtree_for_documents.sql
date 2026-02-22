-- Migration: 012_fetch_subtree_for_documents.sql
-- Description: Add fetch_subtree_for_documents for contextual subtree projection.
--
-- Returns minimal subtree covering given document_ids (doc-attached nodes + ancestors).
-- Read-only; no tree mutation.
--
-- Run with: psql -d your_database -f migrations/012_fetch_subtree_for_documents.sql

CREATE OR REPLACE FUNCTION semantic_tree_v2.fetch_subtree_for_documents(
    p_user_id UUID,
    p_vault_id UUID,
    p_document_ids UUID[]
) RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    result JSONB;
BEGIN
    -- Permission check
    IF NOT semantic_tree_v2.user_has_vault_role(p_user_id, p_vault_id, 'viewer') THEN
        RAISE EXCEPTION 'insufficient_privilege';
    END IF;

    WITH RECURSIVE doc_nodes AS (
        SELECT DISTINCT nd.node_id, nd.vault_id
        FROM semantic_tree_v2.node_document nd
        WHERE nd.vault_id = p_vault_id
          AND nd.document_id = ANY(p_document_ids)
    ),
    ancestors AS (
        SELECT tn.id, tn.parent_id, tn.vault_id, tn.title, tn.node_type, tn.locked
        FROM semantic_tree_v2.tree_node tn
        JOIN doc_nodes dn
          ON tn.id = dn.node_id

        UNION ALL

        SELECT parent.id, parent.parent_id, parent.vault_id, parent.title, parent.node_type, parent.locked
        FROM semantic_tree_v2.tree_node parent
        JOIN ancestors child
          ON child.parent_id = parent.id
         AND parent.vault_id = p_vault_id
    ),
    deduped AS (
        SELECT DISTINCT ON (id) id, parent_id, title, node_type, locked
        FROM ancestors
    )
    SELECT jsonb_agg(
        jsonb_build_object(
            'id', d.id,
            'parent_id', d.parent_id,
            'title', d.title,
            'node_type', d.node_type,
            'locked', COALESCE(d.locked, FALSE),
            'doc_count', (
                SELECT COUNT(*)::int
                FROM semantic_tree_v2.node_document nd2
                WHERE nd2.node_id = d.id
                  AND nd2.vault_id = p_vault_id
                  AND nd2.document_id = ANY(p_document_ids)
            )
        )
    )
    INTO result
    FROM deduped d;

    RETURN COALESCE(result, '[]'::jsonb);
END $$;
