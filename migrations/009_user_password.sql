-- Migration: 009_user_password.sql
-- Description: optional password sign-in (batch29). PBKDF2-SHA256 hash (app/passwords.py);
-- NULL = no password set (the emailed-code flow still works).
--
-- Run with: psql -d your_database -f migrations/009_user_password.sql

BEGIN;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_hash TEXT;
ALTER TABLE users ADD COLUMN IF NOT EXISTS password_set_at TIMESTAMP;
COMMIT;
