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
from typing import Literal
from pgvector.sqlalchemy import Vector as pgVector # pip install pgvector
#from .config import PREFERENCES

Base = declarative_base()

class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    url = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    full_text = Column(Text, nullable=False)
    captured_at = Column(DateTime, default=datetime.utcnow)
    # Assigned topic - stores the UUID of the topic this document belongs to
    # References the dynamically-created TOPIC_TABLE (no FK constraint due to dynamic table)
    assigned_topic_id = Column(UUID(as_uuid=True), nullable=True)
    # Denormalized topic title for quick access without joins
    assigned_topic_title = Column(Text, nullable=True)


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

from sqlalchemy.types import UserDefinedType
from pgvector.sqlalchemy import Vector as PgVector  # name it PgVector to avoid clash


class VectorComparator(UserDefinedType.Comparator):
    """Comparator that exposes pgvector distance methods."""

    def cosine_distance(self, other):
        """Cosine distance: 1 - cosine_similarity. Range [0, 2]."""
        return self.op('<=>', return_type=Float)(other)

    def l2_distance(self, other):
        """Euclidean (L2) distance."""
        return self.op('<->', return_type=Float)(other)

    def max_inner_product(self, other):
        """Negative inner product (for ordering by max inner product)."""
        return self.op('<#>', return_type=Float)(other)


class Vector(UserDefinedType):
    cache_ok = True
    comparator_factory = VectorComparator

    def __init__(self, dim: int):
        self.dim = dim
        # use the pgvector type internally
        self._inner = PgVector(dim)

    def get_col_spec(self, **kw):
        # delegate to pgvector’s type, which emits "vector(<dim>)"
        return self._inner.compile(kw.get("dialect")) if kw.get("dialect") else f"vector({self.dim})"

    # Optional: bind/column expression passthrough if you need them
    def bind_processor(self, dialect):
        return self._inner.bind_processor(dialect)

    def result_processor(self, dialect, coltype):
        return self._inner.result_processor(dialect, coltype)


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


def get_or_create_embedding_class(model_name: str, version: str,
     dim: int, db: "get_db()",chunk_type: Literal["chunk","sent","topic"] = "chunk"):
    """get or create the chunk or sentence embeddings table.  will be model name for chunks
    will append sent_/topic_ to model name for sentences/topics"""

    
    if chunk_type == "chunk":
        model_prefixed=model_name
    elif chunk_type == "sent":
        model_prefixed = chunk_type + "_" + model_name
        chunk_table = make_safe_table_name(model_name, version, dim)
    elif chunk_type == "topic":
        model_prefixed = chunk_type + "_" + model_name
    else:
        raise ValueError(f"Invalid chunk type: {chunk_type}")

    table_name = make_safe_table_name(model_prefixed, version, dim)  # -> "all_minilm_v1_384"
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

    class_name = f"Embedding_{table_name}"
    # for chunk type table, we have document_id, chunk_index, chunk_text, embedding, created_at
    # for sent type table, we have chunk_id, sent_index, sent_text, embedding, created_at
    # for topic type table, we have parent_id, level_index, title_text, embedding, created_at
    attrs = {
        "__tablename__": table_name,
        "__table_args__": (
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
    if chunk_type == "sent":
        attrs = {
            "__tablename__": table_name,
            "__table_args__": (
                {"schema": "embedding"},
            ),
            "id": Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
            "chunk_id": Column(UUID(as_uuid=True),
                            ForeignKey(f"embedding.{chunk_table}.id", ondelete="CASCADE"),
                            nullable=False),
            "sent_index": Column(Integer, nullable=False),
            "sent_text": Column(Text, nullable=False),
            "embedding": Column(Vector(dim), nullable=False),
            "created_at": Column(
                DateTime(timezone=True),
                server_default=text("now()"),
                nullable=False,
            ),
        }

    if chunk_type == "topic":
        attrs = {
            "__tablename__": table_name,
            "__table_args__": (
                {"schema": "embedding"},
            ),
            "id": Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
            "parent_id": Column(UUID(as_uuid=True), nullable=True),
            "level_index": Column(Integer, nullable=False),
            "title_text": Column(Text, nullable=False),
            "summary_text": Column(Text, nullable=True),
            "document_count": Column(Integer, nullable=False),
            "embedding": Column(Vector(dim), nullable=False),
            "last_matched_at": Column(DateTime(timezone=True), nullable=True),
            "match_count": Column(Integer, default=0),
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
    #db_gen = db
    #Session = next(db_gen)
    Session = db
    meta = register_embedding_model(
    session= Session,
    model_name= model_name,
    version= version,
    dim = dim
    )

    return cls
