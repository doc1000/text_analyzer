# Graph System Integration Contract

This document defines the contract between the **main application repository** and the **graph-engine repository**.

It must be kept consistent with:

* `development_process/implementation_plans/IP_GRAPH.md`
* `development_process/system_rules/GRAPH_ARCHITECTURE_RULES.md`
* `development_process/system_rules/GRAPH_MODULE_SCOPE.md`

---

# System Ownership

## Main Application Repository

The main application repository owns:

* `public.documents`
* `public.vaults`
* `public.vault_memberships`
* users and permissions
* ingestion and parsing
* OCR
* embedding generation
* feature enrichment pipelines
* UI and application workflows

## Graph Repository

The graph-engine repository owns:

* graph configuration
* graph build / update logic
* feature fusion logic
* sparse graph persistence (`graph.*` tables)
* bridge edge persistence
* graph versions
* subgraph extraction
* graph payload generation for UI
* optional hierarchy / Z derived caches
* optional labeling module

The graph repository must **not** take ownership of:

* ingestion
* OCR
* parsing
* authentication or user management
* embedding generation
* generic enrichment pipelines
* general application orchestration

---

# Database Contract

The graph system uses the **same PostgreSQL database** as the main application.

## Write Scope

The graph system writes only to:

```
graph.*
```

All graph tables live in the dedicated `graph` schema.

## Read Scope

The graph system reads only from approved upstream tables or views:

```
public.documents
public.vaults
public.vault_memberships
approved embedding tables
approved feature tables or views
```

The graph system must **not** read arbitrary application tables.

Recommended read patterns:

* read through stable tables
* read through dedicated views
* use feature-provider adapters

## Prohibited Persistence

The graph system must never persist:

* dense similarity matrices
* dense distance matrices
* full pairwise document tables
* candidate pair tables
* full graph adjacency expansions

These are ephemeral compute artifacts only.

---

# Feature Contract

## Extraction vs Fusion

Feature extraction occurs **outside** the graph repository, in the main application's enrichment pipelines.

Feature fusion occurs **inside** the graph repository.

## Feature Layer Configuration

The graph repository consumes named feature layers through configuration specifying:

* feature layer name
* source table or view
* join key
* similarity metric
* feature weight

This configuration is stored in `graph.graph_config.feature_weights` (JSONB).

## Initial Layers

Required initial layer:

* document embedding

Optional initial layer:

* summary embedding

## Future Layers

Future optional layers (not required initially):

* tags
* category score vectors
* NER / entity profiles
* classifier outputs
* user-intent signals

The graph system must operate with **embeddings-only** initially. The system must not require all future features to exist.

---

# Execution Modes

## Import Mode (preferred initially)

The graph repository is imported directly as a Python package:

```python
from graph_engine import GraphService
```

The main application calls graph operations through Python function calls.

## Service Mode (optional, added later)

The graph repository exposes a lightweight internal API wrapper:

```
POST /graph/build-vault
POST /graph/extract-subgraph
POST /graph/compose-view
POST /graph/rebuild-bridges
```

The system should prefer **import mode initially**. Service mode is added later or in parallel only when useful.

Do not redesign the system as a distributed microservice platform.

---

# API Surface

The graph system exposes these primary operations (callable via import or API):

* `build_vault_graph` — construct a full sparse graph for a vault
* `update_graph_for_document` — incremental graph update for document insert/update/delete
* `extract_subgraph` — extract an induced subgraph for a set of document IDs
* `compose_view` — assemble a multi-vault composed graph view
* `rebuild_bridges` — recompute cross-vault bridge edges

---

# Outputs

The graph system produces:

* sparse graph edges (canonical relational state)
* graph versions (version metadata per vault)
* subgraph payloads (induced subgraphs for document subsets)
* composed graph payloads (multi-vault merged views)
* optional hierarchy caches (Z linkage, tree structures)

Derived artifacts:

* clusters
* semantic trees
* Z linkage arrays
* visualization layouts
* topic labels

These derived artifacts are **not canonical graph state**. They may be rebuilt at any time.

---

# Storage Rules

This contract explicitly reinforces the following storage rules:

Dense similarity matrices, dense distance matrices, full pairwise document tables, and candidate pair tables must **never** be persisted.

Only sparse graph edges are stored as the canonical relational structure.

All dense matrices must remain **ephemeral compute artifacts**.
