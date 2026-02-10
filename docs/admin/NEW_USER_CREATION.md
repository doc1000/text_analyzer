# New User Creation Process

## Overview

When a new user is created in VaultBubbles, they automatically receive a fully populated personal vault with pre-calculated embeddings copied from a template user (`newuser@example.com`). This ensures users see content immediately upon first login instead of a blank canvas.

## Process Flow

### 1. User Creation Triggers

New users are created at two entry points:

**A. Extension Verification Flow** (`app/main.py::extension_verify_code()`, lines 1770-1780)
- User requests access via browser extension
- User verifies email with 6-digit code
- System creates or retrieves user account
- **If new user**: `create_personal_vault()` is called immediately

**B. Bootstrap API Key Creation** (`app/main.py::bootstrap_create_api_key()`, lines 147-154)
- Admin creates API key for user via bootstrap endpoint
- System creates or retrieves user account
- **If new user**: `create_personal_vault()` is called immediately

### 2. Personal Vault Creation

**Function**: `create_personal_vault()` in `app/auth.py` (lines 546-659)

**Steps**:

1. **Create Empty Vault**
   - Creates a `Vault` record with name "Personal Vault"
   - Sets `owner_id` to the new user
   - Marks as `is_personal=True`
   - Flushes to database to get `vault.id`

2. **Create Vault Membership**
   - Creates `VaultMembership` with role "owner"
   - Links user to vault
   - Commits vault and membership to database

3. **Attempt Template Copy**
   - Queries for `newuser@example.com` user
   - Finds their personal vault
   - Calls `copy_template_vault_to_user()` to copy all content

4. **Fallback (if template unavailable)**
   - Creates single "Getting Started" document from hardcoded content
   - Generates embeddings for document using `embed_doc_chunks()`
   - Creates topic hierarchy with `_save_topic_to_db()`
   - Assigns document to topics

### 3. Template Vault Copying

**Function**: `copy_template_vault_to_user()` in `app/auth.py` (lines 351-543)

This function performs a complete copy of all content from the template vault to the new user's vault, including documents, embeddings, and topics with proper vault isolation.

#### A. Document Copying

**Process**:
1. Query all documents from template vault: `db.query(Document).filter(Document.vault_id == template_vault.id).all()`
2. Create new `Document` records for new vault:
   - Sets `vault_id=new_vault.id`
   - Sets `created_by=new_user.id`
   - Copies all text fields (`captured_text`, `extracted_text`, `full_text`)
   - Copies metadata (`url`, `title`, `captured_at`)
   - Initially sets `assigned_topic_id=None` (updated after topics are copied)
3. Maintains mapping: `doc_id_map[old_doc_id] = new_doc_id`

#### B. Chunk Embedding Copying

**Process**:
1. Get embedding model configuration from `PREFERENCES.models`
2. Determine model name, version, and dimensions (e.g., "all-minilm", "v1", 384)
3. Get dynamic embedding table classes:
   - `get_or_create_embedding_class(model_name, version, dim, db, chunk_type="chunk")`
   - `get_or_create_embedding_class(model_name, version, dim, db, chunk_type="sent")`
4. For each copied document:
   - Query chunks from template: `db.query(ChunkEmbedding).filter(ChunkEmbedding.document_id == old_doc_id).all()`
   - Create new chunk records with:
     - New UUID (tracked in `chunk_id_map`)
     - Updated `document_id` (using `doc_id_map`)
     - Copied `chunk_text`, `summary_text`, and `embedding` vector
     - Preserved `chunk_index` and `created_at`
   - Maintain mapping: `chunk_id_map[old_chunk_id] = new_chunk_id`

**Result**: All pre-calculated chunk embeddings are copied with ID mapping for sentence references.

#### C. Sentence Embedding Copying

**Process**:
1. For each copied chunk (using `chunk_id_map`):
   - Query sentences from template: `db.query(SentenceEmbedding).filter(SentenceEmbedding.chunk_id == old_chunk_id).all()`
   - Create new sentence records with:
     - New UUID
     - Updated `chunk_id` (using `chunk_id_map`)
     - Copied `sent_text`, `embedding` vector
     - Preserved `sent_index` and `created_at`
2. Track total sentence count for logging

**Result**: All sentence embeddings are copied, enabling query functionality.

**Critical**: Sentence embeddings are used by the query endpoint (`/query`). Without them, users would get "no matching chunks found" even though documents and chunks exist. The query joins `SENTENCE_TABLE → EMBED_TABLE → Document` and filters by `SENTENCE_TABLE.embedding != None`.

#### D. Document Embedding Copying

**Process**:
1. Get document embedding table class: `get_or_create_embedding_class(model_name, version, dim, db, chunk_type="doc")`
2. For each copied document:
   - Query document embedding from template
   - Create new record with updated `document_id`
   - Copy `summary_text` and `embedding` vector

