# AI Context — Graph / Topic Modeling Module

When working in this module:

Primary references:
- development_process/implementation_plans/IP_GRAPH.md
- development_process/graph_module/GRAPH_MODULE_SCOPE.md

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

---

# Core Graph System Assumptions

This module implements a **vault-aware semantic graph subsystem**.

It must:

- build sparse semantic graphs from document features
- persist graphs per vault and graph version
- support incremental graph updates
- support subgraph extraction
- support cross-vault graph composition
- provide derived graph views for the UI

This module **shares the primary PostgreSQL database**.

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

# Initial Feature Assumptions

Phase 1 should assume:

- document embeddings exist
- summary embeddings may exist
- no tags, categories, or NER are required

Later phases may add feature layers.

Graph core must support **pluggable feature layers**.

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

Cluster/topic labels are handled by a separate labeling module.

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

Python/application logic owns:

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