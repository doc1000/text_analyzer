# Graph Schema Plan

## Purpose

This document defines the **database schema plan** for the Graph / Topic Modeling subsystem.

It is intended to be used as a reference by **Opus** when generating the phased implementation plan and by **Sonnet** during phase execution.

This schema plan is intentionally conservative.

It is designed to:

- keep the graph subsystem in its **own PostgreSQL schema**
- preserve **sparse graph state** as the canonical relational structure
- avoid database growth from dense or pairwise relationship tables
- support vault-scoped graphs, bridge edges, graph versions, and derived hierarchy caches
- support phased implementation without destabilizing the existing system

---

## Non-Negotiable Storage Rules

These rules are mandatory.

### 1. The graph subsystem must live in its own schema
Use a dedicated PostgreSQL schema:

```sql
graph
```

Do **not** place new graph subsystem tables in `public`.

---

### 2. Sparse graph edges are the canonical relational state
The canonical persisted relational structure is:

- graph configuration
- graph versions
- sparse internal vault edges
- sparse cross-vault bridge edges
- graph jobs
- graph action log
- optional derived hierarchy caches

---

### 3. Dense distance matrices must never be persisted
Do **not** create tables for:

- dense similarity matrices
- dense distance matrices
- per-document full pairwise distance storage
- cached full pairwise candidate tables
- full graph adjacency expansion tables

These are explicitly out of scope **forever unless a future architectural decision replaces this plan**.

Reason:

- they blow up database size
- they do not match the sparse-graph architecture
- they make graph persistence scale poorly
- they undermine the design principle that matrices are **ephemeral compute artifacts**, not database objects

---

### 4. No per-pair candidate tables
Do **not** persist every potential candidate pair for graph building.

Allowed:

- sparse graph edges only
- local graph updates at runtime
- ephemeral candidate generation during build/update jobs

Not allowed:

- database tables storing all candidate neighbors for every document
- persistent pairwise comparison tables

---

### 5. No canonical full-graph tables
Do **not** create a second “full graph” representation in the database.

The persisted graph should be the **sparse graph only**.

Any induced graph slice, local hierarchy, or composed runtime view must be derived from sparse graph state.

---

## Architectural Position

The graph subsystem follows these principles:

- document-level features are canonical inputs
- sparse graph edges are canonical relational outputs
- clusters, trees, layouts, and labels are derived artifacts
- SQL stores and slices persisted graph state
- Python/application services perform graph math and graph rebuild logic
- normal graph queries must not require full graph recomputation

---

## Database Schema Boundary

The graph subsystem should use:

```sql
CREATE SCHEMA IF NOT EXISTS graph;
```

Existing schemas remain separate:

- `public` for documents, vaults, users, memberships, and app data
- `embedding` for embedding-related storage
- `semantic_tree_v2` for legacy or compatibility tree artifacts
- `graph` for the new canonical graph subsystem

The graph schema should reference existing core tables, but should not absorb them.

---

## Core Tables

The first version of the `graph` schema should be limited to the following tables.

### 1. `graph.graph_config`

Defines how a graph should be built.

This allows graph behavior to be versioned without hardcoding everything in application code.

#### Responsibilities

- identify which feature sources are used
- define graph build parameters
- support future weighting and pruning options

#### Suggested columns

```sql
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

#### Notes

- `embedding_source` points to the embedding source/model used for graph build.
- `feature_weights` is included now so the design remains extensible, even if early phases use only embeddings.
- `pruning_mode` can later support values such as `none`, `rng`, or another explicitly approved pruning rule.

---

### 2. `graph.vault_graph_version`

Stores a versioned graph build for a vault.

This is the anchor for all sparse internal edges in a vault.

#### Responsibilities

- preserve graph history
- support controlled rebuilds
- distinguish active vs stale vs failed versions

#### Suggested columns

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
```

#### Recommended statuses

- `building`
- `active`
- `superseded`
- `failed`
- `stale`

#### Recommended index

```sql
CREATE UNIQUE INDEX uq_active_vault_graph_version
  ON graph.vault_graph_version (vault_id)
  WHERE is_active = true;
```

---

### 3. `graph.vault_graph_edge`

Stores sparse **internal** graph edges for a vault graph version.

This is the core canonical sparse edge table.

#### Responsibilities

- store sparse undirected internal edges
- preserve per-version graph state
- allow cheap SQL slicing of graph subsets

#### Suggested columns

```sql
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
```

#### Notes

- Store each undirected edge only once, using canonical ordering as `doc_lo` and `doc_hi`.
- `contributions` is for sparse metadata only, not for large numeric payloads.
- `is_locked` allows future protection of user-strengthened edges.

#### Required indexes

```sql
CREATE INDEX idx_vault_graph_edge_version_lo
  ON graph.vault_graph_edge (graph_version_id, doc_lo);

CREATE INDEX idx_vault_graph_edge_version_hi
  ON graph.vault_graph_edge (graph_version_id, doc_hi);

CREATE INDEX idx_vault_graph_edge_vault_version
  ON graph.vault_graph_edge (vault_id, graph_version_id);

CREATE INDEX idx_vault_graph_edge_weight
  ON graph.vault_graph_edge (graph_version_id, weight DESC);
```

