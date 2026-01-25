-- db/init.sql
-- Database initialization script for fresh deployments

-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- ============================================================================
-- USERS & AUTHENTICATION
-- ============================================================================

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);

CREATE TABLE IF NOT EXISTS api_keys (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    key_hash VARCHAR(64) NOT NULL UNIQUE,
    prefix VARCHAR(32) NOT NULL DEFAULT 'vb_live_',
    name TEXT,
    key_hint VARCHAR(16),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_used_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_api_keys_user ON api_keys(user_id);
CREATE INDEX IF NOT EXISTS idx_api_keys_hash ON api_keys(key_hash);

-- ============================================================================
-- VAULTS & MEMBERSHIPS
-- ============================================================================

CREATE TABLE IF NOT EXISTS vaults (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    owner_id UUID REFERENCES users(id) ON DELETE SET NULL,
    is_personal BOOLEAN NOT NULL DEFAULT true,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    archived_at TIMESTAMPTZ
);
CREATE INDEX IF NOT EXISTS idx_vaults_owner ON vaults(owner_id);

CREATE TABLE IF NOT EXISTS vault_memberships (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_id UUID NOT NULL REFERENCES vaults(id) ON DELETE CASCADE,
    user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    role TEXT NOT NULL DEFAULT 'owner',
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (vault_id, user_id)
);
CREATE INDEX IF NOT EXISTS idx_vault_memberships_user ON vault_memberships(user_id);
CREATE INDEX IF NOT EXISTS idx_vault_memberships_vault ON vault_memberships(vault_id);

-- ============================================================================
-- DOCUMENTS
-- ============================================================================

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_id UUID REFERENCES vaults(id) ON DELETE CASCADE,
    created_by UUID REFERENCES users(id) ON DELETE SET NULL,
    url TEXT NOT NULL,
    title TEXT,
    full_text TEXT NOT NULL,
    captured_at TIMESTAMPTZ DEFAULT now(),
    assigned_topic_id UUID,
    assigned_topic_title TEXT
);
CREATE INDEX IF NOT EXISTS idx_documents_vault ON documents(vault_id);
CREATE INDEX IF NOT EXISTS idx_documents_created_by ON documents(created_by);

-- ============================================================================
-- EMBEDDING SCHEMA
-- Embedding tables are created dynamically by the application based on model config.
-- This schema just ensures the namespace and metadata table exist.
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS embedding;

CREATE TABLE IF NOT EXISTS embedding.embedding_model (
    id BIGSERIAL PRIMARY KEY,
    model_name VARCHAR NOT NULL,
    version VARCHAR NOT NULL,
    dimensions INTEGER NOT NULL,
    table_location VARCHAR NOT NULL,
    loaded_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
