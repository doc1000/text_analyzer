

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

# Repository and Integration Model

The graph/topic modeling system is implemented in a **separate repository** (`graph-engine`), not as an internal module of the main application.

## Repo Ownership

Main application repository owns:

* `public.documents`
* `public.vaults`
* `public.vault_memberships`
* users / auth / app permissions
* ingestion, parsing, OCR
* embedding generation and feature enrichment pipelines
* UI and application workflows

Graph repository (`graph-engine`) owns:

* `graph.*` schema tables
* graph build / update logic
* feature fusion logic
* sparse graph persistence
* bridge edge persistence
* subgraph extraction logic
* graph versioning
* hierarchy / Z derived caches
* graph view payload builders
* optional labeling subsystem

The graph repository must not take over ingestion, OCR, parsing, authentication, user management, or generic enrichment pipelines.

## Execution Modes

Two execution modes are supported:

**Import mode** (preferred initially)

The graph repository is imported as a Python package:

```python
from graph_engine import GraphService
```

**Service mode** (added later or in parallel when useful)

The graph repository exposes a lightweight internal API wrapper:

```
POST /graph/build-vault
POST /graph/extract-subgraph
POST /graph/compose-view
POST /graph/rebuild-bridges
```

Import mode is the default. Do not redesign the system as a distributed microservice platform.

## Database Contract

The graph system uses the **same PostgreSQL database** as the main application.

Write scope:

* `graph.*` — all tables in the graph schema

Read scope (narrow approved contract):

* `public.documents`
* `public.vaults`
* `public.vault_memberships`
* approved embedding tables or views
* approved feature tables or views

The graph system must not read arbitrary application tables. Reads should go through stable tables, dedicated views, or feature-provider adapters.

Dense distance matrices, full graph tables, and per-pair candidate tables remain **prohibited**.

## Feature Contract

* Feature extraction occurs **outside** the graph repository (main application pipelines).
* Feature fusion occurs **inside** the graph repository.
* The graph repo consumes named feature layers through references to approved source tables/views/adapters.
* Graph configuration describes: feature layer names, source locations, join keys, metrics, weights, pruning mode, and graph build parameters.

Initial required layer: document embedding.
Initial optional layer: summary embedding.

Future optional layers (not required initially):

* tags
* category score vectors
* NER / entity profiles
* classifier outputs
* user-intent signals

The system must operate with **embeddings-only** in early phases.

---

# Global Implementation Rules

All phases must follow:

* `development_process/system_rules/GRAPH_ARCHITECTURE_RULES.md`
* `development_process/system_rules/GRAPH_AI_CONTEXT.md`
* `development_process/system_rules/GRAPH_MODULE_SCOPE.md`
* `development_process/GRAPH_SYSTEM_INTEGRATION_CONTRACT.md`
* `CURSOR_CONSTITUTION.md`

### Mandatory constraints

1. Implement **one phase at a time**
2. Do not modify files outside the phase scope
3. Do not introduce new dependencies unless required
4. Avoid modifying ingestion or unrelated modules
5. Graph queries must **not require full graph rebuild**
6. Sparse graph must be persisted in PostgreSQL
7. Clusters, trees, layouts, and labels remain **derived artifacts**
8. All graph tables live in a dedicated `graph` schema, not `public`
9. Dense distance matrices must **never** be persisted
10. Subgraph extraction must happen in SQL
11. All graph code lives in the `graph-engine` repository, not in the main application repository
12. The graph repo must not import or depend on main application code; it reads from approved database tables/views only

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

# Schema Reference

All database changes must follow:

* `development_process/graph_schema_plan.md`

Tables are introduced incrementally across phases.

---

# Phase Overview

| Phase   | Goal                                | New Tables                                             |
| ------- | ----------------------------------- | ------------------------------------------------------ |
| Phase 1 | Graph foundations                   | `graph.graph_config`                                   |
| Phase 2 | Graph persistence + versioning      | `graph.vault_graph_version`, `graph.vault_graph_edge`  |
| Phase 3 | Subgraph extraction                 | none                                                   |
| Phase 4 | Incremental graph updates           | `graph.graph_job`, `graph.graph_action_log`            |
| Phase 5 | Graph views for UI                  | none                                                   |
| Phase 6 | Cross-vault graph composition       | `graph.bridge_graph_edge`                              |
| Phase 7 | Derived hierarchy / Z compatibility | `graph.graph_hierarchy_cache`                          |
| Phase 8 | Feature-layer expansion             | none                                                   |
| Phase 9 | Labeling module                     | `graph.graph_label`                                    |

Each phase produces **working functionality**.

---

# Phase 1 — Graph Foundations

## Phase Goal

Create the `graph` PostgreSQL schema, the `graph.graph_config` table, and the `graph_core` Python module. Implement in-memory sparse kNN graph construction from document embeddings.

After this phase the system can build a sparse graph in memory for any vault and read/write graph build configurations.

## Files in Scope

```
src/graph_engine/__init__.py
src/graph_engine/graph_core/types.py
src/graph_engine/db/models/graph_config.py
src/graph_engine/graph_core/builder.py
migrations/versions/008_create_graph_schema.py
tests/unit/__init__.py
tests/unit/test_graph_builder.py
```

## Inputs

* document embeddings from the `embedding` schema (via approved embedding tables/views in the shared database)
* vault IDs from `public.vaults`
* document IDs from `public.documents`

## Outputs

* `graph` PostgreSQL schema
* `graph.graph_config` table
* in-memory `SparseGraph` object (adjacency edge list)
* `GraphConfig` dataclass
* `GraphBuilder` class with `build_vault_graph()` method

## Database Changes

Migration file: `migrations/versions/008_create_graph_schema.py`

```sql
CREATE SCHEMA IF NOT EXISTS graph;

CREATE TABLE graph.graph_config (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name TEXT NOT NULL,
  description TEXT,
  embedding_source TEXT NOT NULL,
  summary_embedding_source TEXT,
  feature_weights JSONB NOT NULL DEFAULT '{}'::jsonb,
  knn_k INTEGER NOT NULL,
  candidate_k INTEGER NOT NULL,
  pruning_mode TEXT NOT NULL DEFAULT 'none',
  bridge_knn_k INTEGER,
  is_active BOOLEAN NOT NULL DEFAULT true,
  created_by UUID REFERENCES public.users(id),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  updated_at TIMESTAMP NOT NULL DEFAULT now()
);
```

Run using Alembic within the graph-engine repo.

