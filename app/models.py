# app/models.py
import uuid
from sqlalchemy import Column, Text, Float, DateTime
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.ext.declarative import declarative_base
from datetime import datetime

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
