-- Migration: 008_semantic_tree_v2.sql
-- Description: Isolated vault-scoped semantic tree (semantic_tree_v2 schema)
--
-- This migration:
-- 1. Creates semantic_tree_v2 schema with vault_user_role, tree_node, tree_node_stats,
--    node_document, user_workspace_vault
-- 2. Enforces strict vault isolation via composite FK on parent_id
-- 3. Defines SQL functions: user_has_vault_role, create_node, move_node, attach_document, fetch_tree_flat
--
-- Run with: psql -d your_database -f migrations/008_semantic_tree_v2.sql
--
-- Or pipe from host into Docker:
--   docker compose exec -T db psql -U badger -d badgerdb < migrations/008_semantic_tree_v2.sql

BEGIN;

-- ============================================================================
-- semantic_tree_v2: Isolated vault-scoped semantic tree
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS semantic_tree_v2;

-- ============================================================================
-- Vault Roles
-- ============================================================================

DO $$ BEGIN
  CREATE TYPE semantic_tree_v2.vault_role AS ENUM ('owner','admin','editor','viewer');
EXCEPTION WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS semantic_tree_v2.vault_user_role (
  vault_id   UUID NOT NULL,
  user_id    UUID NOT NULL,
  role       semantic_tree_v2.vault_role NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (vault_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_vault_user_role_user
ON semantic_tree_v2.vault_user_role(user_id, vault_id);

-- ============================================================================
-- Tree Nodes (STRICT vault isolation)
-- ============================================================================

CREATE TABLE IF NOT EXISTS semantic_tree_v2.tree_node (
  id          UUID NOT NULL,
  vault_id    UUID NOT NULL,
  parent_id   UUID NULL,
  title       TEXT NOT NULL,
  summary     TEXT NULL,
  sort_key    TEXT NOT NULL DEFAULT '',
  created_by  UUID NOT NULL,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (id),
  UNIQUE (id, vault_id),

  -- CRITICAL: parent must be in same vault
  CONSTRAINT fk_tree_node_parent_same_vault
    FOREIGN KEY (parent_id, vault_id)
    REFERENCES semantic_tree_v2.tree_node(id, vault_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tree_node_vault
ON semantic_tree_v2.tree_node(vault_id);

CREATE INDEX IF NOT EXISTS idx_tree_node_parent
ON semantic_tree_v2.tree_node(vault_id, parent_id);

-- ============================================================================
-- Node Stats (UI performance, optional)
-- ============================================================================

CREATE TABLE IF NOT EXISTS semantic_tree_v2.tree_node_stats (
  node_id    UUID PRIMARY KEY,
  vault_id   UUID NOT NULL,
  doc_count  INTEGER NOT NULL DEFAULT 0,
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),

  CONSTRAINT fk_tree_node_stats_node
    FOREIGN KEY (node_id, vault_id)
    REFERENCES semantic_tree_v2.tree_node(id, vault_id)
    ON DELETE CASCADE
);

-- ============================================================================
-- Document Attachment (Decoupled)
-- documents table uses id as PK, not (id, vault_id) composite - no FK added
-- ============================================================================

CREATE TABLE IF NOT EXISTS semantic_tree_v2.node_document (
  vault_id     UUID NOT NULL,
  node_id      UUID NOT NULL,
  document_id  UUID NOT NULL,
  created_by   UUID NOT NULL,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),

  PRIMARY KEY (vault_id, node_id, document_id),

  CONSTRAINT fk_node_document_node
    FOREIGN KEY (node_id, vault_id)
    REFERENCES semantic_tree_v2.tree_node(id, vault_id)
    ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_node_document_doc
ON semantic_tree_v2.node_document(vault_id, document_id);

-- ============================================================================
-- Workspace Overlay (User-level, not structural)
-- ============================================================================

CREATE TABLE IF NOT EXISTS semantic_tree_v2.user_workspace_vault (
  user_id      UUID NOT NULL,
  workspace_id UUID NOT NULL,
  vault_id     UUID NOT NULL,
  pinned       BOOLEAN NOT NULL DEFAULT FALSE,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  PRIMARY KEY (user_id, workspace_id, vault_id)
);

-- ============================================================================
-- Permission + Tree Functions
-- ============================================================================

CREATE OR REPLACE FUNCTION semantic_tree_v2.user_has_vault_role(
  p_user_id UUID,
  p_vault_id UUID,
  p_min_role semantic_tree_v2.vault_role
) RETURNS BOOLEAN
LANGUAGE sql
STABLE
AS $$
  WITH r AS (
    SELECT role
    FROM semantic_tree_v2.vault_user_role
    WHERE user_id = p_user_id
      AND vault_id = p_vault_id
  )
  SELECT CASE p_min_role
    WHEN 'viewer' THEN EXISTS (SELECT 1 FROM r)
    WHEN 'editor' THEN EXISTS (SELECT 1 FROM r WHERE role IN ('owner','admin','editor'))
    WHEN 'admin'  THEN EXISTS (SELECT 1 FROM r WHERE role IN ('owner','admin'))
    WHEN 'owner'  THEN EXISTS (SELECT 1 FROM r WHERE role = 'owner')
  END;
$$;

CREATE OR REPLACE FUNCTION semantic_tree_v2.create_node(
  p_user_id UUID,
  p_vault_id UUID,
  p_parent_id UUID,
  p_title TEXT,
  p_summary TEXT DEFAULT NULL,
  p_sort_key TEXT DEFAULT ''
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
    (id, vault_id, parent_id, title, summary, sort_key, created_by)
  VALUES
    (v_id, p_vault_id, p_parent_id, p_title, p_summary, p_sort_key, p_user_id);

  INSERT INTO semantic_tree_v2.tree_node_stats(node_id, vault_id)
  VALUES (v_id, p_vault_id)
  ON CONFLICT (node_id) DO NOTHING;

  RETURN v_id;
END $$;

CREATE OR REPLACE FUNCTION semantic_tree_v2.move_node(
  p_user_id UUID,
  p_vault_id UUID,
  p_node_id UUID,
  p_new_parent_id UUID
) RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  IF NOT semantic_tree_v2.user_has_vault_role(p_user_id, p_vault_id, 'editor') THEN
    RAISE EXCEPTION 'insufficient_privilege';
  END IF;

  UPDATE semantic_tree_v2.tree_node
  SET parent_id = p_new_parent_id,
      updated_at = now()
  WHERE id = p_node_id
    AND vault_id = p_vault_id;

  IF NOT FOUND THEN
    RAISE EXCEPTION 'node_not_found';
  END IF;
END $$;

CREATE OR REPLACE FUNCTION semantic_tree_v2.attach_document(
  p_user_id UUID,
  p_vault_id UUID,
  p_node_id UUID,
  p_document_id UUID
) RETURNS VOID
LANGUAGE plpgsql
AS $$
BEGIN
  IF NOT semantic_tree_v2.user_has_vault_role(p_user_id, p_vault_id, 'editor') THEN
    RAISE EXCEPTION 'insufficient_privilege';
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
          'updated_at', n.updated_at
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