## Implementation Tasks

1. Create migration `migrations/versions/008_create_graph_schema.py` with the DDL above. Run it.

2. Create `src/graph_engine/__init__.py` as the package root.

3. Create `src/graph_engine/graph_core/types.py`:
   - Define `SparseEdge` dataclass with fields: `doc_lo: UUID`, `doc_hi: UUID`, `weight: float`, `distance: float | None`, `rank_lo: int | None`, `rank_hi: int | None`.
   - Define `SparseGraph` dataclass with fields: `nodes: list[UUID]`, `edges: list[SparseEdge]`, `vault_id: UUID`, `config_id: UUID`.
   - Enforce canonical edge ordering (`doc_lo < doc_hi`) via a factory or `__post_init__` check on `SparseEdge`.

4. Create `src/graph_engine/db/models/graph_config.py`:
   - Define a `GraphConfigRow` dataclass matching the `graph.graph_config` columns.
   - Implement `get_graph_config(config_id: UUID) -> GraphConfigRow` to read a config by ID.
   - Implement `get_active_graph_config() -> GraphConfigRow | None` to fetch the active config.
   - Implement `create_graph_config(...) -> GraphConfigRow` to insert a new config row.
   - Use SQLAlchemy core within the graph-engine repo; models live in `src/graph_engine/db/models/`.

5. Create `src/graph_engine/graph_core/builder.py`:
   - Define `GraphBuilder` class.
   - Implement `build_vault_graph(vault_id: UUID, config_id: UUID) -> SparseGraph`:
     a. Load graph config from `graph.graph_config`.
     b. Retrieve all document IDs for the vault from `public.documents`.
     c. Retrieve document embeddings for those documents from the `embedding` schema.
     d. Compute pairwise cosine distances in memory using numpy. This matrix is **ephemeral** and must never be persisted.
     e. For each document, find `candidate_k` nearest candidates and select the top `knn_k` neighbors.
     f. Build `SparseEdge` list with canonical ordering and weights (weight = 1 - distance or similarity score).
     g. Deduplicate symmetric edges (store each pair once with `doc_lo < doc_hi`).
     h. Return `SparseGraph`.
   - The builder must reject an empty vault (no documents) with a clear error.

6. Create `tests/unit/__init__.py` and `tests/unit/test_graph_builder.py`:
   - Test kNN construction with synthetic embeddings (small set, e.g. 10 documents).
   - Test canonical edge ordering: every edge satisfies `doc_lo < doc_hi`.
   - Test that node count matches input document count.
   - Test that edge count is bounded by `n * knn_k / 2` (approximate, due to deduplication).
   - Test that the builder raises on empty vault.

## Definition of Done

- [ ] `graph` schema exists in PostgreSQL
- [ ] `graph.graph_config` table exists and can be read/written
- [ ] `GraphBuilder.build_vault_graph()` produces a valid `SparseGraph` from embeddings
- [ ] kNN neighbor count is configurable via `graph_config.knn_k`
- [ ] Canonical edge ordering is enforced (`doc_lo < doc_hi`)
- [ ] Dense matrices are ephemeral (computed in memory, never written to DB)
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* Graph persistence to database (Phase 2)
* Graph versioning (Phase 2)
* Subgraph extraction (Phase 3)
* Incremental updates (Phase 4)
* UI payloads (Phase 5)
* Bridge edges (Phase 6)
* Hierarchy caches (Phase 7)
* Pruning algorithms beyond basic kNN (future)
* Feature fusion with multiple layers (Phase 8)
* Labels (Phase 9)

---

# Phase 2 — Graph Persistence and Versioning

## Phase Goal

Persist sparse graph edges per vault and manage graph versions. After this phase, graphs survive application restarts and can be rebuilt without destroying history. Version activation and supersession must be atomic.

## Files in Scope

```
src/graph_engine/db/repositories/graph_edge_repo.py
src/graph_engine/db/repositories/graph_version_repo.py
migrations/versions/009_graph_persistence.py
tests/unit/test_graph_repository.py
tests/unit/test_graph_version_manager.py
```

Files modified from Phase 1:

```
src/graph_engine/graph_core/builder.py (integrate persistence calls)
```

## Inputs

* `SparseGraph` from Phase 1 `GraphBuilder`
* `graph.graph_config` rows
* vault IDs

## Outputs

* `graph.vault_graph_version` table with unique active-version index
* `graph.vault_graph_edge` table with all required indexes
* `GraphEdgeRepo` class for edge CRUD
* `GraphVersionRepo` class for version lifecycle
* `GraphBuilder` now persists edges and creates versions

## Database Changes

Migration file: `migrations/versions/009_graph_persistence.py`

```sql
CREATE TABLE graph.vault_graph_version (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  vault_id UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
  graph_config_id UUID NOT NULL REFERENCES graph.graph_config(id),
  version_no INTEGER NOT NULL,
  status TEXT NOT NULL DEFAULT 'building',
  build_scope TEXT NOT NULL DEFAULT 'full',
  source_document_count INTEGER NOT NULL DEFAULT 0,
  edge_count INTEGER NOT NULL DEFAULT 0,
  built_by UUID REFERENCES public.users(id),
  build_started_at TIMESTAMP NOT NULL DEFAULT now(),
  build_finished_at TIMESTAMP,
  is_active BOOLEAN NOT NULL DEFAULT false,
  parent_version_id UUID REFERENCES graph.vault_graph_version(id),
  notes TEXT,
  UNIQUE (vault_id, version_no)
);

CREATE UNIQUE INDEX uq_active_vault_graph_version
  ON graph.vault_graph_version (vault_id)
  WHERE is_active = true;

CREATE TABLE graph.vault_graph_edge (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  graph_version_id UUID NOT NULL REFERENCES graph.vault_graph_version(id) ON DELETE CASCADE,
  vault_id UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
  doc_lo UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
  doc_hi UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
  weight DOUBLE PRECISION NOT NULL,
  distance DOUBLE PRECISION,
  rank_lo INTEGER,
  rank_hi INTEGER,
  edge_type TEXT NOT NULL DEFAULT 'internal',
  source_type TEXT NOT NULL DEFAULT 'knn',
  contributions JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_locked BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  CHECK (doc_lo <> doc_hi),
  CHECK (doc_lo < doc_hi),
  UNIQUE (graph_version_id, doc_lo, doc_hi)
);

CREATE INDEX idx_vault_graph_edge_version_lo
  ON graph.vault_graph_edge (graph_version_id, doc_lo);

CREATE INDEX idx_vault_graph_edge_version_hi
  ON graph.vault_graph_edge (graph_version_id, doc_hi);

CREATE INDEX idx_vault_graph_edge_vault_version
  ON graph.vault_graph_edge (vault_id, graph_version_id);

CREATE INDEX idx_vault_graph_edge_weight
  ON graph.vault_graph_edge (graph_version_id, weight DESC);
```

