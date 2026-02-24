-- Migration 018: Persistent IncrementalPCA model storage
--
-- Stores PCA arrays (components, mean, explained_variance) trained globally on
-- all chunk embeddings. `active` is only set TRUE after the background thread
-- has finished recomputing all document.reduced_embedding values with this model,
-- ensuring clustering always sees a fully-consistent geometry.

CREATE TABLE IF NOT EXISTS embedding.pca_model (
    id                   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    vault_scope          TEXT NOT NULL DEFAULT 'global',  -- 'global' or vault_id
    source_table         TEXT NOT NULL,                   -- e.g. 'all_minilm_v1_384'
    original_dim         INT NOT NULL,
    n_components         INT NOT NULL,
    components           FLOAT8[][] NOT NULL,             -- shape: (n_components, original_dim)
    mean                 FLOAT8[] NOT NULL,               -- shape: (original_dim,)
    explained_variance   FLOAT8[] NULL,                   -- shape: (n_components,)
    training_chunk_count INT NULL,
    trained_at           TIMESTAMP NULL,
    created_at           TIMESTAMP NOT NULL DEFAULT now(),
    active               BOOLEAN NOT NULL DEFAULT false   -- true only after recompute completes
);

CREATE INDEX IF NOT EXISTS ix_pca_model_scope_table_active
    ON embedding.pca_model (vault_scope, source_table, active);
