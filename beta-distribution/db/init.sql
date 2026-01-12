-- db/init.sql
-- Database initialization script for VaultBubble
-- This script runs automatically when the database container starts for the first time

-- Enable pgvector extension for vector similarity search
CREATE EXTENSION IF NOT EXISTS vector;

-- Note: Tables are created automatically by SQLAlchemy's Base.metadata.create_all()
-- in app/db.py when the backend starts. This script only enables the pgvector extension.
