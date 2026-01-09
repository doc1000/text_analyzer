"""
Maximal Marginal Relevance (MMR) implementation for diverse sentence selection.

MMR balances relevance (similarity to query) with diversity (dissimilarity to already selected items)
to avoid redundant selections while maintaining high relevance.
"""

import numpy as np
from typing import List, Tuple


def cosine_sim(a: np.ndarray, b: np.ndarray) -> float:
    """
    Compute cosine similarity between two vectors.
    
    Args:
        a: First vector (should be normalized for proper cosine similarity)
        b: Second vector (should be normalized for proper cosine similarity)
    
    Returns:
        Cosine similarity in range [-1, 1] (or [0, 1] for normalized vectors)
    """
    return np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10)


def l2_normalize(embeddings: np.ndarray) -> np.ndarray:
    """
    L2-normalize embeddings so that cosine similarity equals dot product.
    
    Args:
        embeddings: Array of shape (n_samples, dim) or (dim,)
    
    Returns:
        Normalized embeddings with L2 norm = 1.0
    """
    if embeddings.ndim == 1:
        norm = np.linalg.norm(embeddings)
        if norm < 1e-10:
            return embeddings
        return embeddings / norm
    
    # Handle 2D arrays
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    # Avoid division by zero
    norms = np.maximum(norms, 1e-10)
    return embeddings / norms


def l2_normalize_vector(vec: np.ndarray) -> np.ndarray:
    """
    L2-normalize a single vector.
    
    Args:
        vec: Vector of shape (dim,) or list
    
    Returns:
        Normalized vector with L2 norm = 1.0
    """
    vec = np.asarray(vec, dtype=float)
    norm = np.linalg.norm(vec)
    if norm < 1e-10:
        return vec
    return vec / norm


def is_normalized(embeddings: np.ndarray, tolerance: float = 1e-3) -> bool:
    """
    Check if embeddings are L2-normalized.
    
    Args:
        embeddings: Array of shape (n_samples, dim) or (dim,)
        tolerance: Acceptable deviation from norm=1.0
    
    Returns:
        True if all vectors have L2 norm ≈ 1.0
    """
    if embeddings.ndim == 1:
        norm = np.linalg.norm(embeddings)
        return abs(norm - 1.0) < tolerance
    
    norms = np.linalg.norm(embeddings, axis=1)
    return np.all(np.abs(norms - 1.0) < tolerance)


def mmr_select(
    embeddings: List[np.ndarray],
    query_embedding: np.ndarray,
    k: int = 10,
    lambda_: float = 0.7,
    normalize: bool = True
) -> List[int]:
    """
    Select k items using Maximal Marginal Relevance (MMR).
    
    MMR iteratively selects items that maximize:
        score = λ * relevance - (1-λ) * redundancy
    
    where:
        - relevance = cosine similarity to query_embedding
        - redundancy = max cosine similarity to any already-selected item
        - λ controls the trade-off (higher λ = more relevance, less diversity)
    
    Args:
        embeddings: List of embeddings, one per candidate item
        query_embedding: Reference embedding (e.g., cluster centroid)
        k: Number of items to select
        lambda_: Trade-off parameter in [0, 1]
                 λ=1.0: pure relevance (no diversity)
                 λ=0.0: pure diversity (no relevance)
                 Recommended: 0.65-0.75
        normalize: If True, L2-normalize all embeddings before MMR
    
    Returns:
        List of k indices into embeddings (or fewer if len(embeddings) < k)
    
    Example:
        >>> sentences = [emb1, emb2, emb3, ...]
        >>> centroid = compute_centroid(doc_embeddings)
        >>> selected_idx = mmr_select(sentences, centroid, k=10, lambda_=0.7)
        >>> selected_sentences = [sentences[i] for i in selected_idx]
    """
    if not embeddings:
        return []
    
    # Convert to numpy arrays if needed
    embeddings_array = np.array([np.asarray(e, dtype=float) for e in embeddings])
    query_embedding = np.asarray(query_embedding, dtype=float)
    
    # Normalize if requested
    if normalize:
        embeddings_array = l2_normalize(embeddings_array)
        query_embedding = l2_normalize_vector(query_embedding)
    
    n_candidates = len(embeddings)
    k = min(k, n_candidates)  # Can't select more than available
    
    selected_indices = []
    candidates = list(range(n_candidates))
    
    while len(selected_indices) < k and candidates:
        best_idx = None
        best_score = -1e9
        
        for i in candidates:
            # Relevance: similarity to query
            relevance = cosine_sim(embeddings_array[i], query_embedding)
            
            # Redundancy: max similarity to any selected item
            if selected_indices:
                redundancy = max(
                    cosine_sim(embeddings_array[i], embeddings_array[j])
                    for j in selected_indices
                )
            else:
                redundancy = 0.0
            
            # MMR score
            score = lambda_ * relevance - (1 - lambda_) * redundancy
            
            if score > best_score:
                best_score = score
                best_idx = i
        
        if best_idx is not None:
            selected_indices.append(best_idx)
            candidates.remove(best_idx)
    
    return selected_indices


def mmr_select_with_scores(
    embeddings: List[np.ndarray],
    query_embedding: np.ndarray,
    k: int = 10,
    lambda_: float = 0.7,
    normalize: bool = True
) -> List[Tuple[int, float]]:
    """
    Select k items using MMR and return their scores.
    
    Same as mmr_select but returns list of (index, score) tuples.
    
    Args:
        embeddings: List of embeddings, one per candidate item
        query_embedding: Reference embedding (e.g., cluster centroid)
        k: Number of items to select
        lambda_: Trade-off parameter in [0, 1]
        normalize: If True, L2-normalize all embeddings before MMR
    
    Returns:
        List of (index, mmr_score) tuples sorted by selection order
    """
    if not embeddings:
        return []
    
    # Convert to numpy arrays if needed
    embeddings_array = np.array([np.asarray(e, dtype=float) for e in embeddings])
    query_embedding = np.asarray(query_embedding, dtype=float)
    
    # Normalize if requested
    if normalize:
        embeddings_array = l2_normalize(embeddings_array)
        query_embedding = l2_normalize_vector(query_embedding)
    
    n_candidates = len(embeddings)
    k = min(k, n_candidates)
    
    selected_with_scores = []
    candidates = list(range(n_candidates))
    
    while len(selected_with_scores) < k and candidates:
        best_idx = None
        best_score = -1e9
        
        for i in candidates:
            relevance = cosine_sim(embeddings_array[i], query_embedding)
            
            if selected_with_scores:
                redundancy = max(
                    cosine_sim(embeddings_array[i], embeddings_array[j])
                    for j, _ in selected_with_scores
                )
            else:
                redundancy = 0.0
            
            score = lambda_ * relevance - (1 - lambda_) * redundancy
            
            if score > best_score:
                best_score = score
                best_idx = i
        
        if best_idx is not None:
            selected_with_scores.append((best_idx, best_score))
            candidates.remove(best_idx)
    
    return selected_with_scores


def compute_centroid(embeddings: List[np.ndarray], normalize: bool = True) -> np.ndarray:
    """
    Compute the centroid (mean) of a set of embeddings.
    
    Args:
        embeddings: List of embeddings
        normalize: If True, L2-normalize the result
    
    Returns:
        Centroid embedding
    """
    if not embeddings:
        raise ValueError("Cannot compute centroid of empty embedding list")
    
    embeddings_array = np.array([np.asarray(e, dtype=float) for e in embeddings])
    centroid = np.mean(embeddings_array, axis=0)
    
    if normalize:
        centroid = l2_normalize_vector(centroid)
    
    return centroid
