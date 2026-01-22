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
    pdf_urls: Optional[List[str]] = None  # URLs of embedded PDFs to parse

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
    assigned_topic_id: Optional[str] = None
    assigned_topic_title: Optional[str] = None

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
    chat_model: Optional[str] = None  # Model name for chat (Ollama or OpenAI)
    topic_model: Optional[str] = None  # Model name for topic generation (Ollama)
    embed_model: Optional[str] = None  # Model name for embeddings (Ollama)
    llm_model: Optional[str] = None  # OpenAI model name (when chat_provider is "openai")
    embedding_model: Optional[str] = None  # OpenAI embedding model name (when embedding_provider is "openai")
    openai_api_key: Optional[str] = None  # If provided, will update the API key


class TopicOption(BaseModel):
    """A topic option for dropdown selection."""
    id: str
    title: str
    similarity: float
    document_count: int


class TopicsListResponse(BaseModel):
    """List of topics for selection, sorted by similarity."""
    topics: List[TopicOption]
class TopicAssignmentRequest(BaseModel):
    """Request to update document's topic assignment."""
    topic_id: Optional[str] = None  # UUID of existing topic, or None to clear
    custom_title: Optional[str] = None  # Custom title for new topic
    generate_new: bool = False  # If True, generate a new topic using the model