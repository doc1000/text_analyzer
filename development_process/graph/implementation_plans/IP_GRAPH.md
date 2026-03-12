

# IP_GRAPH.md

# Implementation Plan — Graph / Topic Modeling Module

## Purpose

This implementation plan defines the phased construction of the **vault-aware semantic graph subsystem**.

The graph subsystem must support:

* sparse semantic graphs over documents
* vault-scoped graph isolation
* incremental graph updates
* subgraph extraction
* cross-vault graph composition
* graph-derived visualization payloads
* optional derived hierarchy/Z caches

The plan is structured to allow **safe iterative implementation in Cursor**.

Each phase must be:

* **self-contained**
* **non-breaking**
* **architecturally compliant**
* **testable independently**

---

# Global Implementation Rules

All phases must follow:

* `development_process/graph/system_rules/GRAPH_ARCHITECTURE_RULES.md`
* `development_process/graph/system_rules/GRAPH_AI_CONTEXT.md`
* `development_process/graph/system_rules/GRAPH_MODULE_SCOPE.md`
* `development_process/CURSOR_CONSTITUTION.md`

### Mandatory constraints

1. Implement **one phase at a time**
2. Do not modify files outside the phase scope
3. Do not introduce new dependencies unless required
4. Avoid modifying ingestion or unrelated modules
5. Graph queries must **not require full graph rebuild**
6. Sparse graph must be persisted in PostgreSQL
7. Clusters, trees, layouts, and labels remain **derived artifacts**

---

# Canonical Data Model

The canonical graph state consists of:

* vault graph configuration
* graph version metadata
* sparse graph edges
* cross-vault bridge edges
* document feature references

Derived artifacts include:

* clusters
* Z linkage arrays
* semantic trees
* layouts
* group labels

---

# Phase Overview

| Phase   | Goal                                |
| ------- | ----------------------------------- |
| Phase 1 | Graph foundations                   |
| Phase 2 | Graph persistence + versioning      |
| Phase 3 | Subgraph extraction                 |
| Phase 4 | Incremental graph updates           |
| Phase 5 | Graph views for UI                  |
| Phase 6 | Cross-vault graph composition       |
| Phase 7 | Derived hierarchy / Z compatibility |
| Phase 8 | Feature-layer expansion             |
| Phase 9 | Labeling module                     |

Each phase produces **working functionality**.

---

# Phase 1 — Graph Foundations

## Goal

Create the core graph module structure and implement basic graph construction from embeddings.

## Responsibilities

* implement graph module structure
* retrieve embeddings
* build nearest neighbor relationships
* construct sparse graph
* prepare graph persistence interfaces

## Inputs

* documents
* document embeddings
* vault IDs

## Outputs

* in-memory sparse graph
* graph construction utilities

## Files Allowed to Modify

```
graph_core/
    graph_builder.py
    graph_types.py
    graph_config.py
```

## Deliverables

* kNN graph construction
* configurable neighbor count
* graph builder interface

---

# Phase 2 — Graph Persistence and Versioning

## Goal

Persist graph edges and manage graph versions per vault.

## Responsibilities

* create graph tables
* store graph edges
* store graph version metadata

## Database Objects

```
vault_graph_versions
vault_graph_edges
```

## Required Behaviors

* store edges per vault
* maintain graph version records
* allow graph rebuilds without destroying history

## Files Allowed

```
graph_core/
    graph_repository.py
    graph_version_manager.py
db/migrations/
    graph_tables.sql
```

---

# Phase 3 — Subgraph Extraction

## Goal

Extract graph slices for selected document sets.

## Responsibilities

* induced subgraph queries
* SQL-based node/edge filtering
* efficient payload construction

## Required Behaviors

* fetch nodes
* fetch edges among nodes
* return graph payload

## Files Allowed

```
graph_core/
    subgraph_service.py
```

---

# Phase 4 — Incremental Graph Updates

## Goal

Allow graph maintenance when documents are inserted, updated, or deleted.

## Responsibilities

* local neighbor recalculation
* local edge updates
* local edge pruning

## Required Behaviors

Document insertion:

```
new document
→ find nearest neighbors
→ compute fused similarity
→ insert edges
```

Document deletion:

```
remove node
remove incident edges
repair neighbor edges
```

## Files Allowed

```
graph_core/
    graph_update_service.py
```

---

# Phase 5 — Graph Views for UI

## Goal

Provide graph payloads optimized for UI rendering.

## Responsibilities

* D3 force graph payload
* grouped node payload
* graph slice payloads

## Outputs

```
{
  nodes: [],
  edges: [],
  metadata: {}
}
```

## Files Allowed

```
graph_views/
    force_view_builder.py
    graph_payload_builder.py
```

---

# Phase 6 — Cross-Vault Graph Composition

## Goal

Support graph views combining multiple vaults.

## Responsibilities

* maintain vault graph isolation
* compute bridge edges
* compose graph payloads

## Data Objects

```
bridge_edges
```

## Required Behaviors

Combined view:

```
vault A graph
+ vault B graph
+ bridge edges
```

Internal vault edges must not be modified.

## Files Allowed

```
graph_core/
    graph_composition_service.py
```

---

# Phase 7 — Derived Hierarchy / Z Compatibility

## Goal

Provide optional hierarchy views derived from graph slices.

## Responsibilities

* compute hierarchical clustering
* generate Z linkage arrays
* produce tree payloads

## Notes

These structures are **derived artifacts**.

They must never replace the canonical graph.

## Files Allowed

```
graph_views/
    hierarchy_builder.py
```

---

# Phase 8 — Feature Layer Expansion

## Goal

Support multiple semantic feature layers.

## Examples

* tag vectors
* category probability vectors
* NER/entity profiles
* user feedback signals

## Responsibilities

* layer weighting
* feature fusion
* modular feature providers

## Files Allowed

```
graph_features/
    feature_provider.py
    feature_fusion.py
```

---

# Phase 9 — Labeling Module

## Goal

Generate human-readable labels for graph groups.

## Responsibilities

* group labeling
* label caching
* user override support

## Constraints

Label generation must not block graph rendering.

## Files Allowed

```
graph_labels/
    label_service.py
    label_repository.py
```

---

# Testing Requirements

Each phase must include:

* unit tests
* integration tests
* graph payload verification
* permission-aware graph slicing

---

# Deployment Strategy

Graph rebuild operations must be:

* asynchronous
* vault-scoped
* versioned

---

# Future Extensions

Potential later features include:

* graph database support
* graph ML clustering
* graph-based recommendations
* topic evolution tracking
* graph search indexing

These are **not required for the initial system**.

---

# Definition of Done

A phase is complete when:

* functionality works
* database schema updates are applied
* tests pass
* architecture rules are respected
* no out-of-scope files were modified

---

# Notes for Opus

When generating detailed phase instructions:

* strictly limit file scope
* include migration steps when needed
* include required SQL queries
* include example payload outputs
* include failure cases
* include performance considerations

