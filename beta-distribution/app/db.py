# app/db.py
import os
#import time
#from typing import Optional, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker #, Session
from .models import Base, get_or_create_embedding_class
from .config import PREFERENCES

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://badger:badgerpass@db:5432/badgerdb")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
def ensure_schemas():
    with engine.connect() as conn:
        conn.execute(text("CREATE SCHEMA IF NOT EXISTS embedding"))
        conn.commit()


def init_db():
    """
    Initialize database: create schemas, tables, and generate schema documentation.
    
    After creating tables, automatically runs schema inspection to update DATABASE_SCHEMA.md
    for Cursor/AI reference.
    
    All tables are defined using SQLAlchemy's declarative base, suitable for Base.metadata.create_all(bind=engine).
    Each embedding model gets its own table under the embedding schema and a row in embedding.embedding_model.
    """
    ensure_schemas()
    
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
        Tuple of (embed_dim, embed_model, embed_table, sentence_table, topic_table)
    """
    global _embedding_tables_cache
    
    if _embedding_tables_cache is not None:
        return _embedding_tables_cache
    
    # Ensure database is initialized first (creates embedding.embedding_model table)
    # This is safe to call multiple times - init_db() is idempotent
    # We need to ensure the embedding.embedding_model table exists before
    # register_embedding_model() tries to query it
    init_db()
    
    # Import here to avoid circular dependency
    from .helpers import get_embedding
    
    embed_dim = len(get_embedding("dimension probe"))
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
    finally:
        # Clean up the session
        try:
            next(db_gen)
        except StopIteration:
            pass
    
    _embedding_tables_cache = (embed_dim, embed_model, embed_table, sentence_table, topic_table)
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
    raise AttributeError(f"module '{__name__}' has no attribute '{name}'")
