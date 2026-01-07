# Database Schema Documentation System

## Overview

This project automatically generates and maintains `DATABASE_SCHEMA.md` - a comprehensive markdown document describing the current database schema. This document is used by Cursor/AI to understand database structure when assisting with code.

## How It Works

### Automatic Updates

The schema documentation is automatically updated when:

1. **`init_db()` is called** - After `Base.metadata.create_all()` creates/updates tables, the schema inspection script runs automatically
2. **Manual execution** - Run `python inspect_schema.py` anytime to regenerate the documentation

### Files Involved

- **`inspect_schema.py`** - Script that connects to the database, inspects schema, and generates `DATABASE_SCHEMA.md`
- **`app/db.py`** - Contains `init_db()` which automatically calls schema inspection after table creation
- **`DATABASE_SCHEMA.md`** - Generated markdown document (auto-created in project root)

## Usage

### Automatic (Recommended)

Just call `init_db()` as normal:

```python
from app.db import init_db

init_db()  # Automatically updates DATABASE_SCHEMA.md
```

### Manual Update

If you need to update the schema documentation without running `init_db()`:

```bash
python inspect_schema.py
```

Or from Docker:

```bash
docker compose exec backend python inspect_schema.py
```

## What Gets Documented

The `DATABASE_SCHEMA.md` file includes:

1. **All Schemas** - Lists all database schemas (public, embedding, etc.)
2. **Table Structures** - For each table:
   - Column names, types, nullability, defaults
   - Foreign key relationships
   - Indexes (including IVFFlat vector indexes)
3. **Statistics** - Row counts for all tables
4. **Embedding Models** - Registered embedding models and their metadata
5. **Generation Timestamp** - When the documentation was last updated

## Benefits for Cursor/AI

Having `DATABASE_SCHEMA.md` in the workspace allows Cursor to:

- ✅ Understand table structures when writing queries
- ✅ Know foreign key relationships
- ✅ See available indexes for optimization
- ✅ Understand embedding table naming conventions
- ✅ Reference current schema without connecting to database

## Example Output

The generated `DATABASE_SCHEMA.md` looks like:

```markdown
# Database Schema Documentation

**Generated:** 2026-01-07 14:30:00

## Public Schema

### Table: `documents`
| Column | Type | Nullable | Default |
|--------|------|----------|---------|
| `id` | `UUID` | NO | |
| `url` | `TEXT` | NO | |
| `title` | `TEXT` | YES | |
...

## Embedding Schema

### Table: `embedding.text_embedding_3_small_v1_1536`
...
```

## Configuration

The schema inspection script connects to:
- **Host:** localhost:5433 (or from `DATABASE_URL` env var)
- **Database:** badgerdb
- **User:** badger

To inspect a different database, set the `DATABASE_URL` environment variable:

```bash
export DATABASE_URL="postgresql+psycopg2://user:pass@host:port/dbname"
python inspect_schema.py
```

## Troubleshooting

### Schema doc not updating

1. Check that `inspect_schema.py` exists in project root
2. Verify database connection (check `DATABASE_URL`)
3. Check console output for warnings/errors

### Connection errors

- Ensure PostgreSQL is running on localhost:5433
- Verify credentials in `DATABASE_URL`
- Check firewall/network settings

### Missing tables

- Run `init_db()` first to create tables
- Check that tables exist in database
- Verify schema names match (public, embedding)

## Best Practices

1. **Commit `DATABASE_SCHEMA.md`** - Include it in git so team members have current schema info
2. **Update after migrations** - Run `inspect_schema.py` after any manual schema changes
3. **Review before commits** - Check that schema doc reflects your changes
4. **Use in PRs** - Include schema doc updates in pull requests for schema changes

## Integration with Cursor

Cursor automatically reads workspace files, so `DATABASE_SCHEMA.md` is always available for reference. When you ask questions about database structure or write queries, Cursor will use this document to provide accurate assistance.
