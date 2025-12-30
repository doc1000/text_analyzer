# app/db.py
import os
import time
from typing import Optional, Tuple
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, Session
from .models import Base
from .helpers import get_embedding
from .config import PREFERENCES

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+psycopg2://badger:badgerpass@db:5432/badgerdb")

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

def init_db():
    Base.metadata.create_all(bind=engine)

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ... keep your existing engine / SessionLocal / init_db / get_db as-is ...

def migrate_embeddings(
    batch_size: int = 128,
    sleep_secs: float = 0.0,
    verbose_every: int = 1000,
) -> Tuple[int, int]:
    """
    Migration that:
      1) Drops + recreates chunks_rollback as a snapshot of chunks
      2) Redefines chunks.embedding column to vector(<new_dim>) where new_dim is inferred from current embedder
      3) Backfills chunks.embedding from chunks.chunk_text where embedding is NULL

    Returns: (processed_count, remaining_count)

    NOTE:
    - This rewrites schema and will lock 'chunks' during ALTER TABLE statements.
    - Any index on chunks.embedding will be dropped and must be recreated afterward.
    - After migration, your SQLAlchemy model Chunk.embedding dimension must match the DB schema.
    """

    # Determine current embedding dimension from your configured embedder
    probe_vec = get_embedding("dimension probe")
    embed_dim = len(probe_vec)

    # For logging (optional; helpful for reproducibility)
    # Your config as uploaded only has embedding_model; if you add provider/ollama config later,
    # you can print those too.
    embedding_model = getattr(PREFERENCES.models, "embedding_model", None)
    print(f"[migrate_embeddings] embedding_model={embedding_model} inferred_dim={embed_dim}")

    # Get a session using the same pattern as get_db()
    db_gen = get_db()
    db: Session = next(db_gen)

    try:
        # 1) Create rollback snapshot
        _create_chunks_staging(db)
        _create_chunks_rollback(db)

        # 2) Redefine chunks.embedding column to match embed_dim
        _redefine_embedding_column(db, embed_dim)

        # 3) Backfill
        processed = 0
        remaining = _count_remaining(db)
        print(f"[migrate_embeddings] rows remaining: {remaining}")

        while True:
            batch = _fetch_batch(db, batch_size)
            if not batch:
                break

            for chunk_id, chunk_text in batch:
                try:
                    vec = get_embedding(chunk_text or "")
                    if len(vec) != embed_dim:
                        raise ValueError(
                            f"Dim mismatch for chunk {chunk_id}: got {len(vec)} expected {embed_dim}"
                        )
                    _update_row(db, chunk_id, vec)
                    processed += 1
                except Exception as e:
                    # Continue on error; you can change to `raise` if you prefer strict fail-fast
                    print(f"[migrate_embeddings][WARN] chunk {chunk_id} failed: {e}")

                if sleep_secs:
                    time.sleep(sleep_secs)

                if verbose_every and processed % verbose_every == 0:
                    remaining = _count_remaining(db)
                    print(f"[migrate_embeddings] processed={processed} remaining={remaining}")

        remaining = _count_remaining(db)
        print(f"[migrate_embeddings] DONE processed={processed} remaining={remaining}")
        print("[migrate_embeddings] rollback table preserved as chunks_rollback")

        return processed, remaining
    except Exception as e:
        print("error: ",e)

    else:
        # ensure full count of rows are updated before changing Chunks table
        _update_chunks_on_success(db)
    
    finally:
        # Ensure generator cleanup (closes session)
        try:
            next(db_gen)
        except StopIteration:
            pass


def _create_chunks_rollback(db: Session) -> None:
    """
    Drop chunks_rollback if it exists, then snapshot chunks into it.
    """
    db.execute(text("DROP TABLE IF EXISTS chunks_rollback;"))
    db.execute(text("CREATE TABLE chunks_rollback AS SELECT * FROM chunks;"))
    db.commit()

def _create_chunks_staging(db: Session) -> None:
    """
    Drop chunks_rollback if it exists, then snapshot chunks into it.
    """
    db.execute(text("DROP TABLE IF EXISTS chunks_staging;"))
    db.execute(text("CREATE TABLE chunks_staging AS SELECT * FROM chunks;"))
    db.commit()

def _redefine_embedding_column(db: Session, dim: int) -> None:
    """
    Drop and recreate chunks.embedding as vector(dim).
    This clears old embeddings from chunks (but preserved in chunks_rollback).
    """
    # IMPORTANT: this will drop indexes/constraints referencing embedding (if any)
    db.execute(text("ALTER TABLE chunks_staging DROP COLUMN IF EXISTS embedding;"))
    db.execute(text(f"ALTER TABLE chunks_staging ADD COLUMN embedding vector({dim});"))
    db.commit()


def _fetch_batch(db: Session, limit: int):
    """
    Fetch (id, chunk_text) rows where embedding is NULL.
    Returns list[tuple[str, str]] where id is text to simplify binding.
    """
    rows = db.execute(
        text(
            """
            SELECT id::text AS id, chunk_text
            FROM chunks_staging
            WHERE embedding IS NULL
            ORDER BY id
            LIMIT :limit
            """
        ),
        {"limit": limit},
    ).fetchall()
    return [(r[0], r[1]) for r in rows]


def _update_row(db: Session, chunk_id: str, vec) -> None:
    """
    Update one row's embedding.
    """
    # Format as pgvector literal: [0.1,0.2,...]
    #vec_txt = "[" + ",".join(f"{x:.8f}" for x in vec) + "]"

    db.execute(
        text(
            """
            UPDATE chunks_staging
            SET embedding = :vec
            WHERE id::text = :id
            """
        ),
        {"vec": vec, "id": chunk_id},
    )
    db.commit()


def _count_remaining(db: Session) -> int:
    return int(
        db.execute(text("SELECT COUNT(*) FROM chunks_staging WHERE embedding IS NULL;")).scalar()
    )

def _update_chunks_on_success(db: Session) -> None:
    """determines if staging has same count of non-null embeddings as chunks"""
    stage_count = int(
        db.execute(text("SELECT COUNT(*) FROM chunks_staging WHERE embedding IS NOT NULL;")).scalar()
    )
    chunk_count = int(
        db.execute(text("SELECT COUNT(*) FROM chunks WHERE embedding IS NOT NULL;")).scalar()
    )
    if stage_count >= chunk_count:
        db.execute(text("DROP TABLE IF EXISTS chunks;"))
        db.execute(text("CREATE TABLE chunks AS SELECT * FROM chunks_staging;"))
        db.execute(text("DROP TABLE IF EXISTS chunks_staging;"))
        db.commit()
    else:
        print("staging has non-null embeddings - chunks not updated")