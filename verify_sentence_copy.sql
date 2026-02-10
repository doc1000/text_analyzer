-- Verification script for sentence embedding copy fix
-- Run this after deleting and recreating newuser1@example.com to verify the fix

-- 1. Check if newuser1@example.com exists and has vault access
SELECT 
    u.email,
    u.id as user_id,
    vm.vault_id,
    vm.role,
    v.name as vault_name,
    v.is_personal
FROM users u
LEFT JOIN vault_memberships vm ON vm.user_id = u.id
LEFT JOIN vaults v ON v.id = vm.vault_id
WHERE u.email = 'newuser1@example.com';

-- 2. Check if documents, chunks, and sentences were copied for newuser1
SELECT 
    u.email,
    v.name as vault_name,
    COUNT(DISTINCT d.id) as doc_count,
    COUNT(DISTINCT c.id) as chunk_count,
    COUNT(DISTINCT s.id) as sentence_count,
    COUNT(s.id) FILTER (WHERE s.embedding IS NOT NULL) as embedded_sentences
FROM users u
JOIN vaults v ON v.owner_id = u.id AND v.is_personal = true
LEFT JOIN documents d ON d.vault_id = v.id
LEFT JOIN embedding.all_minilm_v1_384 c ON c.document_id = d.id
LEFT JOIN embedding.sent_all_minilm_v1_384 s ON s.chunk_id = c.id
WHERE u.email = 'newuser1@example.com'
GROUP BY u.email, v.name;

-- 3. Compare newuser1 with template user (should have same counts)
SELECT 
    u.email,
    v.name as vault_name,
    COUNT(DISTINCT d.id) as doc_count,
    COUNT(DISTINCT c.id) as chunk_count,
    COUNT(DISTINCT s.id) as sentence_count,
    COUNT(s.id) FILTER (WHERE s.embedding IS NOT NULL) as embedded_sentences
FROM users u
JOIN vaults v ON v.owner_id = u.id AND v.is_personal = true
LEFT JOIN documents d ON d.vault_id = v.id
LEFT JOIN embedding.all_minilm_v1_384 c ON c.document_id = d.id
LEFT JOIN embedding.sent_all_minilm_v1_384 s ON s.chunk_id = c.id
WHERE u.email IN ('newuser@example.com', 'newuser1@example.com')
GROUP BY u.email, v.name
ORDER BY u.email;

-- 4. Verify sentence embeddings link correctly to chunks
-- Should return data if copy was successful
SELECT 
    d.title as document_title,
    c.chunk_index,
    COUNT(s.id) as sentence_count,
    COUNT(s.id) FILTER (WHERE s.embedding IS NOT NULL) as embedded_count,
    MIN(s.sent_index) as min_sent_index,
    MAX(s.sent_index) as max_sent_index
FROM documents d
JOIN embedding.all_minilm_v1_384 c ON c.document_id = d.id
JOIN embedding.sent_all_minilm_v1_384 s ON s.chunk_id = c.id
WHERE d.vault_id IN (
    SELECT v.id 
    FROM vaults v 
    JOIN users u ON u.id = v.owner_id 
    WHERE u.email = 'newuser1@example.com'
)
GROUP BY d.id, d.title, c.chunk_index
ORDER BY d.title, c.chunk_index
LIMIT 10;

-- Expected results:
-- Query 1: Should show newuser1@example.com with vault_id and 'owner' role
-- Query 2: Should show doc_count > 0, chunk_count > 0, sentence_count > 0, embedded_sentences > 0
-- Query 3: Both users should have similar counts
-- Query 4: Should show sentences linked to chunks with embeddings
