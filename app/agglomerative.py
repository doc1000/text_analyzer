# app/agglomerative.py
"""
Agglomerative hierarchical clustering module for document embeddings.

This module provides functions to:
1. Build a linkage tree from document embeddings using cosine distance
2. Cut the tree at multiple thresholds to create a 4-level hierarchy
3. Extract nested cluster structures for topic organization

The hierarchy goes from finest (level 0, highest similarity required) 
to coarsest (level 3, lowest similarity required).
"""

import numpy as np
from typing import List, Dict, Tuple, Optional
from uuid import UUID
from .config import PREFERENCES

# Lazy-load heavy scipy/sklearn libraries (~10s import time each)
_scipy_linkage = None
_scipy_fcluster = None
_scipy_squareform = None
_sklearn_cosine_distances = None

def get_scipy_linkage():
    """Lazy-load scipy linkage function."""
    global _scipy_linkage
    if _scipy_linkage is None:
        from scipy.cluster.hierarchy import linkage
        _scipy_linkage = linkage
    return _scipy_linkage

def get_scipy_fcluster():
    """Lazy-load scipy fcluster function."""
    global _scipy_fcluster
    if _scipy_fcluster is None:
        from scipy.cluster.hierarchy import fcluster
        _scipy_fcluster = fcluster
    return _scipy_fcluster

def get_scipy_squareform():
    """Lazy-load scipy squareform function."""
    global _scipy_squareform
    if _scipy_squareform is None:
        from scipy.spatial.distance import squareform
        _scipy_squareform = squareform
    return _scipy_squareform

def get_cosine_distances():
    """Lazy-load sklearn cosine_distances function."""
    global _sklearn_cosine_distances
    if _sklearn_cosine_distances is None:
        from sklearn.metrics.pairwise import cosine_distances
        _sklearn_cosine_distances = cosine_distances
    return _sklearn_cosine_distances


def build_linkage_tree(
    embeddings: np.ndarray,
    linkage_method: str = None
) -> np.ndarray:
    """
    Build a hierarchical clustering linkage tree from embeddings using cosine distance.
    
    Args:
        embeddings: Array of shape (n_samples, dim) containing document embeddings
        linkage_method: Linkage method ('average', 'single', 'complete'). 
                       Defaults to config setting.
    
    Returns:
        Linkage matrix Z of shape (n_samples-1, 4) as returned by scipy.cluster.hierarchy.linkage
        Each row contains [idx1, idx2, distance, sample_count]
    
    Note:
        Uses cosine distance (1 - cosine_similarity) as the distance metric.
        Average linkage is recommended for balanced, semantically meaningful clusters.
    """
    if linkage_method is None:
        linkage_method = PREFERENCES.agglomerative.linkage_method
    
    n_samples = embeddings.shape[0]
    
    if n_samples < 2:
        raise ValueError(f"Need at least 2 samples for clustering, got {n_samples}")
    
    # Lazy-load heavy libraries
    cosine_distances = get_cosine_distances()
    squareform = get_scipy_squareform()
    linkage = get_scipy_linkage()
    
    # Compute pairwise cosine distances: D[i,j] = 1 - cosine_similarity(i, j)
    D = cosine_distances(embeddings)
    
    # Ensure diagonal is exactly 0 (numerical precision)
    np.fill_diagonal(D, 0)
    
    # Clip to [0, 2] range (cosine distance theoretical range)
    D = np.clip(D, 0, 2)
    
    # Convert to condensed distance matrix for scipy linkage
    D_condensed = squareform(D, checks=False)
    
    # Build the linkage tree
    Z = linkage(D_condensed, method=linkage_method)
    
    return Z


