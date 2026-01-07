# app/config.py
from dataclasses import dataclass, field
from typing import Literal, Optional
import os
# You can extend these later or load them from JSON if you want.
# For now: one central place to tweak your internal tools.
Provider = Literal["openai", "ollama"]

EmbeddingModel = Literal[
    "text-embedding-3-large",
    "text-embedding-3-small", #1536?
    "bge-m3", #1024
    "all-minilm" #324
]

#EmbeddingDimension = Literal[384]  # keep in sync with DB/vector size

ChatModel = Literal[
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
    "gemma3:1b-it-q4_K_M", # supposed to be low latency, light
    "phi3.5-mini-q4km", # midsize - mid CPU latency, mid performance
    "llama3.2:3b", #a bit big for CPU"
]
# may need to add something to make sure that EMBED_DIM is consistent

DimReducer = Literal["pca","umap", "none"]
ClusterAlgo = Literal["kmeans"]  # easy to add "hdbscan" later if you want


@dataclass
class ClusteringConfig:
    dim_reducer: DimReducer = "pca"   # "pca", "umap", or "none"
    use_reducer_for_clustering: bool = True  # was use_umap_for_clustering
    cluster_algo: ClusterAlgo = "kmeans"
    min_docs_for_clustering: int = 6
    max_neighbors: int = 15          # still used if you pick UMAP
    max_components: int = 24
    random_state: int = 42
    k_topics_min: int = 2
    k_topics_max: int = 10
    k_sub_min: int = 1
    k_sub_max: int = 4


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
    batch_size: int = 100              # Texts per API call
    max_batch_size_openai: int = 2048  # OpenAI API limit
    max_batch_size_ollama: int = 100   # Ollama practical limit
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
class OllamaConfig:
    base_url: str = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    chat_model: str = "gemma3:1b-it-q4_K_M" #os.getenv("OLLAMA_CHAT_MODEL","gemma3:1b-it-q4_K_M")
    embed_model: str = "all-minilm" #os.getenv("OLLAMA_EMBED_MODEL", "bge-m3")

@dataclass
class ModelConfig:
    provider: Provider = "openai" #os.getenv("MODEL_PROVIDER", "openai")  # default openai for now
    embedding_model: EmbeddingModel = "text-embedding-3-small" #OllamaConfig.embed_model #"text-embedding-3-small" #os.getenv("OLLAMA_EMBED_MODEL","text-embedding-3-small")
    #embedding_dim: int = 384 #os.getenv("EMBED_DIM_V2", 1536)
    llm_model: ChatModel = "gpt-4.1-nano" #OllamaConfig.chat_model #"gpt-4.1-nano" #os.getenv("OLLAMA_CHAT_MODEL","gpt-4.1-nano")
    ollama: OllamaConfig = field(default_factory=OllamaConfig)


@dataclass
class Preferences:
    models: ModelConfig = field(default_factory=ModelConfig)
    clustering: ClusteringConfig = field(default_factory=ClusteringConfig)
    mmr: MMRConfig = field(default_factory=MMRConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    topic_persistence: TopicPersistenceConfig = field(default_factory=TopicPersistenceConfig)


# Single global preferences object. Import this elsewhere.
PREFERENCES = Preferences()