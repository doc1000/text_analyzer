# app/config.py
from dataclasses import dataclass, field, asdict
from typing import Literal, Optional, List
import os
import json
from pathlib import Path
# You can extend these later or load them from JSON if you want.
# For now: one central place to tweak your internal tools.
PROVIDERS = ("openai", "ollama", "huggingface")
Provider = Literal[*PROVIDERS]
# Model alias mapping: internal name -> provider-specific model IDs
# This ensures the same underlying model is used regardless of provider
EMBEDDING_MODEL_ALIASES = {
    "all-minilm": {
        "ollama": "all-minilm",
        "huggingface": "all-MiniLM-L6-v2",  # Used with dedicated endpoint
        "dim": 384,
    },
    "bge-m3": {
        "ollama": "bge-m3",
        "huggingface": "BAAI/bge-m3",
        "dim": 1024,
    },
}

EmbeddingModel = Literal[
    "text-embedding-3-large",
    "text-embedding-3-small", #1536?
    "bge-m3", #1024
    "all-minilm" #324
    "bge_small_en" #512
]

#EmbeddingDimension = Literal[384]  # keep in sync with DB/vector size

ChatModel = Literal[
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
    "gemma3:1b-it-q4_K_M", # supposed to be low latency, light (~700MB)
    "phi3.5-mini-q4km", # midsize - mid CPU latency, mid performance (~3.8GB)
    "phi3.5:3.8b-mini-instruct-q2_K", # smaller phi3.5 variant (~1.9GB)
    "llama3.2:3b", # base llama3.2 (~2.0GB)
    "llama3.2:3b", # llama3.2 3B - good balance (~2.0GB)
]
# may need to add something to make sure that EMBED_DIM is consistent

DimReducer = Literal["pca", "umap", "none", "incrPCA"]
ClusterAlgo = Literal["kmeans", "agglomerative"]  # kmeans or agglomerative hierarchical
LinkageMethod = Literal["average", "single", "complete"]  # for agglomerative clustering


@dataclass
class ClusteringConfig:
    dim_reducer: DimReducer = "incrPCA"
    use_reducer_for_clustering: bool = True  # was use_umap_for_clustering
    cluster_algo: ClusterAlgo = "kmeans"
    min_docs_for_clustering: int = 1
    max_neighbors: int = 15          # still used if you pick UMAP
    max_components: int = 24
    random_state: int = 42
    k_topics_min: int = 2
    k_topics_max: int = 10
    k_topics_recluster: int = 50
    k_sub_min: int = 1
    k_sub_max: int = 10
    # Fraction of chunk-count growth that triggers a full PCA retrain (default 10%)
    pca_retrain_threshold: float = 0.10


@dataclass
class MMRConfig:
    """Configuration for MMR-based sentence selection for topic titles."""
    k_sentences: int = 10              # Number of sentences to select
    lambda_param: float = 0.7          # Relevance vs diversity trade-off (0.65-0.75 recommended)
    max_prompt_chars: int = 1500       # Maximum chars to send to LLM
    min_sentences_for_mmr: int = 5     # Minimum sentences needed to use MMR
    max_docs_to_process: int = 50      # Max docs per cluster to avoid overwhelming MMR


@dataclass
class EmbeddingConfig:
    """Configuration for batch embedding processing."""
    batch_size: int = 50              # Texts per API call
    max_batch_size_openai: int = 2048  # OpenAI API limit
    max_batch_size_ollama: int = 50   # Ollama practical limit
    retry_failed_batches: bool = True   # Retry failed batches individually


@dataclass
class TopicPersistenceConfig:
    """Configuration for topic persistence and matching."""
    similarity_threshold_topic: float = 0.85      # Min similarity to match top-level topic
    similarity_threshold_subtopic: float = 0.85   # Min similarity to match subtopic
    persist_topics: bool = True                   # Enable/disable persistence
    persist_subtopics: bool = True                # Enable/disable subtopic persistence
    max_existing_topics_to_check: int = 1000      # Limit for similarity search

@dataclass
class QueryConfig:
    """Configuration for query answer generation."""
    max_prompt_chars: int = 2000                 # Maximum chars in prompt sent to LLM (prevents timeouts with small models)
    max_context_chars: int = 1500                 # Maximum chars for context portion (leaves room for prompt template)


