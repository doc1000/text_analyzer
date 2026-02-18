-- Migration: 007_add_ingest_queue.sql
-- Description: Add ingest_queue table for async ingest processing
--
-- This migration creates a holding table to capture ingest payloads immediately,
-- allowing the API to return a fast success while processing (PDF parse,
-- trafilatura, embedding, deduplication) happens asynchronously.
--
-- Run with: psql -d your_database -f migrations/007_add_ingest_queue.sql

BEGIN;

CREATE TABLE IF NOT EXISTS ingest_queue (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id UUID REFERENCES users(id) ON DELETE SET NULL,
    vault_id UUID REFERENCES vaults(id) ON DELETE SET NULL,
    payload_json JSONB NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'processing', 'completed', 'failed')),
    document_id UUID,
    created_at TIMESTAMP WITH TIME ZONE NOT NULL DEFAULT NOW(),
    processing_started_at TIMESTAMP WITH TIME ZONE,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingest_queue_status ON ingest_queue(status);
CREATE INDEX IF NOT EXISTS idx_ingest_queue_created_at ON ingest_queue(created_at);

COMMIT;
