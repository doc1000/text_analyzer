-- Migration: 013_label_sem_tree.sql
-- Description: Tag-based semantic labeling for semantic_tree_v2.
--
-- Adds:
-- 1. user_tags to public.documents (GIN index)
-- 2. title_source, title_evidence to semantic_tree_v2.tree_node
-- 3. aggregate_node_signals, suggest_node_label, relabel_vault functions
-- 4. Extended fetch_tree_flat to return title_source, title_evidence
--
-- Run with: psql -d your_database -f migrations/013_label_sem_tree.sql
--
-- Or pipe from host into Docker:
--   docker compose exec -T db psql -U badger -d badgerdb < migrations/013_label_sem_tree.sql

BEGIN;

-- ============================================================================
-- STEP 1: Add user_tags to public.documents
-- ============================================================================

ALTER TABLE public.documents
  ADD COLUMN IF NOT EXISTS user_tags TEXT[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS documents_user_tags_gin_idx
  ON public.documents
  USING GIN (user_tags);

-- ============================================================================
-- STEP 2: Add label metadata to semantic_tree_v2.tree_node
-- ============================================================================

ALTER TABLE semantic_tree_v2.tree_node
  ADD COLUMN IF NOT EXISTS title_source TEXT NOT NULL DEFAULT 'auto',
  ADD COLUMN IF NOT EXISTS title_evidence JSONB;

-- Add check constraint (only if not exists - use DO block for idempotency)
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM pg_constraint
    WHERE conname = 'tree_node_title_source_check'
    AND conrelid = 'semantic_tree_v2.tree_node'::regclass
  ) THEN
    ALTER TABLE semantic_tree_v2.tree_node
      ADD CONSTRAINT tree_node_title_source_check
      CHECK (title_source IN ('auto', 'pinned', 'manual'));
  END IF;
END $$;

COMMENT ON COLUMN semantic_tree_v2.tree_node.title_source IS 'auto: tag-derived, relabelable. pinned: user-pinned, never relabeled. manual: user-set.';
COMMENT ON COLUMN semantic_tree_v2.tree_node.title_evidence IS 'JSON from aggregate_node_signals when title was last auto-derived.';

-- ============================================================================
-- STEP 3: aggregate_node_signals - pure read-only tag aggregation
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.aggregate_node_signals(
    p_vault_id UUID,
    p_node_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_doc_count INT;
    v_tag_stats JSONB;
BEGIN
    SELECT COUNT(*) INTO v_doc_count
    FROM semantic_tree_v2.node_document
    WHERE vault_id = p_vault_id
      AND node_id = p_node_id;

    SELECT jsonb_agg(
        jsonb_build_object(
            'tag', tag,
            'freq', freq,
            'ratio', freq::float / GREATEST(v_doc_count,1)
        )
    )
    INTO v_tag_stats
    FROM (
        SELECT tag, COUNT(*) as freq
        FROM semantic_tree_v2.node_document nd
        JOIN public.documents d ON d.id = nd.document_id
        JOIN LATERAL unnest(d.user_tags) tag ON TRUE
        WHERE nd.vault_id = p_vault_id
          AND nd.node_id = p_node_id
        GROUP BY tag
        ORDER BY freq DESC
    ) t;

    RETURN jsonb_build_object(
        'doc_count', v_doc_count,
        'tags', COALESCE(v_tag_stats, '[]'::jsonb)
    );
END;
$$;

-- ============================================================================
-- STEP 4: suggest_node_label - anchor detection + generalization
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.suggest_node_label(
    p_vault_id UUID,
    p_node_id UUID
)
RETURNS JSONB
LANGUAGE plpgsql
AS $$
DECLARE
    v_signals JSONB;
    v_doc_count INT;
    v_tags JSONB;
    v_top_tag TEXT;
    v_top_ratio FLOAT := 0;
    v_label TEXT;
BEGIN
    v_signals := semantic_tree_v2.aggregate_node_signals(p_vault_id, p_node_id);

    v_doc_count := (v_signals->>'doc_count')::INT;
    v_tags := v_signals->'tags';

    IF jsonb_array_length(COALESCE(v_tags, '[]'::jsonb)) > 0 THEN
        v_top_tag := v_tags->0->>'tag';
        v_top_ratio := (v_tags->0->>'ratio')::FLOAT;
    END IF;

    -- Anchor detection: top tag ratio >= 0.6
    IF v_top_ratio >= 0.6 THEN
        v_label := v_top_tag;
    ELSE
        -- Generalization rule
        IF v_doc_count >= 20 THEN
            v_label := COALESCE(v_top_tag, 'General');
        ELSIF v_doc_count BETWEEN 5 AND 19 THEN
            v_label := COALESCE(v_top_tag, 'Topic');
        ELSE
            v_label := COALESCE(v_top_tag, 'Cluster');
        END IF;
    END IF;

    RETURN jsonb_build_object(
        'label', v_label,
        'evidence', v_signals
    );
END;
$$;

-- ============================================================================
-- STEP 5: relabel_vault - batch update auto nodes, skip locked/pinned
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.relabel_vault(
    p_vault_id UUID
)
RETURNS VOID
LANGUAGE plpgsql
AS $$
DECLARE
    r RECORD;
    v_result JSONB;
BEGIN
    FOR r IN
        SELECT id
        FROM semantic_tree_v2.tree_node
        WHERE vault_id = p_vault_id
          AND locked = false
          AND title_source = 'auto'
    LOOP
        v_result := semantic_tree_v2.suggest_node_label(p_vault_id, r.id);

        UPDATE semantic_tree_v2.tree_node
        SET title = v_result->>'label',
            title_evidence = v_result->'evidence',
            updated_at = now()
        WHERE id = r.id
          AND vault_id = p_vault_id;
    END LOOP;
END;
$$;

-- ============================================================================
-- STEP 6: Extend fetch_tree_flat to return title_source, title_evidence
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
          'doc_count', COALESCE(s.doc_count,0),
          'updated_at', n.updated_at,
          'title_source', COALESCE(n.title_source, 'auto'),
          'title_evidence', n.title_evidence
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