@dataclass
class AgglomerativeConfig:
    """Configuration for agglomerative hierarchical clustering with 3 cosine DISTANCE levels.
    
    The hierarchy goes from finest (level 0, lowest distance) to coarsest (level 2, highest distance).
    Documents are clustered bottom-up using cosine distance and average linkage.
    
    NOTE: All thresholds are COSINE DISTANCE (distance = 1 - similarity).
    To convert: similarity = 1 - distance, distance = 1 - similarity
    """
    enabled: bool = True                          # Toggle between KMeans and agglomerative
    linkage_method: LinkageMethod = "average"     # Linkage method: average, single, or complete
    
    # Cosine DISTANCE thresholds for each level (distance = 1 - similarity)
    # Level 0 (finest): very similar content, tightly related
    level_0_distance: float = 0.6                 # distance <= 0.5 means similarity >= 0.5
    # Level 1: topics - related content
    level_1_distance: float = 0.8              # distance <= 0.75 means similarity >= 0.25
    # Level 2 (coarsest): super-topics - broad categories
    level_2_distance: float = 0.9                # distance <= 0.95 means similarity >= 0.05
    
    min_cluster_size: int = 1                     # Minimum docs per cluster
    use_document_summaries: bool = True           # Use document summaries for clustering input
    
    @property
    def level_thresholds(self) -> list:
        """Return distance thresholds as a list ordered from finest to coarsest."""
        return [
            self.level_0_distance,
            self.level_1_distance,
            self.level_2_distance,
        ]
    
    def distance_to_similarity(self, distance: float) -> float:
        """Convert cosine distance to cosine similarity."""
        return 1.0 - distance
    
    def level_0_similarity(self) -> float:
        """Get Level 0 threshold as similarity (for topic matching)."""
        return 1.0 - self.level_0_distance


@dataclass
class SummaryConfig:
    """Configuration for LLM-based summary generation during ingestion."""
    generate_chunk_summaries: bool = True         # Generate summaries for each chunk
    generate_document_summaries: bool = True      # Aggregate chunk summaries into doc summary
    max_chunk_summary_chars: int = 300            # Max chars per chunk summary
    max_doc_summary_chars: int = 600              # Max chars for document summary
    max_chunks_for_doc_summary: int = 10          # Max chunk summaries to include in doc summary prompt
    summary_batch_size: int = 5                   # Chunks to summarize per LLM batch call


@dataclass
class DeduplicationConfig:
    """Configuration for scheduled deduplication tasks."""
    enabled: bool = field(default_factory=lambda: os.getenv("DEDUP_ENABLED", "true").lower() in ("1", "true", "yes"))
    schedule_times: List[str] = field(default_factory=lambda: ["00:00", "12:00"])  # UTC times in HH:MM format
    similarity_threshold: float = 0.92  # Cosine similarity threshold for semantic deduplication


@dataclass
class EmailConfig:
    """Configuration for email sending (verification codes)."""
    smtp_host: str = os.getenv("SMTP_HOST", "")
    smtp_port: int = int(os.getenv("SMTP_PORT", "587"))
    smtp_user: str = os.getenv("SMTP_USER", "")
    smtp_pass: str = os.getenv("SMTP_PASS", "")
    from_email: str = os.getenv("SMTP_FROM_EMAIL", "noreply@vaultbubbles.com")
    from_name: str = os.getenv("SMTP_FROM_NAME", "VaultBubble")
    # Dev mode: if True, codes are logged to console instead of sent
    dev_mode: bool = os.getenv("EMAIL_DEV_MODE", "true").lower() in ("1", "true", "yes")


@dataclass
class OllamaConfig:
    base_url: str = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:3b")
    topic_model: str = os.getenv("OLLAMA_TOPIC_MODEL", "gemma3:1b-it-q4_K_M")
    embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "all-minilm")


@dataclass
class HuggingFaceConfig:
    """Configuration for HuggingFace Inference API (cloud embeddings)."""
    api_token: str = os.getenv("HUGGINGFACE_API_TOKEN", "")
    embed_model: str = os.getenv("HUGGINGFACE_EMBED_MODEL", "all-MiniLM-L6-v2")
    # HuggingFace endpoint URL. Supports two modes:
    #
    # 1. Dedicated Inference Endpoint (recommended):
    #    Set HUGGINGFACE_EMBED_URL to your endpoint URL directly.
    #    Example: https://xxxxx.us-east-1.aws.endpoints.huggingface.cloud
    #    The model is baked into the endpoint, so embed_model is just for reference.
    #
    # 2. Shared Inference API (may have routing issues):
    #    Use {model} placeholder: https://router.huggingface.co/hf-inference/models/{model}
    embed_url: str = os.getenv(
        "HUGGINGFACE_EMBED_URL",
        "https://h52cfilt2ukml8vc.us-east-1.aws.endpoints.huggingface.cloud",  # Dedicated endpoint for all-MiniLM-L6-v2
    )