def cut_tree_at_thresholds(
    Z: np.ndarray,
    thresholds: List[float] = None
) -> Dict[int, np.ndarray]:
    """
    Cut the linkage tree at multiple distance thresholds to produce cluster assignments.
    
    Args:
        Z: Linkage matrix from build_linkage_tree()
        thresholds: List of cosine distance thresholds (default from config).
                   Should be ordered from smallest (finest clusters) to largest (coarsest).
    
    Returns:
        Dictionary mapping level index to cluster label array.
        {0: labels_level0, 1: labels_level1, 2: labels_level2, 3: labels_level3}
        
        Labels are 1-indexed as returned by scipy.cluster.hierarchy.fcluster.
    
    Example:
        >>> Z = build_linkage_tree(embeddings)
        >>> labels_by_level = cut_tree_at_thresholds(Z)
        >>> level_0_labels = labels_by_level[0]  # Finest clusters
        >>> level_3_labels = labels_by_level[3]  # Coarsest clusters
    """
    if thresholds is None:
        thresholds = PREFERENCES.agglomerative.level_thresholds
    
    labels_by_level = {}
    fcluster = get_scipy_fcluster()
    
    for level_idx, threshold in enumerate(thresholds):
        # fcluster with criterion='distance' cuts the tree at the given distance
        # Returns 1-indexed cluster labels
        labels = fcluster(Z, t=threshold, criterion='distance')
        labels_by_level[level_idx] = labels
    
    return labels_by_level


def get_nested_cluster_structure(
    labels_by_level: Dict[int, np.ndarray],
    doc_ids: List[UUID] = None
) -> Dict[int, Dict[int, List[int]]]:
    """
    Extract the nested cluster structure showing which documents belong to each cluster at each level.
    
    Args:
        labels_by_level: Dictionary from cut_tree_at_thresholds()
        doc_ids: Optional list of document UUIDs (same order as embeddings).
                If provided, returns UUIDs instead of indices.
    
    Returns:
        Nested dictionary: {level_idx: {cluster_label: [doc_indices_or_ids]}}
    
    Example:
        >>> structure = get_nested_cluster_structure(labels_by_level)
        >>> structure[0][1]  # Documents in cluster 1 at level 0 (finest)
        [0, 1, 2]  # Document indices
    """
    structure = {}
    
    for level_idx, labels in labels_by_level.items():
        clusters = {}
        for doc_idx, cluster_label in enumerate(labels):
            cluster_label = int(cluster_label)  # Convert from numpy int
            if cluster_label not in clusters:
                clusters[cluster_label] = []
            
            if doc_ids is not None:
                clusters[cluster_label].append(doc_ids[doc_idx])
            else:
                clusters[cluster_label].append(doc_idx)
        
        structure[level_idx] = clusters
    
    return structure


def get_cluster_hierarchy(
    labels_by_level: Dict[int, np.ndarray]
) -> Dict[int, Dict[int, List[int]]]:
    """
    Compute parent-child relationships between clusters at adjacent levels.
    
    At each level, determines which coarser (higher level) cluster each finer cluster belongs to.
    
    Args:
        labels_by_level: Dictionary from cut_tree_at_thresholds()
    
    Returns:
        Dictionary: {level_idx: {fine_cluster_label: coarse_cluster_label}}
        Maps each cluster at level N to its parent cluster at level N+1.
    
    Example:
        >>> hierarchy = get_cluster_hierarchy(labels_by_level)
        >>> parent = hierarchy[0][1]  # Parent of cluster 1 at level 0
    """
    hierarchy = {}
    levels = sorted(labels_by_level.keys())
    
    for i, level in enumerate(levels[:-1]):  # All levels except the coarsest
        next_level = levels[i + 1]
        
        fine_labels = labels_by_level[level]
        coarse_labels = labels_by_level[next_level]
        
        # For each fine cluster, find its parent coarse cluster
        # (all docs in a fine cluster should map to the same coarse cluster)
        parent_map = {}
        
        for fine_label in np.unique(fine_labels):
            # Get indices of docs in this fine cluster
            doc_indices = np.where(fine_labels == fine_label)[0]
            
            # Get the coarse cluster label(s) for these docs
            coarse_for_docs = coarse_labels[doc_indices]
            
            # All should be the same (hierarchical property)
            parent_label = int(coarse_for_docs[0])
            parent_map[int(fine_label)] = parent_label
        
        hierarchy[level] = parent_map
    
    return hierarchy


