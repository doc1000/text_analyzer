# app/config.py
from dataclasses import dataclass, field
from typing import Literal, Optional

# You can extend these later or load them from JSON if you want.
# For now: one central place to tweak your internal tools.

EmbeddingModel = Literal[
    "text-embedding-3-large",
    "text-embedding-3-small",
]

ChatModel = Literal[
    "gpt-4.1-nano",
    "gpt-4.1-mini",
    "gpt-4.1",
    "gpt-4o-mini",
    "gpt-4o",
]
# may need to add something to make sure that EMBED_DIM is consistent

DimReducer = Literal["umap", "none"]
ClusterAlgo = Literal["kmeans"]  # easy to add "hdbscan" later if you want


@dataclass
class ClusteringConfig:
    dim_reducer: DimReducer = "umap"
    use_umap_for_clustering: bool = True  # if False, cluster in original embedding space
    cluster_algo: ClusterAlgo = "kmeans"
    min_docs_for_clustering: int = 6
    max_neighbors: int = 15
    max_components: int = 5
    random_state: int = 42
    k_topics_min: int = 3
    k_topics_max: int = 10
    k_sub_min: int = 2
    k_sub_max: int = 4


@dataclass
class ModelConfig:
    embedding_model: EmbeddingModel = "text-embedding-3-large"
    llm_model: ChatModel = "gpt-4.1-nano"


@dataclass
class Preferences:
    models: ModelConfig = field(default_factory=ModelConfig)
    clustering: ClusteringConfig = field(default_factory=ClusteringConfig)


# Single global preferences object. Import this elsewhere.
PREFERENCES = Preferences()