@dataclass
class ModelConfig:
    # Separate providers for different model types (read from env vars for cloud deployment)
    # Defaults to OpenAI for chat/topics (works both local and cloud)
    chat_provider: Provider = field(default_factory=lambda: os.getenv("CHAT_PROVIDER", "openai"))
    embedding_provider: Provider = field(default_factory=lambda: os.getenv("EMBEDDING_PROVIDER", "huggingface"))
    topic_provider: Provider = field(default_factory=lambda: os.getenv("TOPIC_PROVIDER", "openai"))
    
    embedding_model: EmbeddingModel = field(default_factory=lambda: os.getenv("EMBEDDING_MODEL", "all-minilm"))
    llm_model: ChatModel = field(default_factory=lambda: os.getenv("OPENAI_CHAT_MODEL", "gpt-4o-mini"))
    
    # Embedding dimension override (avoids network probe at startup for fast boot)
    # If not set, will use sensible defaults based on provider/model
    embedding_dim: Optional[int] = field(default_factory=lambda: 
        int(os.getenv("EMBEDDING_DIM", "0")) or None
    )
    
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    huggingface: HuggingFaceConfig = field(default_factory=HuggingFaceConfig)
    
    def get_embedding_dim_default(self) -> int:
        """Sensible defaults per provider/model if EMBEDDING_DIM not set."""
        if self.embedding_provider == "huggingface":
            # BAAI/bge-small-en-v1.5 (most common for HF deployments)
            return 384
        elif self.embedding_provider == "openai":
            if "large" in str(self.embedding_model):
                return 3072  # text-embedding-3-large
            return 1536  # text-embedding-3-small default
        else:  # ollama
            model_lower = str(self.ollama.embed_model).lower()
            if "minilm" in model_lower:
                return 384
            elif "bge-m3" in model_lower:
                return 1024
            return 384  # Conservative default


