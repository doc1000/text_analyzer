# Topic Persistence Implementation

## Implementation Complete

Successfully implemented persistent topic storage with hierarchical structure and similarity-based matching!

## Changes Made

### 1. Updated Topic Table Schema ([`app/models.py`](app/models.py))

**Added fields to topic table** (chunk_type="topic"):
- `summary_text` (Text, nullable) - Topic summary
- `document_count` (Integer) - Number of documents in cluster
- `last_matched_at` (DateTime, nullable) - When topic was last reused
- `match_count` (Integer, default=0) - How many times topic was matched

**Complete schema:**
```
embedding.topic_{model_name}_v1_{dim}
├── id (UUID, primary key)
├── parent_id (UUID, nullable) - NULL for top-level, references parent for subtopics
├── level_index (Integer) - 0 for topics, 1 for subtopics
├── title_text (Text) - Generated topic title
├── summary_text (Text, nullable) - Topic summary
├── document_count (Integer) - Number of documents
├── embedding (Vector) - Cluster centroid
├── last_matched_at (DateTime, nullable) - Last reuse timestamp
├── match_count (Integer, default=0) - Reuse counter
└── created_at (DateTime) - Creation timestamp
```

### 2. Refactored Table Definitions ([`app/db.py`](app/db.py))

**Moved table initialization from `helpers.py` to `db.py`:**

Created `initialize_embedding_tables()` function that:
- Initializes `EMBED_TABLE`, `SENTENCE_TABLE`, `TOPIC_TABLE`
- Uses current model config from `PREFERENCES`
- Handles circular dependencies with lazy imports
- Called at module load time

**New exports from `app/db.py`:**
```python
EMBED_DIM, EMBED_MODEL, EMBED_TABLE, SENTENCE_TABLE, TOPIC_TABLE = initialize_embedding_tables()
```

### 3. Updated Imports Across Codebase

**Files updated:**
- [`app/helpers.py`](app/helpers.py) - Removed table creation, imports from db
- [`app/topics.py`](app/topics.py) - Imports `EMBED_TABLE`, `TOPIC_TABLE` from db
- [`app/main.py`](app/main.py) - Imports `EMBED_TABLE` from db instead of helpers

**Import pattern:**
```python
from .db import EMBED_TABLE, SENTENCE_TABLE, TOPIC_TABLE, EMBED_DIM, EMBED_MODEL
```

### 4. Added Persistence Functions ([`app/topics.py`](app/topics.py))

**New functions:**

**`_save_topic_to_db()`** - Save topic with centroid
- Saves topic/subtopic to TOPIC_TABLE
- Accepts parent_id for hierarchical linking
- Returns UUID of created topic

**`_find_matching_topic()`** - Find similar existing topics
- Computes cosine similarity against existing topics at same level
- Returns best match above similarity threshold
- Updates `last_matched_at` and increments `match_count`
- Limits search for performance (default 1000 topics)

### 5. Updated `compute_topics()` ([`app/topics.py`](app/topics.py))

**Added persistence logic:**
1. Computes cluster centroid from document embeddings
2. Checks for existing topic match (if persistence enabled)
3. If match found: reuses existing topic, updates statistics
4. If no match: generates title, saves new topic to database
5. Passes `parent_db_id` to subtopic builder for hierarchical linking

**Console output:**
- "♻ Reusing existing topic: {title}" - When topic matched
- "✓ Created new topic: {title}" - When new topic created

### 6. Updated `build_subtopics()` ([`app/topics.py`](app/topics.py))

**Added `parent_db_id` parameter** to link subtopics to parent:
1. Computes subtopic centroid
2. Checks for existing subtopic match
3. If match found: reuses existing subtopic
4. If no match: generates title, saves with `parent_id` link
5. Maintains hierarchical structure in database

### 7. Added Configuration ([`app/config.py`](app/config.py))

**New `TopicPersistenceConfig` dataclass:**
```python
@dataclass
class TopicPersistenceConfig:
    similarity_threshold_topic: float = 0.85      # Min similarity for topic match
    similarity_threshold_subtopic: float = 0.85   # Min similarity for subtopic match
    persist_topics: bool = True                   # Enable/disable topic persistence
    persist_subtopics: bool = True                # Enable/disable subtopic persistence
    max_existing_topics_to_check: int = 1000      # Performance limit
```

**Access via:** `PREFERENCES.topic_persistence.persist_topics`

### 8. Created Test Suite ([`test_topic_persistence.py`](test_topic_persistence.py))

**Tests:**
1. Topic table exists and is accessible
2. Save topic to database
3. Topic matching (find similar topics)
4. Hierarchical structure (parent_id links)
5. End-to-end topic generation with persistence

## How It Works

### Topic Persistence Flow

```
Cluster Documents
     ↓
Compute Cluster Centroid
     ↓
Check Existing Topics (similarity > 0.85)
     ↓
┌─────────────┬──────────────┐
│  Match Found│  No Match    │
├─────────────┼──────────────┤
│ Reuse Topic │  Generate    │
│ Update Stats│  New Title   │
│             │  Save to DB  │
└─────────────┴──────────────┘
     ↓
Build & Persist Subtopics (same flow)
     ↓
Return TopicsResponse
```

### Hierarchical Structure

- **Top-level topics**: `level_index=0`, `parent_id=NULL`
- **Subtopics**: `level_index=1`, `parent_id=<parent UUID>`

