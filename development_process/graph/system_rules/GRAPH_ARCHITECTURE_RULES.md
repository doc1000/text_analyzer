# Architecture Rules — Graph / Topic Modeling Module

## Purpose

This module implements a semantic graph layer for document organization and topic exploration.

Responsibilities:

- build sparse semantic graphs
- persist graph state
- support incremental updates
- support cross-vault graph composition
- provide graph-derived UI payloads

---

# Architectural Principles

## 1. Sparse Graph Is Canonical

Canonical relational state consists of:

- document features
- graph build configuration
- sparse graph edges
- graph versions
- user structural actions

Clusters, trees, layouts, and labels are **derived artifacts**.

---

## 2. Build Once, Derive Many Views

Sparse graph construction is expensive.

The system should:

- persist graph state
- derive multiple views from that state

Derived views include:

- subgraphs
- local hierarchies
- community clusters
- visualization layouts

---

## 3. SQL Persists and Slices

PostgreSQL must own:

- document storage
- graph persistence
- graph versions
- graph slicing
- subgraph extraction

Application code must own:

- graph construction math
- pruning algorithms
- derived view generation

---

## 4. Normal Queries Must Be Cheap

Normal requests must:

- query graph slices
- not rebuild graphs
- not load full graph blobs unnecessarily

Graph rebuilds are **controlled operations**.

---

## 5. Vault Graph Isolation

Each vault has an independent graph.

Vault graphs contain:

- internal nodes
- internal edges
- graph version metadata

Combined views across vaults are **runtime projections**.

---

## 6. Bridge Edges Are Separate

Edges connecting vaults must be stored separately.

Bridge edges must not modify internal vault graph structure unless explicitly permitted.

---

## 7. Feature Layers Are Modular

Graph construction should accept multiple feature layers.

Examples:

- embeddings
- summary embeddings
- tags
- category distributions
- NER/entity profiles
- user intent signals

Feature enrichment pipelines are **not owned by graph_core**.

---

## 8. Incremental Updates Must Be Local

Document additions/deletions should update the graph locally.

Full graph rebuilds should be rare.

---

## 9. Labeling Is Independent

Label generation is a separate subsystem.

Graph rendering must not depend on label availability.

---

## 10. Derived Structures Are Replaceable

Derived structures include:

- clusters
- semantic trees
- Z linkage caches
- visualization layouts

These artifacts may be rebuilt at any time.

---

# Repo Boundaries

This module owns:

- graph persistence
- graph construction
- graph slicing
- bridge edge management
- graph versioning
- graph-derived UI payloads

This module does not own:

- document ingestion
- OCR
- enrichment pipelines
- workflow orchestration
- external API services unrelated to graph exploration