def compute_cluster_centroids(
    embeddings: np.ndarray,
    labels: np.ndarray,
    normalize: bool = True
) -> Dict[int, np.ndarray]:
    """
    Compute the centroid embedding for each cluster.
    
    Args:
        embeddings: Array of shape (n_samples, dim)
        labels: Cluster labels (1-indexed from fcluster)
        normalize: If True, L2-normalize the centroids
    
    Returns:
        Dictionary mapping cluster label to centroid embedding
    """
    centroids = {}
    
    for label in np.unique(labels):
        mask = labels == label
        cluster_embeddings = embeddings[mask]
        centroid = np.mean(cluster_embeddings, axis=0)
        
        if normalize:
            norm = np.linalg.norm(centroid)
            if norm > 1e-10:
                centroid = centroid / norm
        
        centroids[int(label)] = centroid
    
    return centroids


def cluster_embeddings_hierarchical(
    embeddings: np.ndarray,
    doc_ids: List[UUID] = None,
    thresholds: List[float] = None,
    linkage_method: str = None
) -> Tuple[Dict[int, np.ndarray], Dict[int, Dict[int, List]], np.ndarray]:
    """
    Main entry point: cluster embeddings hierarchically and return all structures.
    
    This is a convenience function that combines building the tree, cutting at thresholds,
    and extracting the cluster structure.
    
    Args:
        embeddings: Array of shape (n_samples, dim) containing document embeddings
        doc_ids: Optional list of document UUIDs (same order as embeddings)
        thresholds: List of cosine distance thresholds (default from config)
        linkage_method: Linkage method (default from config)
    
    Returns:
        Tuple of:
        - labels_by_level: {level_idx: cluster_labels_array}
        - structure: {level_idx: {cluster_label: [doc_ids_or_indices]}}
        - linkage_matrix: The scipy linkage matrix Z
    
    Example:
        >>> labels, structure, Z = cluster_embeddings_hierarchical(embeddings, doc_ids)
        >>> # Get documents in cluster 1 at level 2 (topics level)
        >>> docs_in_cluster = structure[2][1]
    """
    # Build the linkage tree
    Z = build_linkage_tree(embeddings, linkage_method)
    
    # Cut at thresholds to get labels at each level
    labels_by_level = cut_tree_at_thresholds(Z, thresholds)
    
    # Extract cluster structure with doc IDs
    structure = get_nested_cluster_structure(labels_by_level, doc_ids)
    
    return labels_by_level, structure, Z


def get_cluster_stats(
    labels_by_level: Dict[int, np.ndarray]
) -> Dict[int, Dict[str, int]]:
    """
    Compute statistics about clusters at each level.
    
    Args:
        labels_by_level: Dictionary from cut_tree_at_thresholds()
    
    Returns:
        Dictionary: {level_idx: {'n_clusters': int, 'min_size': int, 'max_size': int, 'avg_size': float}}
    """
    stats = {}
    
    for level_idx, labels in labels_by_level.items():
        unique_labels, counts = np.unique(labels, return_counts=True)
        
        stats[level_idx] = {
            'n_clusters': len(unique_labels),
            'min_size': int(counts.min()),
            'max_size': int(counts.max()),
            'avg_size': float(counts.mean()),
            'total_docs': int(len(labels))
        }
    
    return stats


def filter_small_clusters(
    labels: np.ndarray,
    min_size: int = None
) -> Tuple[np.ndarray, set]:
    """
    Identify clusters that are too small and should be merged or filtered.
    
    Args:
        labels: Cluster labels array
        min_size: Minimum cluster size (default from config)
    
    Returns:
        Tuple of:
        - Set of cluster labels that meet minimum size
        - Set of cluster labels that are too small
    """
    if min_size is None:
        min_size = PREFERENCES.agglomerative.min_cluster_size
    
    unique_labels, counts = np.unique(labels, return_counts=True)
    
    valid_clusters = set(unique_labels[counts >= min_size])
    small_clusters = set(unique_labels[counts < min_size])
    
    return valid_clusters, small_clusters