Allowed version statuses: `building`, `active`, `superseded`, `failed`, `stale`.

## Implementation Tasks

1. Create migration `migrations/versions/009_graph_persistence.py` with the DDL above. Run it.

2. Create `src/graph_engine/db/repositories/graph_edge_repo.py`:
   - Implement `save_graph(graph_version_id: UUID, vault_id: UUID, sparse_graph: SparseGraph) -> int`:
     a. Bulk insert all edges from `SparseGraph` into `graph.vault_graph_edge`.
     b. Use batch inserts (e.g. `executemany` or `COPY`) for performance on large graphs.
     c. Return the number of edges inserted.
   - Implement `load_graph(graph_version_id: UUID) -> SparseGraph`:
     a. Select all edges for the given version.
     b. Reconstruct `SparseGraph` with node list derived from edge endpoints.
     c. Return the graph.
   - Implement `delete_graph_edges(graph_version_id: UUID) -> int`:
     a. Delete all edges for a graph version.
     b. Return count of deleted rows.

3. Create `src/graph_engine/db/repositories/graph_version_repo.py`:
   - Implement `create_version(vault_id: UUID, config_id: UUID, built_by: UUID | None = None) -> dict`:
     a. Compute `version_no` as `max(version_no) + 1` for the vault, defaulting to 1.
     b. Insert into `graph.vault_graph_version` with status `building`.
     c. Return the new row as a dict.
   - Implement `finalize_version(version_id: UUID, edge_count: int, doc_count: int)`:
     a. Set `build_finished_at = now()`, `edge_count`, `source_document_count`.
   - Implement `activate_version(version_id: UUID)`:
     a. In a single transaction: set previous active version for the same vault to `status = 'superseded'`, `is_active = false`; then set this version to `status = 'active'`, `is_active = true`.
     b. The unique partial index enforces only one active version per vault.
   - Implement `fail_version(version_id: UUID, error_message: str | None = None)`:
     a. Set `status = 'failed'`, `build_finished_at = now()`, optionally store error message in `notes`.
   - Implement `get_active_version(vault_id: UUID) -> dict | None`:
     a. Query for the row where `vault_id` matches and `is_active = true`.

4. Update `src/graph_engine/graph_core/builder.py`:
   - After `build_vault_graph()` constructs the in-memory graph:
     a. Call `GraphVersionRepo.create_version()`.
     b. Call `GraphEdgeRepo.save_graph()`.
     c. Call `GraphVersionRepo.finalize_version()` with edge and doc counts.
     d. Call `GraphVersionRepo.activate_version()`.
   - Wrap in try/except: on failure call `GraphVersionRepo.fail_version()`.

5. Create tests:
   - `tests/unit/test_graph_repository.py`:
     a. Test save/load round-trip: edges match after persistence.
     b. Test bulk insert correctness: edge count in DB matches input.
     c. Test canonical ordering is preserved in DB.
     d. Test `delete_graph_edges` removes all edges for the version.
   - `tests/unit/test_graph_version_manager.py`:
     a. Test version creation increments `version_no`.
     b. Test activation sets `is_active = true` and supersedes old version.
     c. Test only one active version exists per vault at any time.
     d. Test failure marking sets correct status.

## Definition of Done

- [ ] `graph.vault_graph_version` and `graph.vault_graph_edge` tables exist with all indexes
- [ ] `GraphBuilder.build_vault_graph()` now persists edges and creates a version
- [ ] Active version is queryable per vault
- [ ] Version supersession works: activating a new version deactivates the old one
- [ ] Edge round-trip (save then load) produces identical graph
- [ ] Partial index enforces single active version per vault
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* Subgraph extraction (Phase 3)
* Incremental updates (Phase 4)
* Job tracking (Phase 4)
* UI payloads (Phase 5)
* Bridge edges (Phase 6)
* Hierarchy caches (Phase 7)

---

# Phase 3 — Subgraph Extraction

## Phase Goal

Extract induced subgraphs from persisted vault graphs using SQL. Given a set of document IDs, return the subset of edges connecting those documents without loading the full graph into memory.

## Files in Scope

```
src/graph_engine/graph_core/subgraph.py
tests/unit/test_subgraph_service.py
```

## Inputs

* vault ID
* set of document IDs (the requested subset)
* active graph version from Phase 2

## Outputs

* `SubgraphResult` dataclass with nodes, edges, and metadata
* `SubgraphService` with SQL-based extraction methods

## Database Changes

None. This phase queries tables from Phase 2.

## Implementation Tasks

1. Create `src/graph_engine/graph_core/subgraph.py`:
   - Define `SubgraphResult` dataclass: `nodes: list[UUID]`, `edges: list[SparseEdge]`, `graph_version_id: UUID`, `vault_id: UUID`, `node_count: int`, `edge_count: int`.
   - Implement `extract_subgraph(vault_id: UUID, document_ids: list[UUID], graph_version_id: UUID | None = None) -> SubgraphResult`:
     a. If `graph_version_id` is None, look up the active version for the vault via `GraphVersionRepo.get_active_version()`.
     b. Execute SQL to select edges where **both** `doc_lo` and `doc_hi` are in the provided document ID set and `graph_version_id` matches.
     c. Collect distinct node IDs from the returned edges.
     d. Return `SubgraphResult`.

   SQL pattern for induced subgraph:
     ```sql
     SELECT * FROM graph.vault_graph_edge
     WHERE graph_version_id = :version_id
       AND doc_lo = ANY(:doc_ids)
       AND doc_hi = ANY(:doc_ids);
     ```

   - Implement `extract_ego_subgraph(vault_id: UUID, center_doc_id: UUID, depth: int = 1, graph_version_id: UUID | None = None) -> SubgraphResult`:
     a. Look up the active version if not provided.
     b. At depth 1: find all edges incident to `center_doc_id` (where `doc_lo = center_id OR doc_hi = center_id`).
     c. Collect neighbor IDs from those edges.
     d. Return the induced subgraph over `{center_doc_id} + neighbors`.
     e. Depth > 1 is optional stretch for this phase. If implemented, use iterative expansion (not recursive SQL).

   - SQL pattern for ego subgraph depth 1:
     ```sql
     SELECT * FROM graph.vault_graph_edge
     WHERE graph_version_id = :version_id
       AND (doc_lo = :center_id OR doc_hi = :center_id);
     ```

   - All graph slicing must happen in SQL. Application code only assembles the `SubgraphResult`.

