# app/models.py
import uuid
from sqlalchemy import Column, Text, Float, DateTime, ForeignKey, Integer
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime
from pgvector.sqlalchemy import Vector  # pip install pgvector

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

class Chunk(Base):
    __tablename__ = "chunks"

    id = Column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id = Column(UUID(as_uuid=True),
                         ForeignKey("documents.id", ondelete="CASCADE"),
                         nullable=False)
    chunk_index = Column(Integer, nullable=False)
    chunk_text = Column(Text, nullable=False)

    # match your pgvector dimension, e.g. 1536
    embedding = Column(Vector(1536), nullable=True)

    score_info = Column(Float, nullable=True)
    score_ai_slop = Column(Float, nullable=True)