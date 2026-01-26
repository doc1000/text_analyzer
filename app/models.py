# app/models.py
import uuid
from sqlalchemy import (
    Column, Text, Float, DateTime,
    ForeignKey, Integer, Index, BigInteger,
    String, text, Boolean, UniqueConstraint
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

class User(Base):
    __tablename__ = "users"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(Text, nullable=False, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class ApiKey(Base):
    __tablename__ = "api_keys"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # SHA256 hash of (pepper + raw_key). Never store raw keys.
    key_hash = Column(String(64), nullable=False, unique=True, index=True)

    # Helpful metadata (non-sensitive)
    prefix = Column(String(32), nullable=False, default="vb_live_")
    name = Column(Text, nullable=True)
    key_hint = Column(String(16), nullable=True)  # e.g., last 6 chars (optional)

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class ExtensionToken(Base):
    """OAuth-style tokens for browser extension authentication."""
    __tablename__ = "extension_tokens"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)

    # SHA256 hash of (pepper + raw_token). Never store raw tokens.
    token_hash = Column(String(64), nullable=False, unique=True, index=True)

    # Device/browser name for display, e.g., "Chrome on Doster-PC"
    name = Column(Text, nullable=True)

    # Permission scopes (default: ingest only)
    scopes = Column(Text, nullable=True, default="ingest")  # Comma-separated for simplicity

    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    last_used_at = Column(DateTime, nullable=True)
    revoked_at = Column(DateTime, nullable=True)


class ExtensionVerificationCode(Base):
    """Temporary verification codes for extension OAuth flow."""
    __tablename__ = "extension_verification_codes"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email = Column(Text, nullable=False, index=True)
    code = Column(String(6), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    expires_at = Column(DateTime, nullable=False)
    used_at = Column(DateTime, nullable=True)  # Set when code is used


class Vault(Base):
    """Knowledge container - the unit of sharing, permissions, and billing."""
    __tablename__ = "vaults"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name = Column(Text, nullable=False)
    owner_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True)
    is_personal = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    archived_at = Column(DateTime, nullable=True)


class VaultMembership(Base):
    """Permission control plane - all access checks flow through this table."""
    __tablename__ = "vault_memberships"
    __table_args__ = (
        UniqueConstraint('vault_id', 'user_id', name='uq_vault_user'),
        Index('idx_vault_memberships_user', 'user_id'),
        Index('idx_vault_memberships_vault', 'vault_id'),
    )

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vault_id = Column(UUID(as_uuid=True), ForeignKey("vaults.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role = Column(Text, nullable=False, default="owner")  # owner, admin, editor, viewer
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class Document(Base):
    __tablename__ = "documents"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    vault_id = Column(UUID(as_uuid=True), ForeignKey("vaults.id", ondelete="CASCADE"), nullable=True, index=True)
    created_by = Column(UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    url = Column(Text, nullable=False)
    title = Column(Text, nullable=True)
    
    # Text storage - separated for display vs NLP
    captured_text = Column(Text, nullable=True)   # What extension captured (displayed to user)
    extracted_text = Column(Text, nullable=True)  # Trafilatura output (for NLP/embeddings, optional)
    full_text = Column(Text, nullable=True)       # Deprecated - kept for migration compatibility
    
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

    # NOTE: In multi-machine deployments (Fly), two instances can race on first boot
    # and insert duplicate metadata rows. We tolerate that by selecting the newest row
    # and (optionally) cleaning up older duplicates.
    existing_rows = (
        session.query(EmbeddingModel)
        .filter_by(model_name=model_name, version=version)
        .order_by(EmbeddingModel.id.desc())
        .all()
    )
    if existing_rows:
        newest = existing_rows[0]
        # Ensure consistent dimensions across duplicates (and across new requests)
        for row in existing_rows:
            if row.dimensions != dim:
                raise ValueError(
                    f"Model {model_name} v{version} already registered with "
                    f"dim={row.dimensions}, requested dim={dim}"
                )
        # Cleanup duplicates (best-effort)
        if len(existing_rows) > 1:
            try:
                ids_to_delete = [r.id for r in existing_rows[1:]]
                session.query(EmbeddingModel).filter(EmbeddingModel.id.in_(ids_to_delete)).delete(
                    synchronize_session=False
                )
                session.commit()
            except Exception:
                session.rollback()
        return newest

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
     dim: int, db: "get_db()",chunk_type: Literal["chunk","sent","topic","doc"] = "chunk"):
    """get or create the chunk, sentence, topic, or document embeddings table.
    
    Table types:
    - chunk: Document chunks with text and embeddings
    - sent: Sentence-level embeddings within chunks
    - topic: Topic cluster centroids with titles and summaries
    - doc: Document-level summaries and average embeddings
    
    Will append type prefix to model name for non-chunk tables."""

    
    if chunk_type == "chunk":
        model_prefixed=model_name
    elif chunk_type == "sent":
        model_prefixed = chunk_type + "_" + model_name
        chunk_table = make_safe_table_name(model_name, version, dim)
    elif chunk_type == "topic":
        model_prefixed = chunk_type + "_" + model_name
    elif chunk_type == "doc":
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
    # Chunk table: document_id, chunk_index, chunk_text, summary_text, embedding, created_at
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
        "summary_text": Column(Text, nullable=True),  # LLM-generated chunk summary
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
        # Topic table: parent_id, level_index, title_text, summary_text, document_count, embedding
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
    
    if chunk_type == "doc":
        # Document embedding table: document_id, summary_text, embedding
        # Stores document-level summaries (aggregated from chunk summaries) and average embeddings
        attrs = {
            "__tablename__": table_name,
            "__table_args__": (
                {"schema": "embedding"},
            ),
            "id": Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4),
            "document_id": Column(UUID(as_uuid=True),
                            ForeignKey("documents.id", ondelete="CASCADE"),
                            nullable=False, unique=True),  # One doc embedding per document
            "summary_text": Column(Text, nullable=True),  # LLM-generated document summary
            "embedding": Column(Vector(dim), nullable=False),  # Average of chunk embeddings
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