2. Create `tests/unit/test_subgraph_service.py`:
   - Test induced subgraph returns only edges where both endpoints are in the requested set.
   - Test ego subgraph returns correct 1-hop neighborhood.
   - Test empty document set returns empty result (zero nodes, zero edges).
   - Test requesting documents that have no edges returns result with nodes but no edges.
   - Test with non-existent or stale graph version raises appropriate error or returns empty.

## Definition of Done

- [ ] `extract_subgraph()` returns correct induced subgraph for any document subset
- [ ] `extract_ego_subgraph()` returns correct 1-hop neighborhood
- [ ] All graph slicing is performed in SQL, not in application memory
- [ ] Full graph is never loaded into memory for slicing
- [ ] `SubgraphResult` includes accurate `node_count` and `edge_count`
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* Ego subgraph at depth > 1 (optional stretch, not required)
* Incremental updates (Phase 4)
* UI payload formatting (Phase 5)
* Cross-vault composition (Phase 6)
* Hierarchy derivation (Phase 7)
* Permission filtering on document IDs (application layer responsibility, not graph module)

---

# Phase 4 — Incremental Graph Updates

## Phase Goal

Support local graph maintenance when documents are inserted, updated, or deleted. Introduce job and action tracking tables to record graph operations.

After this phase, single-document changes do not require a full vault graph rebuild.

## Files in Scope

```
src/graph_engine/graph_core/updater.py
migrations/versions/010_graph_jobs.py
tests/unit/test_graph_update_service.py
```

## Inputs

* document ID (inserted, updated, or deleted)
* vault ID
* active graph version
* document embeddings

## Outputs

* `graph.graph_job` table
* `graph.graph_action_log` table
* `GraphUpdateService` with insert, delete, and repair methods

## Database Changes

Migration file: `migrations/versions/010_graph_jobs.py`

```sql
CREATE TABLE graph.graph_job (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  job_type TEXT NOT NULL,
  job_scope TEXT NOT NULL,
  vault_id UUID REFERENCES public.vaults(id) ON DELETE CASCADE,
  target_vault_id UUID REFERENCES public.vaults(id) ON DELETE CASCADE,
  graph_version_id UUID REFERENCES graph.vault_graph_version(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'queued',
  requested_by UUID REFERENCES public.users(id),
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  started_at TIMESTAMP,
  finished_at TIMESTAMP,
  error_message TEXT,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);

CREATE TABLE graph.graph_action_log (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL REFERENCES public.users(id),
  action_type TEXT NOT NULL,
  action_scope TEXT NOT NULL,
  vault_id UUID REFERENCES public.vaults(id) ON DELETE CASCADE,
  target_vault_id UUID REFERENCES public.vaults(id) ON DELETE CASCADE,
  document_ids UUID[] NOT NULL DEFAULT '{}'::uuid[],
  payload JSONB NOT NULL DEFAULT '{}'::jsonb,
  strength DOUBLE PRECISION,
  persisted_effect BOOLEAN NOT NULL DEFAULT false,
  created_at TIMESTAMP NOT NULL DEFAULT now()
);
```

Job types: `build_vault_graph`, `repair_local_graph`, `rebuild_bridges`, `derive_hierarchy_cache`.

## Implementation Tasks

1. Create migration `migrations/versions/010_graph_jobs.py` with the DDL above. Run it.

2. Create `src/graph_engine/graph_core/updater.py`:
   - Implement `insert_document(vault_id: UUID, document_id: UUID, graph_version_id: UUID | None = None)`:
     a. Look up the active graph version if not provided.
     b. Load the graph config for the version.
     c. Retrieve the new document's embedding.
     d. Retrieve embeddings for existing documents in the vault. If the vault is large, limit to `candidate_k` nearest candidates using an approximate approach or random sample.
     e. Compute cosine similarities between the new document and candidates.
     f. Select top `knn_k` neighbors.
     g. Insert new edges into `graph.vault_graph_edge` with canonical ordering (`doc_lo < doc_hi`).
     h. Update `vault_graph_version.edge_count` and `source_document_count` by the incremental amounts.
     i. Log the operation to `graph.graph_job` with `job_type = 'repair_local_graph'`, `status = 'completed'`.

   - Implement `delete_document(vault_id: UUID, document_id: UUID, graph_version_id: UUID | None = None)`:
     a. Look up the active graph version if not provided.
     b. Delete all edges in `graph.vault_graph_edge` where `doc_lo = document_id OR doc_hi = document_id` for the given version.
     c. Identify orphaned neighbors: nodes that were connected only through the deleted document. For each orphaned neighbor, optionally find new nearest neighbors from remaining graph nodes and insert replacement edges.
     d. Update `vault_graph_version.edge_count` and `source_document_count`.
     e. Log the operation to `graph.graph_job`.

   - Implement `update_document(vault_id: UUID, document_id: UUID)`:
     a. Call `delete_document()` then `insert_document()`.

   - Neighbor repair strategy for deletion: for each neighbor that lost an edge, check if the neighbor's remaining edge count dropped below `knn_k / 2`. If so, find new nearest neighbors from the vault's document pool (limited to `candidate_k`) and insert edges to restore connectivity.

   - Full graph rebuild must **never** be triggered by these operations.

3. Create `tests/unit/test_graph_update_service.py`:
   - Test document insertion adds correct number of edges.
   - Test inserted edges satisfy canonical ordering.
   - Test document deletion removes all incident edges.
   - Test neighbor repair restores connectivity for orphaned nodes.
   - Test `update_document` produces the same result as delete + insert.
   - Test `edge_count` and `source_document_count` on the version are updated correctly.
   - Test that a `graph.graph_job` row is created for each operation.

## Definition of Done

