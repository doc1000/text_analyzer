# Architecture Rules — Graph / Topic Modeling System

## Purpose

The graph-engine repository implements a semantic graph layer for document organization and topic exploration.

This is a **separate repository** from the main application. It is imported as a Python package or optionally exposed as a lightweight API service.

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

Feature enrichment pipelines are **not owned by the graph repository**. They belong to the main application.

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

## 11. Repository Separation

The graph system is implemented in a **separate repository** (`graph-engine`).

The main application repository and the graph repository have distinct ownership boundaries:

* The main application owns documents, vaults, users, permissions, ingestion, parsing, OCR, embedding generation, and feature enrichment pipelines.
* The graph repository owns graph construction, feature fusion, sparse graph persistence, bridge edges, graph versioning, subgraph extraction, graph payload generation, hierarchy caches, and optional labeling.

The graph repository must not take ownership of ingestion, OCR, parsing, authentication, user management, or generic enrichment pipelines.

---

## 12. Database Contract

The graph system uses the **same PostgreSQL database** as the main application.

Write scope:

* `graph.*` — all tables in the graph schema

Read scope (narrow approved contract):

* `public.documents`
* `public.vaults`
* `public.vault_memberships`
* approved embedding tables or views
* approved feature tables or views

The graph system must not read arbitrary application tables.

Recommended read patterns:

* read through stable tables
* read through dedicated views
* use feature-provider adapters

---

## 13. Feature-Layer Contract

Feature extraction happens **outside** the graph repository.

Examples of external feature extraction:

* embeddings
* tag extraction
* category classification
* NER / entity detection
* other enrichment pipelines

Feature fusion happens **inside** the graph repository.

Graph configuration specifies:

* feature layer name
* source table or view
* join key
* similarity metric
* feature weight

The graph system must operate with **only document embeddings initially**. Additional feature layers remain optional.

---

## 14. Execution Modes

Two execution modes are supported:

**Import mode** (preferred initially)

The graph repository is imported as a Python package:

```python
from graph_engine import GraphService
```

**Service mode** (optional)

The graph repository exposes a lightweight API wrapper for graph operations.

Do not redesign the system as a distributed microservice platform.

---

## 15. Graph System Integration Contract

Inputs expected by the graph system:

* document IDs
* vault IDs
* feature layer sources
* graph configuration parameters

Outputs produced by the graph system:

* sparse graph edges
* graph versions
* subgraph payloads
* composed graph payloads
* optional hierarchy caches

Clusters, trees, layouts, and labels are **derived artifacts**, not integration contract outputs.

---

# Repo Boundaries

* documents
* vaults
* vault memberships
* users and permissions
* ingestion, parsing, OCR
* embedding generation
* feature enrichment pipelines
* UI and application workflows

The graph repository owns:

* graph persistence
* graph construction
* feature fusion
* graph slicing
* bridge edge management
* graph versioning
* subgraph extraction
* graph-derived UI payloads
* optional labeling module

The graph repository does not own:

* document ingestion
* OCR
* parsing
* authentication and user management
* embedding generation
* enrichment pipelines
* general application orchestration
* external API services unrelated to graph exploration