**Result**: Document-level summaries and embeddings are preserved.

#### E. Topic Hierarchy Copying

**Process**:
1. Identify all topics assigned to template documents: `template_topic_ids = {doc.assigned_topic_id for doc in template_docs}`
2. Recursively gather topics and their parent chain using `get_topic_with_parents()`
3. Get topic embedding table class: `get_or_create_embedding_class(model_name, version, dim, db, chunk_type="topic")`
4. Sort topics by level (highest first) to maintain parent-child order
5. For each topic:
   - Create new topic record with:
     - New UUID
     - **`vault_id=new_vault.id`** (CRITICAL: ensures vault isolation)
     - Updated `parent_id` (using `topic_id_map` to reference new parent)
     - Copied `level_index`, `title_text`, `summary_text`, `embedding`
     - Preserved `document_count`, `match_count`, `created_at`
6. Maintain mapping: `topic_id_map[old_topic_id] = new_topic_id`

**Result**: Complete topic hierarchy is copied with vault-scoped IDs.

#### F. Document-Topic Assignment

**Process**:
1. For each copied document:
   - Find corresponding template document
   - If template document had `assigned_topic_id`:
     - Look up new topic ID using `topic_id_map`
     - Set `new_doc.assigned_topic_id = new_topic_id`
     - Copy `assigned_topic_title`

2. Commit all changes to database

**Result**: Documents are properly assigned to their vault-scoped topic hierarchy.

### 4. Vault Isolation

After the migration to add `vault_id` to topics (migration `006_add_vault_to_topics.sql`), all topics are now vault-scoped:

- Topics include a required `vault_id` column with foreign key to `vaults(id)` with CASCADE delete
- Composite index on `(vault_id, level_index)` for efficient queries
- Topic queries filter by user's accessible vault IDs
- Topic matching via `_find_matching_topic()` respects vault boundaries
- Users can only see topics from vaults they have access to

**Key Functions with Vault Filtering**:
- `_save_topic_to_db()` - Requires `vault_id` parameter
- `_find_matching_topic()` - Filters by `vault_ids` list
- `build_topics_hierarchy()` - Filters documents and topics by vault
- `compute_hierarchical_topics()` - Creates topics scoped to vault

## Template User Setup

### Prerequisites

The `newuser@example.com` template user must be set up before new users can receive pre-populated vaults.

### Setup Steps

1. **Create Template User**
   ```python
   # Via bootstrap API or direct database insert
   user = User(email="newuser@example.com")
   db.add(user)
   db.commit()
   ```

2. **Create Template Vault**
   ```python
   vault = create_personal_vault(user, db)
   ```

3. **Add Documents to Template Vault**
   - Add "Getting Started" guide and any other template documents
   - Documents can be added via UI or API

4. **Generate Embeddings and Topics**
   - System automatically generates embeddings when documents are ingested
   - Topics are created via clustering or manual assignment
   - Run reclustering if needed: `/topics/recluster` endpoint

5. **Verify Template Content**
   - Ensure template vault has documents with embeddings
   - Verify topics exist with proper `vault_id` set
   - Check that topic hierarchy is complete (Level 0, Level 1, Level 2)

### Regenerating Template Topics

Since migration 006 cleared all existing topics, you must regenerate topics for the template user:

```bash
# Via API (requires auth)
curl -X POST "http://localhost:8000/topics/recluster?days=365" \
  -H "Authorization: Bearer vb_live_..."

# Or via UI
# 1. Log in as newuser@example.com
# 2. Click "Reload" button to trigger reclustering
```

## Error Handling

### Template User Not Found

If `newuser@example.com` doesn't exist:
- System logs: "Template user newuser@example.com not found, falling back to old behavior"
- Falls back to creating single "Getting Started" document
- Generates fresh embeddings and topics

### Template Vault Empty

If template user exists but vault has no documents:
- System logs: "Warning: Template vault has no documents"
- Returns `False` from `copy_template_vault_to_user()`
- Falls back to creating single "Getting Started" document

### Copy Failure

If copying process raises exception:
- System logs error with traceback
- Rolls back database transaction
- Returns `False` from `copy_template_vault_to_user()`
- Falls back to creating single "Getting Started" document

### Partial Copy

If database operations succeed but topics are empty:
- New user still gets documents and embeddings
- Topics can be generated later via reclustering

## Benefits

### 1. Immediate Content Visibility
- Users see populated vault on first login
- No blank canvas or empty state

### 2. Pre-Calculated Embeddings
- All chunk, sentence, document, and topic embeddings are copied
- No expensive embedding computation at user creation time
- Fast vault creation (sub-second instead of several seconds)
- Query functionality works immediately (sentence embeddings enable search)