- [ ] `graph.graph_job` and `graph.graph_action_log` tables exist
- [ ] Document insertion adds local edges without full rebuild
- [ ] Document deletion removes incident edges and optionally repairs neighbors
- [ ] Graph version metadata (`edge_count`, `source_document_count`) is updated after each operation
- [ ] Operations are logged to `graph.graph_job`
- [ ] Full graph rebuild is never triggered by these operations
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* UI payloads (Phase 5)
* Cross-vault bridge updates on document change (Phase 6)
* Async job queue processing (future)
* User structural action logging via `graph_action_log` (table is created here but populated by future features)
* Stale-marking of derived caches (Phase 7)
* Batch update operations (future enhancement)

---

# Phase 5 — Graph Views for UI

## Phase Goal

Convert graph slices into UI-ready payloads suitable for D3 force layouts and grouped visualizations. After this phase, the frontend can receive graph data in a standardized format.

## Files in Scope

```
src/graph_engine/views/__init__.py
src/graph_engine/views/graph_payload_builder.py
src/graph_engine/views/force_payload.py
tests/unit/test_graph_views.py
```

## Inputs

* `SubgraphResult` from Phase 3 `SubgraphService`
* document metadata from `public.documents` (title, URL, vault_id)

## Outputs

* `GraphNodePayload` and `GraphEdgePayload` dataclasses
* `GraphViewPayload` container
* D3 force-simulation-compatible JSON payload
* grouped node payload

## Database Changes

None.

## Implementation Tasks

1. Create `src/graph_engine/views/__init__.py` as an empty package marker.

2. Create `src/graph_engine/views/graph_payload_builder.py`:
   - Define `GraphNodePayload` dataclass: `id: str`, `title: str`, `url: str | None`, `vault_id: str`, `metadata: dict`.
   - Define `GraphEdgePayload` dataclass: `source: str`, `target: str`, `weight: float`, `edge_type: str`.
   - Define `GraphViewPayload` dataclass: `nodes: list[GraphNodePayload]`, `edges: list[GraphEdgePayload]`, `node_count: int`, `edge_count: int`, `metadata: dict`.
   - Implement `build_payload(subgraph_result: SubgraphResult, document_metadata: dict[UUID, dict]) -> GraphViewPayload`:
     a. For each node UUID in the subgraph, look up document metadata and create a `GraphNodePayload`.
     b. For each edge in the subgraph, create a `GraphEdgePayload` using string UUIDs.
     c. Return `GraphViewPayload`.
   - `document_metadata` is a dict keyed by document UUID with values containing at minimum `title`, `url`, `vault_id`.

3. Create `src/graph_engine/views/force_payload.py`:
   - Implement `build_force_payload(graph_view_payload: GraphViewPayload) -> dict`:
     a. Convert `GraphViewPayload` into D3 force-simulation-compatible JSON:
        ```json
        {
          "nodes": [
            {"id": "uuid", "title": "...", "url": "...", "vault_id": "...", "group": null}
          ],
          "links": [
            {"source": "uuid-a", "target": "uuid-b", "value": 0.85, "edge_type": "internal"}
          ],
          "metadata": {
            "node_count": 42,
            "edge_count": 87,
            "vault_id": "...",
            "graph_version_id": "..."
          }
        }
        ```
     b. Use `weight` as the link `value`.
     c. Include document metadata on each node.

   - Implement `build_grouped_payload(graph_view_payload: GraphViewPayload, groups: dict[str, list[str]]) -> dict`:
     a. Accept a grouping map: `group_label -> list of node IDs`.
     b. Annotate each node dict with a `group` field.
     c. Add a `groups` key to the output listing all group labels and their member counts.
     d. Nodes not in any group get `group = null`.
     e. Return the grouped payload structure.

4. Create `tests/unit/test_graph_views.py`:
   - Test force payload `nodes` and `links` counts match input.
   - Test every `source` and `target` in `links` references a valid node `id`.
   - Test grouped payload assigns group labels correctly.
   - Test nodes not in any group have `group = null`.
   - Test empty subgraph produces valid empty payload: `{"nodes": [], "links": [], "metadata": {...}}`.
   - Test payload is JSON-serializable (`json.dumps` does not raise).

## Definition of Done

- [ ] `build_force_payload()` produces D3-compatible JSON
- [ ] `build_grouped_payload()` annotates nodes with group membership
- [ ] Payloads are JSON-serializable
- [ ] All node references in edges are valid node IDs
- [ ] Empty subgraph produces valid empty payload
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* Cross-vault composed payloads (Phase 6)
* Hierarchy/tree payloads (Phase 7)
* Community detection or automatic grouping (Phase 7 or later)
* Label attachment (Phase 9)
* Frontend rendering code
* Layout coordinate computation (client-side responsibility)

---

# Phase 6 — Cross-Vault Graph Composition

## Phase Goal

Support graph views combining multiple vaults by computing and persisting bridge edges between vaults, and assembling composed multi-vault graph payloads.

Internal vault graph structure must never be modified by bridge operations.

## Files in Scope

```
src/graph_engine/graph_core/composition.py
src/graph_engine/graph_core/bridges.py
migrations/versions/011_bridge_edges.py
tests/unit/test_graph_composition.py
```

## Inputs

* two or more vault IDs
* active graph versions per vault
* document embeddings across vaults

## Outputs

* `graph.bridge_graph_edge` table with indexes
* `GraphCompositionService` for bridge construction and composed views

## Database Changes

Migration file: `migrations/versions/011_bridge_edges.py`

```sql
CREATE TABLE graph.bridge_graph_edge (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  source_vault_id UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
  target_vault_id UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
  source_doc_id UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
  target_doc_id UUID NOT NULL REFERENCES public.documents(id) ON DELETE CASCADE,
  weight DOUBLE PRECISION NOT NULL,
  distance DOUBLE PRECISION,
  source_graph_version_id UUID REFERENCES graph.vault_graph_version(id) ON DELETE SET NULL,
  target_graph_version_id UUID REFERENCES graph.vault_graph_version(id) ON DELETE SET NULL,
  bridge_config_id UUID REFERENCES graph.graph_config(id),
  source_type TEXT NOT NULL DEFAULT 'bridge_knn',
  contributions JSONB NOT NULL DEFAULT '{}'::jsonb,
  is_persistent BOOLEAN NOT NULL DEFAULT true,
  is_locked BOOLEAN NOT NULL DEFAULT false,
  created_by UUID REFERENCES public.users(id),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  CHECK (source_vault_id <> target_vault_id)
);

CREATE INDEX idx_bridge_graph_edge_source_vault
  ON graph.bridge_graph_edge (source_vault_id);

CREATE INDEX idx_bridge_graph_edge_target_vault
  ON graph.bridge_graph_edge (target_vault_id);

CREATE INDEX idx_bridge_graph_edge_source_doc
  ON graph.bridge_graph_edge (source_doc_id);

CREATE INDEX idx_bridge_graph_edge_target_doc
  ON graph.bridge_graph_edge (target_doc_id);
```

