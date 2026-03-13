# Graph / Topic Modeling System Scope

## Purpose

The graph system is implemented as a **separate repository** (`graph-engine`), not as an internal module of the main application.

It provides an importable Python package (`graph_engine`) that builds and maintains semantic graphs over documents.

Graphs must support:

- vault-scoped document relationships
- incremental updates
- multi-vault composition
- graph exploration in the UI
- topic discovery

---

# Repository Boundary

This scope document describes components within the **graph-engine repository**.

* Feature extraction pipelines remain in the main application repository.
* Feature fusion lives inside the graph-engine repository.
* The graph system reads from approved upstream tables/views and writes only to `graph.*`.
* See `development_process/GRAPH_SYSTEM_INTEGRATION_CONTRACT.md` for the full integration contract.

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

## graph_engine.graph_core

Location: `src/graph_engine/graph_core/`

Responsibilities:

- graph construction
- sparse graph persistence
- incremental updates
- bridge edge construction
- subgraph extraction
- graph version management

---

## graph_engine.features

Location: `src/graph_engine/features/`

Responsibilities:

- consume named feature layers from approved source tables/views
- normalize feature layers
- perform feature fusion (weighted multi-layer similarity)
- support pluggable semantic features

Initial implementation:

- document embeddings (required)
- summary embeddings (optional)

Future layers (consumed from main application pipelines):

- tags
- category distributions
- NER/entity vectors
- user feedback constraints

Feature extraction remains in the main application. Feature fusion is owned by the graph-engine repository.

---

## graph_engine.views

Location: `src/graph_engine/views/`

Responsibilities:

- convert graph slices into UI payloads

Examples:

- force graph payloads
- grouped/circle payloads
- local hierarchy payloads

---

## graph_engine.labels

Location: `src/graph_engine/labels/`

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

The graph system must support:

- vault graph retrieval
- subgraph retrieval
- composed multi-vault view graphs
- derived visualization payloads

---

# Out of Scope

The graph-engine repository does not own or implement:

- document ingestion
- OCR
- parsing
- user authentication and management
- general application orchestration
- embedding generation
- tag extraction pipelines
- NER extraction pipelines
- classifier pipelines
- generic enrichment pipelines
- semantic tree replacement

Feature extraction pipelines remain in the main application repository. The graph system consumes their outputs through approved tables/views.