# AI Context — Graph / Topic Modeling System

When working in the graph-engine repository:

Primary references:
- development_process/implementation_plans/IP_GRAPH.md
- development_process/system_rules/GRAPH_MODULE_SCOPE.md
- development_process/GRAPH_SYSTEM_INTEGRATION_CONTRACT.md

Architecture constraints:
- development_process/system_rules/GRAPH_ARCHITECTURE_RULES.md

System overview:
- README.md
- development_process/graph_architecture_map.html (optional visual map)

Rules:
- Implement one phase at a time
- Do not expand scope beyond the current implementation plan
- Do not introduce new dependencies unless explicitly approved
- Respect the boundaries defined in GRAPH_ARCHITECTURE_RULES.md
- Use CURSOR_CONSTITUTION.md
- All graph code lives in the graph-engine repository, not in the main application repository
- The graph repo must not import or depend on main application code

---

# Repository Boundary

The graph system is implemented in a **separate repository** (`graph-engine`).

It is not an internal module of the main application. It interacts with the main system through a defined integration contract documented in `development_process/GRAPH_SYSTEM_INTEGRATION_CONTRACT.md`.

The graph-engine repository:

* is an importable Python package (`graph_engine`)
* optionally exposes a lightweight API wrapper (service mode)
* uses the same PostgreSQL database as the main application
* writes only to the `graph` schema
* reads from a narrow, approved contract in the main system

---

# Core Graph System Assumptions

The graph-engine repository implements a **vault-aware semantic graph subsystem**.

It must:

- build sparse semantic graphs from document features
- persist graphs per vault and graph version
- support incremental graph updates
- support subgraph extraction
- support cross-vault graph composition
- provide derived graph views for the UI

The graph-engine repository **shares the primary PostgreSQL database** with the main application, writing only to `graph.*` and reading from approved upstream tables.

---

# Canonical Truth

Canonical truth lives in:

- documents
- document-level semantic features
- vault membership and permissions
- persisted sparse graph edges
- graph versions/configurations
- user actions affecting graph structure

Canonical truth **does not live in clusters or trees**.

Clusters, semantic trees, Z arrays, layouts, and labels are **derived artifacts**.

---

# Graph Query Model

Normal application queries must:

1. Select documents using SQL
2. Extract the persisted sparse graph slice in SQL
3. Return node + edge payloads
4. Optionally derive local view structures

Normal queries must **never require rebuilding the full graph**.

---

# Feature Contract

Feature extraction occurs **outside** the graph repository (in the main application's enrichment pipelines).

Feature fusion occurs **inside** the graph repository.

Phase 1 should assume:

- document embeddings exist (required initial layer)
- summary embeddings may exist (optional initial layer)
- no tags, categories, or NER are required

Later phases may add feature layers. The graph system must support **pluggable feature layers** consumed through named references to approved source tables/views.

---

# Vault Graph Model

Each vault has its own internal graph.

Graph persistence includes:

- internal vault edges
- optional cross-vault bridge edges
- graph version metadata

Multi-vault views are **runtime projections**, not merged canonical graphs.

---

# Graph Update Model

Graph updates should follow these principles:

- document insertion triggers local neighborhood updates
- document deletion removes local edges and repairs neighbors
- cross-vault bridge edges may be recalculated independently
- full graph rebuilds are rare and controlled

---

# Derived Graph Views

The graph module must support:

- force graph payloads
- grouped/circle payloads
- optional local hierarchy/Z caches

These artifacts are **derived and replaceable**.

---

# Labeling

Cluster/topic labels are handled by a labeling module within the graph-engine repository.

Graph rendering must **not block on label generation**.

Labels may attach to:

- graph communities
- local groups
- derived hierarchy nodes

---

# Compatibility

Existing semantic tree / orchard / Z artifacts may remain temporarily as:

- compatibility outputs
- migration bridges
- optional derived hierarchy caches

They are **not canonical for the new graph system**.

---

# Performance Rules

PostgreSQL owns:

- persistence
- graph slicing
- subgraph extraction
- version management

Graph-engine Python code owns:

- matrix math
- graph construction
- pruning algorithms
- derived view generation

---

# Implementation Direction

Initial phases focus on:

1. vault graph persistence
2. sparse graph construction from embeddings
3. subgraph extraction
4. graph payload generation

Advanced features (feature layers, labeling, bridge policies) come later.