## Implementation Tasks

1. Create migration `migrations/versions/011_bridge_edges.py` with the DDL above. Run it.

2. Create `src/graph_engine/graph_core/composition.py` and `src/graph_engine/graph_core/bridges.py`:
   - Implement `build_bridges(source_vault_id: UUID, target_vault_id: UUID, config_id: UUID, built_by: UUID | None = None) -> int`:
     a. Load graph config for `bridge_knn_k`.
     b. Retrieve document embeddings for both vaults.
     c. Compute cross-vault cosine similarities (ephemeral, in-memory only).
     d. For each document in source vault, find top `bridge_knn_k` nearest documents in target vault.
     e. Persist bridge edges to `graph.bridge_graph_edge` with source/target vault and graph version references.
     f. Return count of bridge edges created.

   - Implement `load_bridges(vault_id_a: UUID, vault_id_b: UUID) -> list[SparseEdge]`:
     a. Query `graph.bridge_graph_edge` for edges where `(source_vault_id, target_vault_id)` matches `(a, b)` or `(b, a)`.
     b. Return edge list.

   - Implement `compose_view(vault_ids: list[UUID], document_ids: list[UUID] | None = None) -> SubgraphResult`:
     a. For each vault, extract the internal subgraph (using `SubgraphService.extract_subgraph()`). If `document_ids` is provided, filter to those documents within each vault.
     b. For each pair of vaults, load bridge edges.
     c. Merge all internal edges and bridge edges into a single combined `SubgraphResult`.
     d. Set `vault_id` on the result to `None` or a sentinel to indicate composed view.

   - Implement `delete_bridges(source_vault_id: UUID, target_vault_id: UUID) -> int`:
     a. Delete all bridge edges for the vault pair (both directions).
     b. Return count of deleted rows.

   - Bridge edges must **never** modify internal vault graph edges.

3. Create `tests/unit/test_graph_composition.py`:
   - Test bridge edge creation between two vaults produces expected edge count.
   - Test `load_bridges` returns edges for the requested vault pair regardless of source/target direction.
   - Test `compose_view` merges internal and bridge edges correctly.
   - Test `delete_bridges` removes all bridge edges for the pair.
   - Test that internal vault edges are unchanged after any bridge operation.
   - Test the `CHECK (source_vault_id <> target_vault_id)` constraint rejects self-bridges.

## Definition of Done

- [ ] `graph.bridge_graph_edge` table exists with indexes
- [ ] Bridge edges can be built between two vaults
- [ ] Composed view combines internal graphs with bridge edges
- [ ] Internal vault edges are never modified by bridge operations
- [ ] Bridge edges can be rebuilt independently without touching internal edges
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* Permission-based bridge filtering (application layer responsibility)
* Automatic bridge recomputation when vault graphs change (future trigger)
* Bridge edge pruning policies (future)
* Hierarchy over composed views (Phase 7)
* Labels on bridge edges (Phase 9)

---

# Phase 7 — Derived Hierarchy / Z Compatibility

## Phase Goal

Produce optional hierarchy views (Z linkage arrays, tree payloads, circle-pack structures) derived from graph slices. Provide a cache table for these derived artifacts.

These structures are **derived artifacts** and must never become canonical graph state.

## Files in Scope

```
src/graph_engine/graph_core/hierarchy.py
migrations/versions/012_graph_hierarchy_cache.py
tests/unit/test_hierarchy_builder.py
```

## Inputs

* `SubgraphResult` from Phase 3 or composed view from Phase 6
* edge weights from `graph.vault_graph_edge`

## Outputs

* `graph.graph_hierarchy_cache` table
* `HierarchyBuilder` with Z linkage and tree payload generation
* cached hierarchy payloads

## Database Changes

Migration file: `migrations/versions/012_graph_hierarchy_cache.py`

```sql
CREATE TABLE graph.graph_hierarchy_cache (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  graph_version_id UUID NOT NULL REFERENCES graph.vault_graph_version(id) ON DELETE CASCADE,
  vault_id UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
  cache_type TEXT NOT NULL,
  source_scope TEXT NOT NULL DEFAULT 'vault',
  document_ids UUID[],
  payload JSONB NOT NULL,
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  expires_at TIMESTAMP
);
```

Cache type values: `z_linkage`, `circle_pack_payload`, `local_hierarchy_payload`.

## Implementation Tasks

1. Create migration `migrations/versions/012_graph_hierarchy_cache.py` with the DDL above. Run it.

2. Create `src/graph_engine/graph_core/hierarchy.py`:
   - Implement `build_z_linkage(subgraph_result: SubgraphResult) -> dict`:
     a. Build a condensed distance vector from edge weights for the subgraph nodes. For node pairs without an edge, use a default large distance (e.g. 1.0 or the maximum observed distance).
     b. Compute hierarchical clustering using `scipy.cluster.hierarchy.linkage` with method `average` (configurable).
     c. Return a dict with `z_array` (the linkage matrix as a list of lists), `labels` (ordered node UUIDs as strings), and `method`.

   - Implement `build_hierarchy_payload(subgraph_result: SubgraphResult, method: str = 'average') -> dict`:
     a. Call `build_z_linkage()`.
     b. Convert the Z linkage array into a nested tree structure using `scipy.cluster.hierarchy.to_tree()` or equivalent.
     c. Return a JSON-serializable tree: `{"id": "root", "children": [...], "documents": [...], "node_count": N}`.
     d. Leaf nodes contain `document_id`; internal nodes contain children.

   - Implement `cache_hierarchy(graph_version_id: UUID, vault_id: UUID, cache_type: str, payload: dict, document_ids: list[UUID] | None = None, expires_at: datetime | None = None)`:
     a. Insert a row into `graph.graph_hierarchy_cache`.

   - Implement `load_cached_hierarchy(graph_version_id: UUID, vault_id: UUID, cache_type: str) -> dict | None`:
     a. Query the cache table for the matching row.
     b. If found and `expires_at` is either null or in the future, return the `payload`.
     c. Otherwise return `None`.

   - Implement `invalidate_hierarchy_cache(graph_version_id: UUID, vault_id: UUID | None = None)`:
     a. Delete matching cache rows. If `vault_id` is None, delete all caches for the version.

