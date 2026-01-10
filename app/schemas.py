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
    captured_at: Optional[datetime] = None

    # ---------- Pydantic response models ----------

class TopicDoc(BaseModel):
    id: UUID
    title: str | None
    url: str
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
    text: str

class DocumentUpdateRequest(BaseModel):
    title: Optional[str] = None
    text: Optional[str] = None

class SettingsResponse(BaseModel):
    chat_model: str
    topic_model: str
    embedding_model: str
    chat_provider: str
    embedding_provider: str
    topic_provider: str
    has_openai_key: bool  # Whether API key is set (without revealing it)

class SettingsUpdateRequest(BaseModel):
    chat_provider: Optional[Literal["openai", "ollama"]] = None
    embedding_provider: Optional[Literal["openai", "ollama"]] = None
    topic_provider: Optional[Literal["openai", "ollama"]] = None
    openai_api_key: Optional[str] = None  # If provided, will update the API key