@dataclass
class Preferences:
    models: ModelConfig = field(default_factory=ModelConfig)
    clustering: ClusteringConfig = field(default_factory=ClusteringConfig)
    agglomerative: AgglomerativeConfig = field(default_factory=AgglomerativeConfig)
    summary: SummaryConfig = field(default_factory=SummaryConfig)
    mmr: MMRConfig = field(default_factory=MMRConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    topic_persistence: TopicPersistenceConfig = field(default_factory=TopicPersistenceConfig)
    query: QueryConfig = field(default_factory=QueryConfig)
    deduplication: DeduplicationConfig = field(default_factory=DeduplicationConfig)
    email: EmailConfig = field(default_factory=EmailConfig)


# Settings file path - store in the app directory
SETTINGS_FILE = Path(__file__).parent.parent / "settings.json"


def save_settings_to_file(prefs: Preferences):
    """Save user-modifiable settings to a JSON file."""
    # Only save model-related settings that users can change
    settings_data = {
        "models": {
            "chat_provider": prefs.models.chat_provider,
            "embedding_provider": prefs.models.embedding_provider,
            "topic_provider": prefs.models.topic_provider,
            "llm_model": prefs.models.llm_model,
            "embedding_model": prefs.models.embedding_model,
            "ollama": {
                "chat_model": prefs.models.ollama.chat_model,
                "topic_model": prefs.models.ollama.topic_model,
                "embed_model": prefs.models.ollama.embed_model,
                "base_url": prefs.models.ollama.base_url,
            }
        },
        "clustering": {
            "dim_reducer": prefs.clustering.dim_reducer,
            "min_docs_for_clustering": prefs.clustering.min_docs_for_clustering,
            "max_components": prefs.clustering.max_components,
            "agglomerative_enabled": prefs.agglomerative.enabled,
            "linkage_method": prefs.agglomerative.linkage_method,
            "level_0_distance": prefs.agglomerative.level_0_distance,
            "level_1_distance": prefs.agglomerative.level_1_distance,
            "level_2_distance": prefs.agglomerative.level_2_distance,
            "min_cluster_size": prefs.agglomerative.min_cluster_size,
            "use_document_summaries": prefs.agglomerative.use_document_summaries,
            "similarity_threshold_topic": prefs.topic_persistence.similarity_threshold_topic,
            "similarity_threshold_subtopic": prefs.topic_persistence.similarity_threshold_subtopic,
        },
    }
    
    try:
        with open(SETTINGS_FILE, 'w') as f:
            json.dump(settings_data, f, indent=2)
    except Exception as e:
        # Log error but don't fail - settings are optional
        import logging
        logging.getLogger(__name__).warning(f"Failed to save settings: {e}")


def load_settings_from_file(prefs: Preferences):
    """Load user-modifiable settings from a JSON file."""
    if not SETTINGS_FILE.exists():
        return  # No saved settings, use defaults
    
    try:
        with open(SETTINGS_FILE, 'r') as f:
            settings_data = json.load(f)
        
        # Update model settings if present
        if "models" in settings_data:
            models_data = settings_data["models"]
            
            if "chat_provider" in models_data:
                prefs.models.chat_provider = models_data["chat_provider"]
            if "embedding_provider" in models_data:
                prefs.models.embedding_provider = models_data["embedding_provider"]
            if "topic_provider" in models_data:
                prefs.models.topic_provider = models_data["topic_provider"]
            if "llm_model" in models_data:
                prefs.models.llm_model = models_data["llm_model"]
            if "embedding_model" in models_data:
                prefs.models.embedding_model = models_data["embedding_model"]
            
            if "ollama" in models_data:
                ollama_data = models_data["ollama"]
                if "chat_model" in ollama_data:
                    prefs.models.ollama.chat_model = ollama_data["chat_model"]
                if "topic_model" in ollama_data:
                    prefs.models.ollama.topic_model = ollama_data["topic_model"]
                if "embed_model" in ollama_data:
                    prefs.models.ollama.embed_model = ollama_data["embed_model"]
                if "base_url" in ollama_data:
                    prefs.models.ollama.base_url = ollama_data["base_url"]

        # Load clustering settings if present (consolidated: clustering + agglomerative + topic_persistence)
        if "clustering" in settings_data:
            c = settings_data["clustering"]
            if "dim_reducer" in c:
                prefs.clustering.dim_reducer = c["dim_reducer"]
            if "min_docs_for_clustering" in c:
                prefs.clustering.min_docs_for_clustering = int(c["min_docs_for_clustering"])
            if "max_components" in c:
                prefs.clustering.max_components = int(c["max_components"])
            if "agglomerative_enabled" in c:
                prefs.agglomerative.enabled = bool(c["agglomerative_enabled"])
            if "linkage_method" in c:
                prefs.agglomerative.linkage_method = c["linkage_method"]
            if "level_0_distance" in c:
                prefs.agglomerative.level_0_distance = float(c["level_0_distance"])
            if "level_1_distance" in c:
                prefs.agglomerative.level_1_distance = float(c["level_1_distance"])
            if "level_2_distance" in c:
                prefs.agglomerative.level_2_distance = float(c["level_2_distance"])
            if "min_cluster_size" in c:
                prefs.agglomerative.min_cluster_size = int(c["min_cluster_size"])
            if "use_document_summaries" in c:
                prefs.agglomerative.use_document_summaries = bool(c["use_document_summaries"])
            if "similarity_threshold_topic" in c:
                prefs.topic_persistence.similarity_threshold_topic = float(c["similarity_threshold_topic"])
            if "similarity_threshold_subtopic" in c:
                prefs.topic_persistence.similarity_threshold_subtopic = float(c["similarity_threshold_subtopic"])

        # Legacy: load from separate agglomerative section if present (for backward compatibility)
        if "agglomerative" in settings_data:
            a = settings_data["agglomerative"]
            if "enabled" in a:
                prefs.agglomerative.enabled = bool(a["enabled"])
            if "linkage_method" in a:
                prefs.agglomerative.linkage_method = a["linkage_method"]
            if "level_0_distance" in a:
                prefs.agglomerative.level_0_distance = float(a["level_0_distance"])
            if "level_1_distance" in a:
                prefs.agglomerative.level_1_distance = float(a["level_1_distance"])
            if "level_2_distance" in a:
                prefs.agglomerative.level_2_distance = float(a["level_2_distance"])
            if "min_cluster_size" in a:
                prefs.agglomerative.min_cluster_size = int(a["min_cluster_size"])
            if "use_document_summaries" in a:
                prefs.agglomerative.use_document_summaries = bool(a["use_document_summaries"])

    except Exception as e:
        # Log error but don't fail - use defaults if file is corrupted
        import logging
        logging.getLogger(__name__).warning(f"Failed to load settings: {e}")


# Single global preferences object. Import this elsewhere.
PREFERENCES = Preferences()

# Load persisted settings on startup
load_settings_from_file(PREFERENCES)