3. Create `tests/unit/test_hierarchy_builder.py`:
   - Test `build_z_linkage` produces a valid linkage array (correct shape: `(n-1, 4)` for `n` nodes).
   - Test `build_hierarchy_payload` produces a valid nested tree with all documents as leaves.
   - Test cache round-trip: `cache_hierarchy` then `load_cached_hierarchy` returns the payload.
   - Test expired cache returns `None`.
   - Test `invalidate_hierarchy_cache` removes cached entries.
   - Test with a 1-node subgraph (edge case: no clustering possible).

## Definition of Done

- [ ] `graph.graph_hierarchy_cache` table exists
- [ ] Z linkage can be computed from any subgraph with 2+ nodes
- [ ] Hierarchy payload is a valid nested tree structure
- [ ] Caches can be stored, retrieved, and invalidated
- [ ] Expired caches are not returned
- [ ] These structures are derived only, not treated as canonical graph state
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* Automatic cache invalidation triggered by graph updates (future hook from Phase 4)
* Compatibility shims for existing `semantic_tree_v2` schema (separate migration path)
* Circle-pack layout coordinate generation (optional future extension)
* Label attachment to hierarchy nodes (Phase 9)
* Streaming hierarchy computation for very large subgraphs (future performance enhancement)

---

# Phase 8 — Feature Layer Expansion

## Phase Goal

Support multiple semantic feature layers beyond embeddings and weighted feature fusion for graph construction. After this phase, graph builds can incorporate summary embeddings and the system is ready for future feature types.

## Files in Scope

```
src/graph_engine/features/__init__.py
src/graph_engine/features/base.py
src/graph_engine/features/embedding_provider.py
src/graph_engine/features/summary_embedding_provider.py
src/graph_engine/features/fusion.py
tests/unit/test_feature_fusion.py
```

Files modified from Phase 1:

```
src/graph_engine/graph_core/builder.py (replace direct embedding access with FeatureFusionEngine)
```

## Inputs

* document IDs
* embedding vectors (existing `embedding` schema)
* summary embedding vectors (existing `embedding` schema)
* `graph.graph_config.feature_weights` JSONB field

## Outputs

* `FeatureProvider` protocol
* `EmbeddingFeatureProvider` and `SummaryEmbeddingFeatureProvider` implementations
* `FeatureFusionEngine` for weighted multi-layer similarity computation

## Database Changes

None. The `feature_weights` JSONB column already exists on `graph.graph_config` from Phase 1. Feature providers read from existing tables in the `embedding` schema.

## Implementation Tasks

1. Create `src/graph_engine/features/__init__.py` as an empty package marker.

2. Create `src/graph_engine/features/base.py` and `src/graph_engine/features/embedding_provider.py`:
   - Define `FeatureProvider` protocol (or ABC):
     ```python
     class FeatureProvider(Protocol):
         name: str
         def get_vectors(self, document_ids: list[UUID]) -> dict[UUID, np.ndarray]: ...
     ```
   - Implement `EmbeddingFeatureProvider`:
     a. Accept embedding source name (matching `graph_config.embedding_source`).
     b. `get_vectors()` reads document embeddings from the `embedding` schema.
     c. Returns L2-normalized vectors keyed by document UUID.
     d. Documents without embeddings are omitted from the result.
   Create `src/graph_engine/features/summary_embedding_provider.py`:
   - Implement `SummaryEmbeddingFeatureProvider`:
     a. Accept summary embedding source name (matching `graph_config.summary_embedding_source`).
     b. `get_vectors()` reads summary embeddings.
     c. Returns L2-normalized vectors or empty dict if no summary embeddings exist.

3. Create `src/graph_engine/features/fusion.py`:
   - Define `FeatureFusionEngine`:
     a. Accept a list of `(FeatureProvider, float)` tuples (provider + weight).
     b. Implement `compute_fused_similarities(document_ids: list[UUID]) -> tuple[np.ndarray, list[UUID]]`:
        - For each provider, call `get_vectors()`.
        - Compute per-layer pairwise cosine similarity matrices.
        - Multiply each matrix by its weight.
        - Sum weighted similarity matrices.
        - Normalize so final values are in [0, 1].
        - Return the fused similarity matrix and the ordered list of document IDs (since some documents may be missing from some providers).
     c. Handle missing features: if a provider returns no vector for a document, that layer contributes zero similarity for pairs involving that document. Re-normalize the weights for those pairs.
     d. The fused similarity matrix is **ephemeral** and must never be persisted.

4. Update `src/graph_engine/graph_core/builder.py`:
   - Replace direct embedding retrieval with `FeatureFusionEngine`.
   - Read `feature_weights` from `graph.graph_config` to determine which providers and weights to use.
   - Construct provider list: always include `EmbeddingFeatureProvider`; include `SummaryEmbeddingFeatureProvider` if `summary_embedding_source` is set and weight > 0.
   - Maintain backward compatibility: if `feature_weights` is empty, default to embeddings-only with weight 1.0.

5. Create `tests/unit/test_feature_fusion.py`:
   - Test single-layer fusion equals raw cosine similarity.
   - Test two-layer weighted fusion with known weights produces correct result.
   - Test missing feature vectors for some documents are handled (those pairs use reduced weight set).
   - Test that the returned similarity matrix shape matches the document count.
   - Test that the fusion engine does not persist any matrices.

## Definition of Done

- [ ] `FeatureProvider` protocol is defined
- [ ] `EmbeddingFeatureProvider` and `SummaryEmbeddingFeatureProvider` are implemented and tested
- [ ] `FeatureFusionEngine` produces correct weighted fused similarities
- [ ] `GraphBuilder` uses `FeatureFusionEngine` instead of direct embedding access
- [ ] Feature weights are read from `graph.graph_config.feature_weights`
- [ ] Backward compatibility: empty weights default to embeddings-only
- [ ] Dense similarity matrices remain ephemeral
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* Tag extraction pipelines
* NER extraction pipelines
* Category classifier pipelines
* New feature provider implementations beyond embeddings and summary embeddings
* Changes to `graph.graph_config` table schema (the `feature_weights` column already exists)
* Automatic feature refresh or recomputation triggers

---

# Phase 9 — Labeling Module

## Phase Goal

Generate human-readable labels for graph communities and groups. Labels attach to graph views without blocking graph rendering.

## Files in Scope

