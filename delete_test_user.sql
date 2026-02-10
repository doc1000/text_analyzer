-- Script to delete test user newuser1@example.com
-- Run this before testing the sentence embedding copy fix
-- The user will be automatically recreated on next login/signup

BEGIN;

-- Check what will be deleted (for confirmation)
SELECT 
    'User' as entity_type,
    u.email,
    u.id as entity_id
FROM users u
WHERE u.email = 'newuser1@example.com'

UNION ALL

SELECT 
    'Vault' as entity_type,
    v.name as email,
    v.id as entity_id
FROM vaults v
WHERE v.owner_id IN (SELECT id FROM users WHERE email = 'newuser1@example.com')

UNION ALL

SELECT 
    'Document' as entity_type,
    d.title as email,
    d.id as entity_id
FROM documents d
WHERE d.vault_id IN (
    SELECT v.id FROM vaults v 
    WHERE v.owner_id IN (SELECT id FROM users WHERE email = 'newuser1@example.com')
);

-- Delete the user (CASCADE will handle vault_memberships, vaults, documents, and embeddings)
DELETE FROM users WHERE email = 'newuser1@example.com';

-- Verify deletion
SELECT 
    'Remaining users' as check_type,
    COUNT(*) as count
FROM users
WHERE email = 'newuser1@example.com';

COMMIT;

-- User can now be recreated via signup/login and will get sentence embeddings