---

### 4. `graph.bridge_graph_edge`

Stores sparse **cross-vault** bridge edges separately from internal vault edges.

This is required for composed multi-vault views where the internal structure of each vault remains protected.

#### Responsibilities

- represent graph relationships between vaults
- support composed runtime views
- allow bridge recomputation without rewriting internal vault graphs

#### Suggested columns

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
```

#### Required indexes

```sql
CREATE INDEX idx_bridge_graph_edge_source_vault
  ON graph.bridge_graph_edge (source_vault_id);

CREATE INDEX idx_bridge_graph_edge_target_vault
  ON graph.bridge_graph_edge (target_vault_id);

CREATE INDEX idx_bridge_graph_edge_source_doc
  ON graph.bridge_graph_edge (source_doc_id);

CREATE INDEX idx_bridge_graph_edge_target_doc
  ON graph.bridge_graph_edge (target_doc_id);
```

#### Important rule

Bridge edges are not a replacement for internal graph edges.
They are a distinct edge class and must remain stored separately.

---

### 5. `graph.graph_action_log`

Stores user structural actions affecting graph state or future rebuild inputs.

#### Responsibilities

- preserve user intent
- distinguish immediate graph nudges from future rebuild signals
- provide auditability for graph-changing behavior

#### Suggested columns

```sql
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

#### Example action types

- `must_link`
- `weaken_link`
- `create_bridge`
- `assign_category_hint`
- `move_into_group`
- `layout_only`

---

### 6. `graph.graph_job`

Tracks graph build, rebuild, repair, and bridge-recompute jobs.

#### Responsibilities

- capture operational state
- support phased async execution later
- separate job execution from graph version storage

#### Suggested columns

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
```

#### Example job types

- `build_vault_graph`
- `repair_local_graph`
- `rebuild_bridges`
- `derive_hierarchy_cache`

---

### 7. Optional: `graph.graph_hierarchy_cache`

Stores derived hierarchy-compatible payloads.

This table exists only as a compatibility layer for graph-derived hierarchy views such as:

- local linkage/Z payloads
- circle-pack payloads
- local hierarchy views for D3

It must not become canonical truth.

#### Suggested columns

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

#### Example `cache_type` values

- `z_linkage`
- `circle_pack_payload`
- `local_hierarchy_payload`

---

## What Must Not Be Added

The following are explicitly prohibited from this schema plan.

### Prohibited table categories

Do not add any table for:

- dense distance matrices
- dense similarity matrices
- all-pairs document scores
- persistent pairwise candidate tables
- full graph copies
- redundant adjacency expansion tables
- canonical cluster tables as the primary relational state

### Why these are prohibited

- they create unbounded database growth
- they do not match the sparse graph architecture
- they make rebuild logic harder to reason about
- they encourage the wrong persistence model

---

## Query Philosophy

The schema must support cheap normal graph queries.

Example query flow:

1. SQL selects a permitted set of document IDs.
2. SQL looks up the active graph version for the vault.
3. SQL extracts sparse edges for the selected set from `graph.vault_graph_edge`.
4. SQL optionally joins bridge edges from `graph.bridge_graph_edge`.
5. Python/application code derives any local view payloads needed.

This means:

- SQL owns persistence and graph slicing
- Python owns graph math and derived structures
- no full graph rebuild is triggered during normal requests

---

## Naming and Design Conventions

Use these conventions consistently.

### 1. Separate internal and bridge edges
Never mix them in one edge table.

### 2. Use graph version IDs everywhere
Do not rely on hidden implicit “current graph” logic.

### 3. Use canonical undirected edge ordering
Store internal edges as:

- `doc_lo`
- `doc_hi`

with a uniqueness constraint per graph version.

### 4. Keep JSONB narrow
Use JSONB for flexible metadata only.
Do not store large numeric arrays or heavy pairwise data in JSONB.

### 5. Treat hierarchy caches as disposable
They are helpful, but not canonical.

---

## Recommended Phase Mapping

This schema plan is meant to align with a phased implementation plan.

### Phase 1
- create `graph` schema
- create `graph.graph_config`

### Phase 2
- create `graph.vault_graph_version`
- create `graph.vault_graph_edge`

### Phase 3
- add SQL extraction helpers and application query paths using those tables

### Phase 4
- create `graph.graph_job`
- create `graph.graph_action_log`

### Phase 6
- create `graph.bridge_graph_edge`

### Phase 7
- optionally create `graph.graph_hierarchy_cache`

This order keeps schema risk low and lets the graph system become useful early.

---

## Guidance for Opus

When generating the implementation plan:

- keep the `graph` schema isolated from `public`
- introduce tables incrementally by phase
- never propose dense pairwise persistence
- never propose full graph duplication tables
- treat sparse graph edges as the canonical relational layer
- treat hierarchy/Z as optional derived caches only
- keep feature-layer expansion out of the base schema unless explicitly required later

---

## Final Rule

If a proposed schema addition would significantly increase storage by representing all or most document pairs, it should be rejected.

The graph subsystem is designed around:

- document-level canonical inputs
- sparse relational persistence
- derived runtime views

not around dense pairwise storage.