### 3. Consistent Onboarding
- All users start with same high-quality template content
- Template can include tutorials, examples, and guides
- Easy to update template to improve onboarding for all future users

### 4. Vault Isolation
- Each user gets independent copies of all data
- Modifications to user's vault don't affect template or other users
- Topics are scoped to vault, preventing cross-vault data leakage

### 5. Graceful Degradation
- If template is unavailable, system falls back to single-document creation
- User creation never fails due to template issues
- Robust error handling with detailed logging

## Monitoring

### Success Indicators

Check logs for:
```
Found template vault for newuser@example.com, copying to user@example.com
Copied N documents from template vault
Copied chunk embeddings for N documents
Copied M sentence embeddings for N chunks
Copied document embeddings for N documents
Copied X topics from template vault
Successfully copied template vault to user user@example.com
```

### Failure Indicators

Watch for:
```
Template user newuser@example.com not found, falling back to old behavior
Template user exists but has no vault, falling back to old behavior
Warning: Template vault has no documents
Error copying template vault: ...
```

## Module Reference

### Core Modules

- **`app/auth.py`** - User authentication and vault creation logic
  - `create_personal_vault()` - Main vault creation function
  - `copy_template_vault_to_user()` - Template copying logic
  - `get_user_vault()` - Lazy vault retrieval (now creates immediately)

- **`app/main.py`** - API endpoints
  - `extension_verify_code()` - Extension OAuth flow endpoint
  - `bootstrap_create_api_key()` - Admin API key creation endpoint

- **`app/topics.py`** - Topic management
  - `_save_topic_to_db()` - Save topic with vault_id
  - `_find_matching_topic()` - Find topics scoped to vault
  - `compute_document_embeddings()` - Generate document embeddings

- **`app/helpers.py`** - Embedding utilities
  - `embed_doc_chunks()` - Generate chunk embeddings for document

- **`app/models.py`** - Database models
  - `User`, `Vault`, `VaultMembership` - Core entities
  - `Document` - Document with vault_id
  - `get_or_create_embedding_class()` - Dynamic embedding table access

### Database Tables

- **`users`** - User accounts
- **`vaults`** - Knowledge containers
- **`vault_memberships`** - User-vault permissions
- **`documents`** - Documents with vault_id foreign key
- **`embedding.{model}_v{version}_{dim}`** - Chunk embeddings with document_id foreign key
- **`embedding.sent_{model}_v{version}_{dim}`** - Sentence embeddings with chunk_id foreign key (critical for query)
- **`embedding.doc_{model}_v{version}_{dim}`** - Document embeddings with document_id foreign key
- **`embedding.topic_{model}_v{version}_{dim}`** - Topic embeddings with vault_id and parent_id foreign keys

## Configuration

### Environment Variables

No special configuration needed. The system uses existing embedding model configuration from `PREFERENCES.models`.

### Template User Email

Hardcoded in `create_personal_vault()` as `"newuser@example.com"`. Change this constant if using a different template user email.

## Future Enhancements

### Potential Improvements

1. **Multiple Templates** - Support different templates for different user types
2. **Template Versioning** - Track template version and migrate users to new templates
3. **Selective Copying** - Allow configuration of which content to copy
4. **Async Copying** - Move copying to background task for very large templates
5. **Template Management UI** - Admin interface to manage template content
6. **Copy Statistics** - Track copy success rates and performance metrics

## Related Documentation

- **Topic Vault Segregation**: `docs/admin/TOPIC_VAULT_SEGREGATION.md` - Details on vault-scoped topics
- **Topic Persistence**: `docs/admin/TOPIC_PERSISTENCE_IMPLEMENTATION.md` - How topics are matched and persisted
- **Migration 006**: `migrations/006_add_vault_to_topics.sql` - Database migration adding vault_id to topics

## Recent Changes

### February 10, 2026 - Added Sentence Embedding Copy

**Issue**: New users were getting "no matching chunks found" when querying, even though they had documents and chunk embeddings copied to their vaults.

**Root Cause**: The query endpoint (`/query` in `app/main.py`) searches sentence embeddings, not chunk embeddings. The query joins `SENTENCE_TABLE → EMBED_TABLE → Document` and filters by `SENTENCE_TABLE.embedding != None`. The template copy process was copying documents, chunk embeddings, document embeddings, and topics, but NOT sentence embeddings.

**Fix**: Modified `copy_template_vault_to_user()` in `app/auth.py` (lines 426-472) to:
1. Track chunk ID mappings during chunk copy (`chunk_id_map`)
2. Copy all sentence embeddings using the chunk ID mappings
3. Log sentence count for verification

**Impact**: New users now get full query functionality immediately upon account creation.

**Testing**: Delete and recreate test users to verify sentence embeddings are copied. Use `verify_sentence_copy.sql` to check counts.