```
src/graph_engine/labels/__init__.py
src/graph_engine/labels/label_service.py
src/graph_engine/labels/cache.py
migrations/versions/013_graph_labels.py
tests/unit/test_label_service.py
```

## Inputs

* graph communities or groups (sets of document IDs, typically from grouped payloads or hierarchy nodes)
* document titles and text content from `public.documents`

## Outputs

* `graph.graph_label` table
* `LabelService` for label generation and attachment
* `LabelRepository` for label CRUD
* cached labels per group per graph version

## Database Changes

Migration file: `migrations/versions/013_graph_labels.py`

```sql
CREATE TABLE graph.graph_label (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  graph_version_id UUID NOT NULL REFERENCES graph.vault_graph_version(id) ON DELETE CASCADE,
  vault_id UUID NOT NULL REFERENCES public.vaults(id) ON DELETE CASCADE,
  group_key TEXT NOT NULL,
  label TEXT NOT NULL,
  method TEXT NOT NULL DEFAULT 'extractive',
  confidence DOUBLE PRECISION,
  user_override BOOLEAN NOT NULL DEFAULT false,
  overridden_by UUID REFERENCES public.users(id),
  created_at TIMESTAMP NOT NULL DEFAULT now(),
  UNIQUE (graph_version_id, group_key)
);
```

## Implementation Tasks

1. Create migration `migrations/versions/013_graph_labels.py` with the DDL above. Run it.

2. Create `src/graph_engine/labels/__init__.py` as an empty package marker.

3. Create `src/graph_engine/labels/cache.py`:
   - Implement `save_label(graph_version_id: UUID, vault_id: UUID, group_key: str, label: str, method: str = 'extractive', confidence: float | None = None)`:
     a. Upsert into `graph.graph_label` (insert or update on conflict of `(graph_version_id, group_key)`).
   - Implement `get_label(graph_version_id: UUID, group_key: str) -> str | None`:
     a. Query for the label. Return the label text or None.
   - Implement `get_labels_for_version(graph_version_id: UUID) -> dict[str, str]`:
     a. Return all labels for the version as `{group_key: label_text}`.
   - Implement `override_label(graph_version_id: UUID, group_key: str, new_label: str, user_id: UUID)`:
     a. Update the label, set `user_override = true`, `overridden_by = user_id`.
   - Implement `delete_labels(graph_version_id: UUID)`:
     a. Delete all labels for the version.

4. Create `src/graph_engine/labels/label_service.py`:
   - Implement `generate_label(document_ids: list[UUID]) -> str`:
     a. Retrieve document titles from `public.documents` for the given IDs.
     b. Use extractive summarization: identify the most frequent significant terms across titles (strip stop words, find common n-grams or TF-IDF top terms).
     c. Return a short label string (target: 2-5 words).
     d. If no titles are available, return a fallback like `"Unlabeled group"`.

   - Implement `generate_labels_for_groups(groups: dict[str, list[UUID]], graph_version_id: UUID, vault_id: UUID) -> dict[str, str]`:
     a. For each group, call `generate_label()`.
     b. Save each label via `LabelRepository.save_label()`.
     c. Return `{group_key: label_text}` mapping.

   - Implement `attach_labels(graph_view_payload: dict, graph_version_id: UUID) -> dict`:
     a. Load cached labels for the version via `LabelRepository.get_labels_for_version()`.
     b. If the payload has grouped nodes, attach labels to matching groups.
     c. Return the enriched payload.
     d. If no labels exist, return the payload **unchanged**. Label absence must never cause an error.

   - Label generation must be **non-blocking**: graph payloads must be returnable without waiting for label generation to complete.

5. Create `tests/unit/test_label_service.py`:
   - Test `generate_label` produces a non-empty string from document titles.
   - Test `generate_label` returns fallback for empty document list.
   - Test label save/load round-trip via repository.
   - Test user override replaces generated label and sets `user_override = true`.
   - Test `attach_labels` enriches grouped payload with labels.
   - Test `attach_labels` returns unchanged payload when no labels exist.
   - Test `delete_labels` removes all labels for a version.

## Definition of Done

- [ ] `graph.graph_label` table exists
- [ ] `generate_label()` produces labels from document titles
- [ ] Labels can be cached, retrieved, overridden, and deleted per graph version
- [ ] User overrides are supported and tracked
- [ ] `attach_labels()` enriches payloads without blocking graph rendering
- [ ] Graph rendering works correctly with or without labels present
- [ ] Unit tests pass
- [ ] No files outside the scope list were modified

## Out of Scope

* LLM-based label generation (future enhancement)
* Automatic label refresh when graph changes
* Label quality metrics or scoring
* Multi-language label support
* Label UI components
* Label generation for bridge edge groups

---

# Testing Requirements

Each phase must include:

* unit tests covering the main code paths
* edge case tests (empty inputs, missing data, constraint violations)
* integration tests where database tables are involved
* payload structure verification (for phases producing JSON payloads)

Test files live in `tests/unit/` and `tests/integration/` within the `graph-engine` repo and follow the pattern `test_<module>.py`.

---

# Deployment Strategy

The graph-engine repository is deployed as an **importable Python package** initially (import mode). Service mode adds an optional lightweight API wrapper when needed.

Graph rebuild operations must be:

* **asynchronous** — never block normal requests
* **vault-scoped** — each vault is rebuilt independently
* **versioned** — new builds create new versions; old versions are superseded, not deleted

Migration files live in `migrations/versions/` within the graph-engine repo and are managed by **Alembic**.

---

# Future Extensions

Potential later features include:

* graph database backend support
* graph ML clustering algorithms
* graph-based document recommendations
* topic evolution tracking across graph versions
* graph search indexing
* advanced pruning (RNG, mutual kNN)
* automatic bridge recomputation triggers
* LLM-powered label generation

These are **not required for the initial system**.

---

# Definition of Done (Global)

A phase is complete when:

* functionality works as specified in the phase
* database schema changes are applied via migration
* tests pass
* architecture rules are respected
* no out-of-scope files were modified
* dense matrices were never persisted
* graph slicing is SQL-based where required

---

# Notes for Cursor / Sonnet

When executing a phase:

1. Read the phase in full before starting
2. Summarize the phase goal before coding
3. List files you will create or modify
4. Confirm what is out of scope
5. After coding, summarize files changed and confirm definition of done
6. Do not proceed to the next phase without explicit approval
7. All work for graph phases happens in the `graph-engine` repository
8. Do not modify files in the main application repository during graph phase execution
