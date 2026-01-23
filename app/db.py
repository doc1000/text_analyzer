# app/db.py
import os
#import time
#from typing import Optional, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker #, Session
from .models import Base, get_or_create_embedding_class
from .config import PREFERENCES

_raw_db_url = os.getenv("DATABASE_URL", "postgresql+psycopg2://badger:badgerpass@db:5432/badgerdb")

# Fly.io uses postgres:// but SQLAlchemy needs postgresql://
# Also ensure we use psycopg2 driver
DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql+psycopg2://", 1)
if "postgresql://" in DATABASE_URL and "+psycopg2" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
def ensure_schemas():
    # Use AUTOCOMMIT so a failed extension install doesn't poison the transaction
    # (Fly Postgres can reject CREATE EXTENSION depending on setup/permissions).
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        # Enable pgvector extension (required for vector columns)
        try:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))
            print("✓ pgvector extension enabled")
        except Exception as e:
            print(f"⚠ Could not enable pgvector extension: {e}")
        
        # Schema for dynamic embedding tables + metadata
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS embedding"))


def _migrate_add_summary_columns():
    """
    Migration: Add summary_text column to existing chunk tables that don't have it.
    
    This handles the case where tables were created before the summary feature was added.
    SQLAlchemy's create_all() won't add new columns to existing tables.
    """
    with engine.connect() as conn:
        # Get list of tables in the embedding schema
        result = conn.execute(text("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'embedding'
        """))
        tables = [row[0] for row in result]
        
        for table_name in tables:
            # Skip tables that start with 'sent_', 'topic_', or 'doc_' - they have different schemas
            # We only need to add summary_text to chunk tables (base model name tables)
            if table_name.startswith('sent_') or table_name.startswith('topic_') or table_name.startswith('doc_'):
                continue
            
            # Skip the embedding_model metadata table
            if table_name == 'embedding_model':
                continue
            
            # Check if summary_text column exists
            col_check = conn.execute(text(f"""
                SELECT column_name 
                FROM information_schema.columns 
                WHERE table_schema = 'embedding' 
                AND table_name = :table_name 
                AND column_name = 'summary_text'
            """), {"table_name": table_name})
            
            if col_check.fetchone() is None:
                # Column doesn't exist, add it
                try:
                    conn.execute(text(f"""
                        ALTER TABLE embedding.{table_name} 
                        ADD COLUMN summary_text TEXT
                    """))
                    conn.commit()
                    print(f"✓ Added summary_text column to embedding.{table_name}")
                except Exception as e:
                    print(f"⚠ Could not add summary_text to {table_name}: {e}")


def init_db():
    """
    Initialize database: create schemas, tables, and generate schema documentation.
    
    After creating tables, automatically runs schema inspection to update DATABASE_SCHEMA.md
    for Cursor/AI reference.
    
    All tables are defined using SQLAlchemy's declarative base, suitable for Base.metadata.create_all(bind=engine).
    Each embedding model gets its own table under the embedding schema and a row in embedding.embedding_model.
    """
    ensure_schemas()
    
    # Run migrations for existing tables (add new columns)
    _migrate_add_summary_columns()
    
    Base.metadata.create_all(bind=engine)
    
    # Automatically generate schema documentation for Cursor/AI reference
    _update_schema_doc()
    
    #with Session(engine) as session:
    #    register_embedding_model(session, "bge-small-en", "v1.5", 512)


def _update_schema_doc():
    """
    Update DATABASE_SCHEMA.md by running the schema inspection script.
    This ensures Cursor/AI always has current schema information.
    """
    # In production (Fly), this file isn't needed and writing to the container FS is not useful.
    # Also avoids noisy warnings and slightly reduces startup time.
    if os.getenv("FLY_APP_NAME") or os.getenv("DISABLE_SCHEMA_DOC_UPDATE") in ("1", "true", "True"):
        return

    import subprocess
    import sys
    from pathlib import Path
    
    script_path = Path(__file__).parent.parent / "inspect_schema.py"
    
    if script_path.exists():
        try:
            # Run the schema inspection script
            result = subprocess.run(
                [sys.executable, str(script_path)],
                capture_output=True,
                text=True,
                timeout=30
            )
            if result.returncode == 0:
                print("✓ Schema documentation updated")
            else:
                print(f"⚠ Schema documentation update had warnings: {result.stderr[:200]}")
        except Exception as e:
            # Don't fail init_db() if schema doc update fails
            print(f"⚠ Could not update schema documentation: {e}")
    else:
        print(f"⚠ Schema inspection script not found at {script_path}")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# Cache for initialized tables
_embedding_tables_cache = None


def initialize_embedding_tables():
    """
    Initialize all dynamic embedding tables based on current model config.
    Uses lazy initialization to avoid circular dependencies.
    
    Returns:
        Tuple of (embed_dim, embed_model, embed_table, sentence_table, topic_table, document_table)
    """
    global _embedding_tables_cache
    
    if _embedding_tables_cache is not None:
        return _embedding_tables_cache

    # Use configured embedding dimension (avoids network probe for fast startup)
    # EMBEDDING_DIM env var or provider-specific defaults
    if PREFERENCES.models.embedding_dim:
        embed_dim = PREFERENCES.models.embedding_dim
    else:
        embed_dim = PREFERENCES.models.get_embedding_dim_default()
    
    embed_model = PREFERENCES.models.embedding_model
    
    # Get a session for table creation (db parameter is not actually used in get_or_create_embedding_class)
    db_gen = get_db()
    db_session = next(db_gen)
    
    try:
        embed_table = get_or_create_embedding_class(
            model_name=embed_model,
            version="v1",
            dim=embed_dim,
            db=db_session,
            chunk_type="chunk"
        )
        
        sentence_table = get_or_create_embedding_class(
            model_name=embed_model,
            version="v1",
            dim=embed_dim,
            db=db_session,
            chunk_type="sent"
        )
        
        topic_table = get_or_create_embedding_class(
            model_name=embed_model,
            version="v1",
            dim=embed_dim,
            db=db_session,
            chunk_type="topic"
        )
        
        document_table = get_or_create_embedding_class(
            model_name=embed_model,
            version="v1",
            dim=embed_dim,
            db=db_session,
            chunk_type="doc"
        )
    finally:
        # Clean up the session
        try:
            next(db_gen)
        except StopIteration:
            pass
    
    _embedding_tables_cache = (embed_dim, embed_model, embed_table, sentence_table, topic_table, document_table)
    return _embedding_tables_cache


# Lazy attribute access for embedding tables (Python 3.7+)
def __getattr__(name):
    """Lazy attribute access for embedding tables to avoid circular imports."""
    if name == "EMBED_DIM":
        return initialize_embedding_tables()[0]
    elif name == "EMBED_MODEL":
        return initialize_embedding_tables()[1]
    elif name == "EMBED_TABLE":
        return initialize_embedding_tables()[2]
    elif name == "SENTENCE_TABLE":
        return initialize_embedding_tables()[3]
    elif name == "TOPIC_TABLE":
        return initialize_embedding_tables()[4]
    elif name == "DOCUMENT_TABLE":
        return initialize_embedding_tables()[5]
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
