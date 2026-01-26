# app/schemas.py
from pydantic import BaseModel, Field
from typing import Optional, List, Literal
from datetime import datetime
from uuid import UUID

# ---------- Auth / API keys ----------

class CreateApiKeyRequest(BaseModel):
    email: str = Field(..., description="User email to associate with this key")
    name: Optional[str] = Field(None, description="Optional label for the key (e.g. 'chrome-extension')")


class CreateApiKeyResponse(BaseModel):
    api_key: str = Field(..., description="Raw API key (shown once). Store it securely.")
    user_id: str
    email: str


class WhoAmIResponse(BaseModel):
    user_id: str
    email: str


class ApiKeyInfo(BaseModel):
    id: str
    name: Optional[str] = None
    prefix: str
    key_hint: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ListApiKeysResponse(BaseModel):
    keys: List[ApiKeyInfo]


# ---------- Vaults ----------

class VaultResponse(BaseModel):
    """Response schema for a vault."""
    id: str
    name: str
    owner_id: Optional[str] = None
    is_personal: bool = True
    created_at: datetime
    archived_at: Optional[datetime] = None
    role: Optional[str] = None  # The current user's role in this vault (from membership)
    document_count: Optional[int] = None  # Optional count of documents in the vault

    class Config:
        from_attributes = True


class VaultCreate(BaseModel):
    """Request schema for creating a vault."""
    name: str = Field(..., min_length=1, max_length=255, description="Name of the vault")
    is_personal: bool = Field(default=False, description="Whether this is a personal vault")


class VaultMembershipResponse(BaseModel):
    """Response schema for a vault membership."""
    id: str
    vault_id: str
    user_id: str
    role: str
    created_at: datetime

    class Config:
        from_attributes = True


class VaultListResponse(BaseModel):
    """Response containing user's vaults."""
    vaults: List[VaultResponse]


# --- Ingest payload from extension ---
class IngestPayload(BaseModel):
    url: str
    title: Optional[str] = None
    text: str
    mode: Literal["page", "selection", "note"] = "page"
    tags: Optional[List[str]] = None
    captured_at: Optional[datetime] = None
    # PDF handling - separated for clarity
    pdf_to_parse: Optional[str] = None           # Single PDF URL to parse (when URL is a PDF or arXiv)
    linked_pdf_urls: Optional[List[str]] = None  # URLs of linked PDFs (stored as references only)
    # Legacy field - still accepted for backward compatibility
    pdf_urls: Optional[List[str]] = None         # Deprecated: use pdf_to_parse and linked_pdf_urls

    # ---------- Pydantic response models ----------

class TopicDoc(BaseModel):
    id: UUID
    title: str | None
    url: str
    captured_at: datetime

    class Config:
        from_attributes = True  # Pydantic v2


class Subtopic(BaseModel):
    """A subtopic within a topic (level 1 in the hierarchy)."""
    subtopic_id: str
    title: str
    summary: str | None = None
    level_index: int = 1  # Hierarchy level (1 = subtopic)
    documents: List[TopicDoc]


class Topic(BaseModel):
    """A topic cluster (level 2 in the hierarchy by default)."""
    topic_id: str
    title: str
    summary: str | None = None
    level_index: int = 2  # Hierarchy level (2 = topic, can vary for multi-level)
    documents_count: int
    subtopics: List[Subtopic]


class TopicsResponse(BaseModel):
    """Response containing topics for a time range."""
    time_range_days: int = Field(..., description="Number of days used for the time window")
    topics: List[Topic]


# ---------- Hierarchical 4-Level Topic Models ----------

class ClusterLevel0(BaseModel):
    """Level 0: Finest clusters (cosine sim >= 0.90) - near-duplicates."""
    cluster_id: str
    title: str
    summary: str | None = None
    level_index: int = 0
    documents: List[TopicDoc]


class ClusterLevel1(BaseModel):
    """Level 1: Tightly related subtopics (cosine sim >= 0.80)."""
    cluster_id: str
    title: str
    summary: str | None = None
    level_index: int = 1
    documents_count: int
    children: List[ClusterLevel0]


class ClusterLevel2(BaseModel):
    """Level 2: Topics (cosine sim >= 0.65)."""
    cluster_id: str
    title: str
    summary: str | None = None
    level_index: int = 2
    documents_count: int
    children: List[ClusterLevel1]


class ClusterLevel3(BaseModel):
    """Level 3: Broad categories (cosine sim >= 0.50) - coarsest level."""
    cluster_id: str
    title: str
    summary: str | None = None
    level_index: int = 3
    documents_count: int
    children: List[ClusterLevel2]


class HierarchicalTopicsResponse(BaseModel):
    """
    Response containing hierarchical topics with 4 levels.
    
    Level hierarchy (from coarsest to finest):
    - Level 3: Broad categories (cosine sim >= 0.50)
    - Level 2: Topics (cosine sim >= 0.65)
    - Level 1: Subtopics (cosine sim >= 0.80)
    - Level 0: Finest clusters (cosine sim >= 0.90)
    """
    time_range_days: int = Field(..., description="Number of days used for the time window")
    categories: List[ClusterLevel3] = Field(description="Top-level broad categories")
    
    # Statistics about the clustering
    total_documents: int = 0
    level_stats: dict = Field(default_factory=dict, description="Stats per level: {level: {n_clusters, min_size, max_size}}")

class DocumentDetailResponse(BaseModel):
    id: str
    vault_id: Optional[str] = None
    url: str
    title: Optional[str] = None
    captured_at: Optional[datetime] = None
    text: str
    assigned_topic_id: Optional[str] = None
    assigned_topic_title: Optional[str] = None
    created_by: Optional[str] = None

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


# ---------- Extension OAuth Connect Flow ----------

class ExtensionConnectRequest(BaseModel):
    """Request to initiate extension connection (send verification email)."""
    email: str = Field(..., description="User email address")


class ExtensionConnectResponse(BaseModel):
    """Response after sending verification email."""
    status: str
    message: str
    email: str  # Masked email for confirmation


class ExtensionVerifyRequest(BaseModel):
    """Request to verify email code and get extension token."""
    email: str = Field(..., description="User email address")
    code: str = Field(..., description="6-digit verification code")


class ExtensionTokenInfo(BaseModel):
    """Information about an extension token."""
    id: str
    name: Optional[str] = None
    created_at: datetime
    last_used_at: Optional[datetime] = None
    revoked_at: Optional[datetime] = None


class ExtensionTokensListResponse(BaseModel):
    """List of user's extension tokens (connected devices)."""
    tokens: List[ExtensionTokenInfo]