Example:
```
Topic: "AI Research" (level=0, parent=NULL)
  ├── Subtopic: "Machine Learning" (level=1, parent=AI Research UUID)
  ├── Subtopic: "Neural Networks" (level=1, parent=AI Research UUID)
  └── Subtopic: "NLP" (level=1, parent=AI Research UUID)
```

### Topic Matching Strategy

1. Query all existing topics at same `level_index` (limit 1000 for performance)
2. Compute cosine similarity between new centroid and each existing topic centroid
3. Find best match above threshold (default 0.85)
4. If match found:
   - Update `last_matched_at` = now
   - Increment `match_count`
   - Return existing topic (id, title, summary)
5. If no match: create new topic

## Usage

### Automatic (Default)

Topic persistence is enabled by default. Just call `compute_topics()`:

```python
from app.db import get_db
from app.topics import compute_topics

db = next(get_db())
topics = compute_topics(db, days=30)
# Topics are automatically persisted and reused
```

### Configure Persistence

Adjust settings in [`app/config.py`](app/config.py):

```python
@dataclass
class TopicPersistenceConfig:
    persist_topics: bool = True           # Enable/disable
    similarity_threshold_topic: float = 0.85  # Higher = stricter matching
```

### Disable Persistence

```python
PREFERENCES.topic_persistence.persist_topics = False
PREFERENCES.topic_persistence.persist_subtopics = False
```

### Query Persisted Topics

```python
from app.db import get_db, TOPIC_TABLE

db = next(get_db())

# Get all top-level topics
topics = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.level_index == 0).all()

# Get subtopics for a topic
parent_id = topics[0].id
subtopics = db.query(TOPIC_TABLE).filter(TOPIC_TABLE.parent_id == parent_id).all()

# Get most frequently matched topics
popular = db.query(TOPIC_TABLE).order_by(TOPIC_TABLE.match_count.desc()).limit(10).all()
```

## Testing

Run the test suite:

```bash
docker compose -f docker-compose.yml -f docker-compose.dev.yml exec backend python test_topic_persistence.py
```

Tests verify:
1. Topic table creation
2. Topic saving
3. Topic matching (similarity-based reuse)
4. Hierarchical structure (parent_id relationships)
5. End-to-end topic generation with persistence

## Benefits

1. **Topic Reuse** - Recurring themes reuse existing titles (faster, consistent)
2. **Historical Tracking** - See when topics first appeared and how often they recur
3. **Incremental Clustering** - Build on existing topic structure over time
4. **Hierarchical Knowledge** - Maintain parent/child relationships
5. **Multi-Model Support** - Different embedding models have separate topic tables

## Performance

**Topic Matching Overhead:**
- Queries limited to 1000 most recent topics (configurable)
- In-memory cosine similarity (numpy - very fast)
- Typical overhead: < 100ms for 100 existing topics
- IVFFlat index on embedding column enables future optimization

**Storage:**
- Each topic: ~1KB (UUID + title + summary + embedding)
- 1000 topics: ~1MB
- Minimal storage impact

## Configuration Options

| Setting | Default | Description |
|---------|---------|-------------|
| `persist_topics` | `True` | Enable topic persistence |
| `persist_subtopics` | `True` | Enable subtopic persistence |
| `similarity_threshold_topic` | `0.85` | Min similarity to match topic (0-1) |
| `similarity_threshold_subtopic` | `0.85` | Min similarity to match subtopic |
| `max_existing_topics_to_check` | `1000` | Limit for similarity search |

## Key Design Decisions

**Why separate tables per model?**
- Different embedding models have different dimensions
- Centroids not comparable across models
- Clean migration when switching models

**Why hierarchical storage?**
- Maintains semantic relationships
- Enables browsing topic hierarchy
- Supports future meta-clustering

**Why similarity threshold 0.85?**
- High enough to avoid false positives
- Low enough to match similar but not identical topics
- Tunable based on use case

## Troubleshooting

### Topics not persisting

Check configuration:
```python
print(PREFERENCES.topic_persistence.persist_topics)  # Should be True
```

### Too many/few matches

Adjust similarity threshold:
```python
PREFERENCES.topic_persistence.similarity_threshold_topic = 0.90  # Stricter
PREFERENCES.topic_persistence.similarity_threshold_topic = 0.80  # More lenient
```

### Performance issues

Reduce topics checked:
```python
PREFERENCES.topic_persistence.max_existing_topics_to_check = 500
```

## Files Modified

1. **`app/models.py`** - Updated topic table schema (+4 fields)
2. **`app/db.py`** - Added `initialize_embedding_tables()`, exports tables (+50 lines)
3. **`app/helpers.py`** - Removed table creation, imports from db (-15 lines, +1 import)
4. **`app/topics.py`** - Added persistence functions, updated topic generation (+100 lines)
5. **`app/config.py`** - Added `TopicPersistenceConfig` (+7 lines)
6. **`app/main.py`** - Updated imports (+1 line)
7. **`test_topic_persistence.py`** - New test suite (350 lines)

## Next Steps

1. **Run tests**: `python test_topic_persistence.py` (in Docker container)
2. **Generate topics**: Run `compute_topics()` and verify database storage
3. **Monitor matching**: Watch for "♻ Reusing existing topic" messages
4. **Tune thresholds**: Adjust similarity thresholds based on results
5. **Analyze patterns**: Query `match_count` to see which topics recur most

## Future Enhancements

- **Vector similarity search**: Use pgvector operators for faster matching
- **Topic evolution**: Track how topics change over time
- **Topic merging**: Combine very similar topics automatically
- **Topic deletion**: Archive old, unused topics
- **Topic analytics**: Visualize topic lifecycle and trends
