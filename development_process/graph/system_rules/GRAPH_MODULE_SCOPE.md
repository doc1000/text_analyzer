# Graph / Topic Modeling Module Scope

## Purpose

Create a modular subsystem that builds and maintains semantic graphs over documents.

Graphs must support:

- vault-scoped document relationships
- incremental updates
- multi-vault composition
- graph exploration in the UI
- topic discovery

---

# Canonical Graph State

Canonical graph state consists of:

- vault graph configuration
- graph version metadata
- sparse graph edges
- bridge edges
- document feature references

---

# Core System Components

## graph_core

Responsibilities:

- graph construction
- sparse graph persistence
- incremental updates
- bridge edge construction
- subgraph extraction
- graph version management

---

## graph_features

Responsibilities:

- expose document feature vectors
- normalize feature layers
- support pluggable semantic features

Initial implementation:

- document embeddings
- summary embeddings

Future layers:

- tags
- category distributions
- NER/entity vectors
- user feedback constraints

---

## graph_views

Responsibilities:

- convert graph slices into UI payloads

Examples:

- force graph payloads
- grouped/circle payloads
- local hierarchy payloads

---

## graph_labels

Responsibilities:

- optional cluster/topic labeling
- label caching
- user overrides

Label generation must not block graph rendering.

---

# Graph Persistence Model

Per vault:

- internal nodes
- internal edges
- graph version metadata

Cross-vault:

- bridge edges

Derived:

- local cluster caches
- hierarchy/Z caches
- layout caches

---

# Initial Graph Algorithm

Phase 1 graph build should:

1. retrieve document embeddings
2. compute nearest neighbors
3. fuse feature similarities
4. build sparse kNN graph
5. optionally prune edges
6. persist graph edges

---

# Incremental Updates

When documents change:

- update local neighborhoods
- adjust affected edges
- mark derived caches stale

Avoid full rebuilds when possible.

---

# UI Integration

Graph module must support:

- vault graph retrieval
- subgraph retrieval
- composed multi-vault view graphs
- derived visualization payloads

---

# Out of Scope

This module does not initially implement:

- tag extraction
- NER extraction
- classifier pipelines
- full semantic labeling
- semantic tree replacement

These may be added later.