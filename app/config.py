# app/config.py
from dataclasses import dataclass, field
from typing import Literal, Optional
import os
# You can extend these later or load them from JSON if you want.
# For now: one central place to tweak your internal tools.
Provider = Literal["openai", "ollama"]

EmbeddingModel = Literal[
    "text-embedding-3-large",
    "text-embedding-3-small",
]

#EmbeddingDimension = Literal[384]  # keep in sync with DB/vector size

ChatModel = Literal[
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
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
    max_components: int = 20
    random_state: int = 42
    k_topics_min: int = 2
    k_topics_max: int = 10
    k_sub_min: int = 1
    k_sub_max: int = 4

@dataclass
class OllamaConfig:
    base_url: str = os.getenv("OLLAMA_BASE_URL", "http://ollama:11434")
    chat_model: str = os.getenv("OLLAMA_CHAT_MODEL", "llama3.2:3b")
    embed_model: str = os.getenv("OLLAMA_EMBED_MODEL", "all-minilm")

@dataclass
class ModelConfig:
    provider: Provider = os.getenv("MODEL_PROVIDER", "openai")  # default openai for now
    embedding_model: EmbeddingModel = os.getenv("OLLAMA_EMBED_MODEL","text-embedding-3-small")
    embedding_dim: int = os.getenv("EMBED_DIM_V2", 1536)
    llm_model: ChatModel = os.getenv("OLLAMA_CHAT_MODEL","gpt-4.1-nano")
    ollama: OllamaConfig = field(default_factory=OllamaConfig)


@dataclass
class Preferences:
    models: ModelConfig = field(default_factory=ModelConfig)
    clustering: ClusteringConfig = field(default_factory=ClusteringConfig)


# Single global preferences object. Import this elsewhere.
PREFERENCES = Preferences()