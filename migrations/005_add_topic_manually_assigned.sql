-- Migration 005: Add topic_manually_assigned flag to documents table
-- This flag protects manually-assigned topics from being overwritten during re-clustering

-- Add the topic_manually_assigned column
ALTER TABLE documents ADD COLUMN topic_manually_assigned BOOLEAN DEFAULT FALSE;

-- Create partial index for efficient filtering (only indexes TRUE values)
CREATE INDEX idx_documents_manual_topic ON documents(topic_manually_assigned) 
  WHERE topic_manually_assigned = TRUE;

-- Add column comment for documentation
COMMENT ON COLUMN documents.topic_manually_assigned IS 
  'TRUE when topic was manually assigned by user (via UI), FALSE/NULL when auto-assigned by clustering. Protects manual assignments from being overwritten during re-clustering.';
