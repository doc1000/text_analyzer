-- db/init.sql

-- Enable pgvector
CREATE EXTENSION IF NOT EXISTS vector;

-- Example schema; tweak as needed

CREATE TABLE IF NOT EXISTS documents (
    id UUID PRIMARY KEY,
    url TEXT,
    title TEXT,
    full_text TEXT,
    score_info REAL,
    score_ai_slop REAL,
    captured_at TIMESTAMPTZ DEFAULT now()
);

-- Adjust dimension to match your embedding model
-- e.g. 1536 for OpenAI text-embedding-3-large
CREATE TABLE IF NOT EXISTS chunks (
    id UUID PRIMARY KEY,
    document_id UUID REFERENCES documents(id) ON DELETE CASCADE,
    chunk_index INT,
    chunk_text TEXT,
    embedding vector(1536),
    score_info REAL,
    score_ai_slop REAL
);

-- Example index for cosine similarity
CREATE INDEX IF NOT EXISTS idx_chunks_embedding
ON chunks
USING ivfflat (embedding vector_cosine_ops)
WITH (lists = 100);
