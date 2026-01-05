# app/models.py
import uuid
from sqlalchemy import (
    Column, Text, Float, DateTime,
    ForeignKey, Integer, Index, BigInteger,
    String, text
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.types import UserDefinedType
from sqlalchemy.orm import Session
import re
from datetime import datetime
from pgvector.sqlalchemy import Vector  # pip install pgvector
from .config import PREFERENCES

Base = declarative_base()

class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    full_text = Column(Text, nullable=False)
    score_info = Column(Float, nullable=True)
    score_ai_slop = Column(Float, nullable=True)
    captured_at = Column(DateTime, default=datetime.utcnow)
_ = """
class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True),
                         ForeignKey("documents.id", ondelete="CASCADE"),
                         nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)

    # match your pgvector dimension, e.g. 1536
    EMBED_DIM = 1536 #PREFERENCES.models.embedding_dim
    embedding = Column(Vector(EMBED_DIM), nullable=True)

    score_info = Column(Float, nullable=True)
    score_ai_slop = Column(Float, nullable=True)

# ... keep your existing engine / SessionLocal / init_db / get_db as-is ...
"""

class EmbeddingModel(Base):
    __tablename__ = "embedding_model"
    __table_args__ = {"schema": "embedding"}

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    model_name = Column(String, nullable=False)
    version = Column(String, nullable=False)
    dimensions = Column(Integer, nullable=False)
    table_location = Column(String, nullable=False)
    loaded_at = Column(
        DateTime(timezone=True),
        server_default=text("now()"),
        nullable=False,
    )

    __mapper_args__ = {
        "eager_defaults": True,
    }

class Vector(UserDefinedType):
    cache_ok = True # tell SQLAlchemy it’s safe to cache
    def __init__(self, dim: int):
        self.dim = dim

    def get_col_spec(self, **kw):
        # e.g. "vector(512)"
        return f"vector({self.dim})"

def make_safe_table_name(model_name: str, version: str, dim: int) -> str:
    base = f"{model_name}_{version}_{dim}"
    base = base.lower()
    base = re.sub(r"[^a-z0-9]+", "_", base).strip("_")
    return base

def register_embedding_model(
    session: Session,
    model_name: str,
    version: str,
    dim: int,
) -> EmbeddingModel:
    table_name = make_safe_table_name(model_name, version, dim)
    table_location = f"embedding.{table_name}"

    existing = (
        session.query(EmbeddingModel)
        .filter_by(model_name=model_name, version=version)
        .one_or_none()
    )
    if existing:
        if existing.dimensions != dim:
            raise ValueError(
                f"Model {model_name} v{version} already registered with "
                f"dim={existing.dimensions}, requested dim={dim}"
            )
        return existing

    meta = EmbeddingModel(
        model_name=model_name,
        version=version,
        dimensions=dim,
        table_location=table_location,
    )
    session.add(meta)
    session.commit()
    session.refresh(meta)
    return meta


def get_or_create_embedding_class(model_name: str, version: str, dim: int, db: "get_db()"):
    table_name = make_safe_table_name(model_name, version, dim)  # -> "all_minilm_v1_384"
    full_key = f"embedding.{table_name}"  # schema-qualified key in metadata.tables

    # If table already exists in metadata, reuse it
    if full_key in Base.metadata.tables:
        table_obj = Base.metadata.tables[full_key]

        # If you already created a class, reuse it instead of creating a new one
        for mapper in Base.registry.mappers:
            if mapper.local_table is table_obj:
                return mapper.class_

        # Otherwise, define a new ORM class bound to the existing Table
        cls = type(
            f"Embedding_{table_name}",
            (Base,),
            {"__table__": table_obj},
        )
        return full_key, cls

    # Otherwise define a new mapped class + table
    # (only runs the first time for this name)


    class Vector(UserDefinedType):
        cache_ok = True
        def __init__(self, dim: int):
            self.dim = dim
        def get_col_spec(self, **kw):
            return f"vector({self.dim})"

    class_name = f"Embedding_{table_name}"
    attrs = {
        "__tablename__": table_name,
        "__table_args__": (
            # schema
            {"schema": "embedding"},
        ),
        "id": Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
        "document_id": Column(UUID(as_uuid=True),
                         ForeignKey("documents.id", ondelete="CASCADE"),
                         nullable=False),
        "chunk_index": Column(Integer, nullable=False),
        "chunk_text": Column(Text, nullable=False),
        "embedding": Column(Vector(dim), nullable=False),
        "created_at": Column(
            DateTime(timezone=True),
            server_default=text("now()"),
            nullable=False,
        ),
    }

    cls = type(class_name, (Base,), attrs)

    Index(
        f"{table_name}_embedding_ivfflat_idx",
        cls.__table__.c.embedding,
        postgresql_using="ivfflat",
        postgresql_ops={"embedding": "vector_cosine_ops"},
        postgresql_with={"lists": "100"},
    )

 # 3) Insert a row into embedding.embedding_model if not present
    db_gen = db
    Session = next(db_gen)
    meta = register_embedding_model(
    session= Session,
    model_name= model_name,
    version= version,
    dim = dim
    )

    return cls
