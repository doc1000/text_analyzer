# app/schemas.py
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
from uuid import UUID

# --- Ingest payload from extension ---
class IngestPayload(BaseModel):
    url: str
    title: Optional[str] = None
    text: str
    mode: Literal["page", "selection", "note"] = "page"
    tags: Optional[List[str]] = None
    score_info: Optional[float] = None
    score_ai_slop: Optional[float] = None
    captured_at: Optional[datetime] = None

    # ---------- Pydantic response models ----------

class TopicDoc(BaseModel):
    id: UUID
    title: str | None
    url: str
    score_info: float | None = None
    score_ai_slop: float | None = None
    captured_at: datetime

    class Config:
        from_attributes = True  # Pydantic v2


class Subtopic(BaseModel):
    subtopic_id: str
    title: str
    summary: str | None = None
    documents: List[TopicDoc]


class Topic(BaseModel):
    topic_id: str
    title: str
    summary: str | None = None
    documents_count: int
    subtopics: List[Subtopic]


class TopicsResponse(BaseModel):
    time_range_days: int = Field(..., description="Number of days used for the time window")
    topics: List[Topic]

class DocumentDetailResponse(BaseModel):
    id: str
    url: str
    title: Optional[str] = None
    captured_at: Optional[datetime] = None
    score_info: Optional[float] = None
    score_ai_slop: Optional[float] = None
    text: str

class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None