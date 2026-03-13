
## Implementation Prompts
Follow development_process\graph\system_rules\GRAPH_AI_CONTEXT.md.
 Read development_process\graph\system_rules\GRAPH_ARCHITECTURE_RULES.md

Implement Phase 1 from development_process\graph\implementation_plans\IP2.md only.

Before coding:
- summarize the phase goal
- list the input files you will use
- list what is out of scope

During implementation:
- modify only files listed in the phase
- do not create extra modules unless required

After coding:
- summarize files changed
- confirm definition of done
- confirm out-of-scope items were not touched






## OPUS IMPLEMENTATION PLAN PROMPT

You are generating the detailed implementation phases for the Graph / Topic Modeling subsystem.

Your output will update:

development_process/graph/implementation_plans/IP_GRAPH.md

This repository follows a strict AI-assisted development workflow.

You must read and follow the architecture rules and scope definitions located here:

development_process/graph/system_rules/GRAPH_AI_CONTEXT.md
development_process/graph/system_rules/GRAPH_ARCHITECTURE.md
development_process/graph/system_rules/GRAPH_MODULE_SCOPE.md

You should also consult the visual architecture map:

graph_architecture_map.html

This map shows the full system architecture including:
- document sources
- feature layers
- sparse graph construction
- bridge edges
- subgraph extraction
- derived hierarchy/Z layer
- UI payload generation

The goal is to create a **safe phased implementation plan** that Sonnet can execute incrementally.

---

SYSTEM REQUIREMENTS

The graph system must support:

- vault-scoped sparse semantic graphs
- graph versioning
- subgraph extraction
- incremental graph updates
- cross-vault graph composition
- derived hierarchy/Z compatibility
- UI graph payload generation

The canonical graph state consists of:

- vault graph configuration
- graph versions
- sparse graph edges
- bridge edges
- document feature references

Clusters, Z arrays, trees, layouts, and labels are **derived artifacts**.

---

IMPLEMENTATION STRATEGY

Phases must be designed so that:

1. Each phase compiles and runs independently.
2. No phase requires the entire system to be completed first.
3. Phases introduce minimal schema risk.
4. Feature enrichment pipelines (tags, categories, NER) are optional later layers.
5. Embeddings-only graph construction works in Phase 1.

---

PHASE STRUCTURE REQUIREMENTS

Each phase must contain the following sections:

PHASE GOAL  
Explain what the phase accomplishes.

FILES IN SCOPE  
List only files that can be modified.

INPUTS  
Data sources used by this phase.

OUTPUTS  
Artifacts produced by this phase.

DATABASE CHANGES  
Any migrations required.

IMPLEMENTATION TASKS  
Step-by-step tasks Sonnet should perform.

DEFINITION OF DONE  
What must be working to complete the phase.

OUT OF SCOPE  
Explicitly list what must not be implemented yet.

---

PHASE ORDER

Design the phases in this order:

Phase 1 — Graph module foundations  
Phase 2 — Graph persistence and versioning  
Phase 3 — Subgraph extraction  
Phase 4 — Incremental graph updates  
Phase 5 — Graph view payload builders  
Phase 6 — Cross-vault graph composition  
Phase 7 — Derived hierarchy / Z compatibility  
Phase 8 — Feature layer expansion  
Phase 9 — Labeling module

Each phase must only depend on previous phases.

---

IMPLEMENTATION CONSTRAINTS

Follow these rules strictly:

- PostgreSQL is the system of record.
- Dense distance matrices must never be persisted.
- Sparse graph edges are canonical relational state.
- Subgraph extraction must happen in SQL.
- Graph rebuilds must not occur during normal requests.
- Label generation must not block graph rendering.

---

CODE ORGANIZATION

The graph subsystem will contain these modules:

graph_core
graph_features
graph_views
graph_labels

Only introduce modules when the phase explicitly requires them.

---

DATABASE TABLES

use the schema plan in development_process/graph/graph_schema_plan.md
You may introduce tables incrementally across phases.

---

OUTPUT REQUIREMENT

Update IP_GRAPH.md with:

1. the full phase list
2. detailed instructions for each phase
3. file scope boundaries
4. database migration guidance
5. test expectations

The plan must be written so that **Sonnet can execute each phase without guessing**.

Do not generate code yet.

Generate the full structured implementation plan only.



## Amendment to implementation plan

Amend the existing graph implementation plan document rather than rewriting it from scratch.

The graph/topic modeling system is no longer being implemented as an internal module inside the main application repo.

It must now be treated as a **separate repository** with a defined integration contract to the main system.

Update the existing implementation plan to reflect this new repo boundary while preserving the existing phase sequence and as much of the current plan structure as possible.

Do not discard the current plan. Amend it.

---

NEW ARCHITECTURAL POSITION

The graph/topic modeling system will be developed in a separate repository as:

- an importable Python package
- with a lightweight API/service wrapper
- using the same PostgreSQL database
- writing only to a dedicated `graph` schema
- reading from a narrow, explicit contract in the main system

The graph system is still responsible for:

- vault-scoped sparse semantic graphs
- graph versioning
- sparse internal edges
- bridge edges
- subgraph extraction
- incremental updates
- derived hierarchy / Z compatibility
- UI graph payload generation

---

REPO BOUNDARY RULES

Amend the plan so that it explicitly reflects these ownership boundaries:

Main application owns:
- public.documents
- public.vaults
- public.vault_memberships
- users / auth / app permissions
- embedding generation and feature enrichment pipelines
- upstream semantic feature production

Graph repo owns:
- graph schema tables
- graph build/update logic
- feature fusion logic
- sparse graph persistence
- bridge edge persistence
- subgraph extraction logic
- hierarchy/Z derived caches
- graph view payload builders
- optional labeling subsystem

The graph repo must not take over:
- ingestion
- OCR
- parsing
- generic enrichment pipelines
- user/auth ownership
- general app orchestration

---

FEATURE CONTRACT RULES

Amend the plan to reflect the following feature model:

- feature extraction occurs outside the graph repo
- feature fusion occurs inside the graph repo
- the graph repo consumes named feature layers through references to approved source tables/views/adapters
- the graph repo stores graph configuration describing:
  - feature layer names
  - source locations
  - join keys
  - metrics
  - weights
  - pruning mode
  - graph build parameters

The graph repo must NOT require all future features to exist initially.

Initial required layer:
- document embedding

Initial optional layer:
- summary embedding

Future optional layers:
- tags
- category score vectors
- NER/entity profiles
- classifier outputs
- user-intent signals

The plan must explicitly preserve the ability to operate with embeddings-only in early phases.

---

EXECUTION MODES

Amend the implementation plan to reflect two supported execution modes:

1. Import mode
   - graph repo is imported directly as a Python package by the main system

2. Service mode
   - graph repo exposes a lightweight internal API wrapper for graph operations

The implementation plan should prefer:
- import mode first
- service wrapper later or in parallel only when useful

Do not redesign the whole system as a full distributed microservice platform.

---

DATABASE CONTRACT RULES

The graph repo uses the shared PostgreSQL database.

It writes only to:
- graph.*

It reads from:
- a narrow approved contract from the main app, typically:
  - public.documents
  - public.vaults
  - public.vault_memberships
  - approved embedding / feature tables or views

Dense distance matrices, full graph tables, and per-pair candidate tables remain prohibited.

Do not weaken this rule.

---

PLAN UPDATE REQUIREMENTS

Update the existing implementation plan to add or amend sections covering:

1. Repo boundary
2. Main-app vs graph-repo ownership
3. Import mode vs service mode
4. Feature provider contract
5. Shared database / separate schema contract
6. Updated file/module assumptions for the separate repo
7. Any phase notes that must change because the graph system is now a separate repo

Add a short section near the top of the implementation plan:
Repository and Integration Model

with:

repo ownership

execution modes

database contract

feature contract
Preserve the current phase sequence unless a change is absolutely required.

Do not expand scope.

Do not generate code.

Only amend the implementation plan document.


Below is a **companion addendum** designed to update your architecture documents without replacing them. It instructs Opus to **amend the existing architecture docs in place** so they reflect the new **separate graph-engine repo** model.

This addendum should apply to:

```text
development_process/graph/system_rules/

GRAPH_AI_CONTEXT.md
GRAPH_ARCHITECTURE.md
GRAPH_MODULE_SCOPE.md
```

---

# Addendum for Opus — Amend Graph Architecture Documents

Use this instruction to **update the existing architecture documents** rather than replacing them.

Do not discard the current architecture documentation.

Amend it to reflect the new **repository boundary and integration model**.

---

# Architectural Change

The graph/topic modeling subsystem is no longer implemented as an internal module inside the main application repository.

It will now be implemented in a **separate repository**.

The architecture documentation must be updated to reflect:

* separate repository ownership
* a shared PostgreSQL database
* a dedicated `graph` schema
* a strict integration contract with the main system

The goal is to **strengthen isolation** without introducing unnecessary distributed-system complexity.

---

# Repository Structure

Update the architecture docs so they reflect the following structure.

Main application repository:

Responsible for:

* documents
* vaults
* vault memberships
* users / permissions
* ingestion
* parsing
* OCR
* embedding generation
* feature enrichment pipelines
* UI and application workflows

Graph repository:

Responsible for:

* graph construction logic
* feature-layer fusion
* sparse graph persistence
* bridge edges
* graph versioning
* subgraph extraction
* derived hierarchy/Z compatibility
* graph payload generation for UI
* optional labeling module

The graph repository must not take ownership of:

* ingestion
* OCR
* parsing
* authentication
* user management
* generic enrichment pipelines

---

# Database Contract

The graph system continues to use the **same PostgreSQL database**.

However the ownership model must be clearly defined.

Graph repository writes only to:

```
graph.*
```

Graph repository reads from a limited contract in the main system such as:

```
public.documents
public.vaults
public.vault_memberships
approved embedding tables
approved feature tables or views
```

The architecture documentation must reinforce the rule that the graph system should not arbitrarily read the entire application schema.

The recommended pattern is:

* read through stable tables
* read through dedicated views
* or use feature-provider adapters

---

# Feature Layer Contract

The architecture documents must clearly distinguish:

Feature extraction
vs
Feature fusion

Feature extraction happens **outside the graph repository**.

Examples:

* embeddings
* tag extraction
* category classification
* NER/entity detection
* other enrichment pipelines

Feature fusion happens **inside the graph repository**.

The graph system must support configurable feature layers.

Graph configuration must specify:

* feature layer name
* source table or view
* join key
* similarity metric
* feature weight

The graph system must be able to operate with **only document embeddings initially**.

Additional feature layers must remain optional.

---

# Execution Modes

The architecture docs must describe two supported execution modes.

Import Mode

The graph repository can be imported as a Python module.

Example:

```python
from graph_engine import GraphService
```

Service Mode

The graph repository may optionally expose a lightweight API wrapper.

Example operations:

```
POST /graph/build-vault
POST /graph/extract-subgraph
POST /graph/compose-view
POST /graph/rebuild-bridges
```

The architecture should prefer **import mode initially**.

Do not redesign the system as a distributed microservice platform.

---

# Graph Persistence Model

The canonical relational state remains unchanged.

Canonical state:

* graph configuration
* vault graph versions
* sparse internal graph edges
* cross-vault bridge edges
* graph jobs
* graph action log

Derived artifacts:

* clusters
* hierarchical views
* Z linkage arrays
* semantic trees
* UI layouts
* topic labels

The architecture docs must reinforce that sparse graph edges are the **canonical relational structure**.

---

# Storage Rules (Must Remain Explicit)

The architecture documents must continue to enforce the following rules:

The graph subsystem must never persist:

* dense similarity matrices
* dense distance matrices
* full pairwise document tables
* candidate pair tables
* full graph adjacency expansions

Sparse graph edges are the only persisted graph relationships.

All dense matrices must remain **ephemeral compute artifacts**.

---

# Integration Boundary

Add a section titled:

```
Graph System Integration Contract
```

This section should define:

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

---

# Required Documentation Updates

Update these files accordingly:

GRAPH_AI_CONTEXT.md

Add guidance that the graph system is now implemented in a separate repository and interacts with the main system through a defined contract.

GRAPH_ARCHITECTURE.md

Add sections describing:

* repository separation
* database contract
* feature-layer contract
* execution modes
* integration boundary

GRAPH_MODULE_SCOPE.md

Update module descriptions so they refer to components within the **graph repository** rather than internal modules in the main application.

---

# Plan Consistency Rule

The architecture documentation must remain consistent with:

```
development_process/graph/implementation_plans/IP_GRAPH.md
```

The architecture must describe the same module boundaries and persistence model used by the implementation plan.

Do not expand scope.

Do not introduce new architectural subsystems.

Only amend the documents to reflect the new **separate repository architecture**.


Additionally create a new document:

development_process/graph/GRAPH_SYSTEM_INTEGRATION_CONTRACT.md

This document defines the contract between:

- the main application repository
- the graph engine repository

The document must clearly define:

SYSTEM OWNERSHIP

Main application repository owns:
- documents
- vaults
- vault memberships
- users and permissions
- ingestion and parsing
- embedding generation
- feature enrichment pipelines

Graph repository owns:
- graph configuration
- graph build/update logic
- sparse graph persistence
- bridge edge persistence
- graph versions
- subgraph extraction
- graph payload generation
- optional hierarchy/Z caches
- optional labeling module

DATABASE CONTRACT

The graph system writes only to:

graph.*

The graph system reads only from approved upstream tables or views such as:

public.documents
public.vaults
public.vault_memberships
approved embedding tables
approved feature tables or views

The document must explicitly reinforce the rule that the graph system must not read arbitrary application tables.

FEATURE CONTRACT

Define how feature layers are passed into the graph system.

Feature extraction occurs outside the graph repository.

The graph repository consumes named feature layers through configuration specifying:

- feature name
- source table/view
- join key
- similarity metric
- feature weight

The graph system must operate with embeddings-only initially.

Additional layers must remain optional.

EXECUTION MODES

Document two supported execution modes:

Import Mode
The graph repository is imported directly as a Python module.

Service Mode
The graph repository exposes a lightweight API wrapper.

The system should prefer import mode initially.

API SURFACE

Define the expected graph operations such as:

build_vault_graph
update_graph_for_document
extract_subgraph
compose_view
rebuild_bridges

These operations may be called either via direct import or via API.

OUTPUTS

The graph system produces:

- sparse graph edges
- graph versions
- subgraph payloads
- composed graph payloads
- optional hierarchy caches

The contract must reinforce that clusters, trees, layouts, and labels are derived artifacts.

STORAGE RULES

The contract must explicitly repeat the storage rule:

Dense similarity matrices, distance matrices, full pairwise tables, and candidate pair tables must never be persisted.

Only sparse graph edges are stored.

Proposed directory layout for graph-engine repo:
graph-engine/
├─ README.md
├─ pyproject.toml
├─ .env.example
├─ .gitignore
├─ alembic.ini
├─ CURSOR_CONSTITUTION.md
├─ development_process/
│  ├─ implementation_plans/
│  │  └─ IP_GRAPH.md
│  ├─ system_rules/
│  │  ├─ GRAPH_AI_CONTEXT.md
│  │  ├─ GRAPH_ARCHITECTURE.md
│  │  └─ GRAPH_MODULE_SCOPE.md
│  ├─ GRAPH_SYSTEM_INTEGRATION_CONTRACT.md
│  └─ graph_architecture_map.html
├─ migrations/
│  ├─ env.py
│  └─ versions/
├─ src/
│  └─ graph_engine/
│     ├─ __init__.py
│     ├─ config/
│     │  ├─ settings.py
│     │  └─ feature_registry.py
│     ├─ db/
│     │  ├─ base.py
│     │  ├─ session.py
│     │  ├─ models/
│     │  │  ├─ graph_config.py
│     │  │  ├─ vault_graph_version.py
│     │  │  ├─ vault_graph_edge.py
│     │  │  ├─ bridge_graph_edge.py
│     │  │  ├─ graph_job.py
│     │  │  ├─ graph_action_log.py
│     │  │  └─ graph_hierarchy_cache.py
│     │  └─ repositories/
│     │     ├─ graph_config_repo.py
│     │     ├─ graph_version_repo.py
│     │     ├─ graph_edge_repo.py
│     │     ├─ bridge_edge_repo.py
│     │     ├─ graph_job_repo.py
│     │     └─ hierarchy_cache_repo.py
│     ├─ contracts/
│     │  ├─ inputs.py
│     │  ├─ outputs.py
│     │  └─ permissions.py
│     ├─ features/
│     │  ├─ base.py
│     │  ├─ embedding_provider.py
│     │  ├─ summary_embedding_provider.py
│     │  └─ fusion.py
│     ├─ graph_core/
│     │  ├─ types.py
│     │  ├─ builder.py
│     │  ├─ updater.py
│     │  ├─ subgraph.py
│     │  ├─ composition.py
│     │  ├─ bridges.py
│     │  ├─ pruning.py
│     │  └─ hierarchy.py
│     ├─ services/
│     │  ├─ graph_service.py
│     │  ├─ build_service.py
│     │  ├─ update_service.py
│     │  ├─ extract_service.py
│     │  └─ compose_service.py
│     ├─ views/
│     │  ├─ force_payload.py
│     │  ├─ grouped_payload.py
│     │  └─ hierarchy_payload.py
│     ├─ labels/
│     │  ├─ base.py
│     │  ├─ label_service.py
│     │  └─ cache.py
│     ├─ api/
│     │  ├─ app.py
│     │  ├─ deps.py
│     │  └─ routes/
│     │     ├─ graphs.py
│     │     ├─ subgraphs.py
│     │     └─ composition.py
│     ├─ jobs/
│     │  ├─ build_jobs.py
│     │  ├─ repair_jobs.py
│     │  └─ bridge_jobs.py
│     └─ utils/
│        ├─ ids.py
│        ├─ timing.py
│        └─ logging.py
├─ tests/
│  ├─ unit/
│  │  ├─ test_builder.py
│  │  ├─ test_fusion.py
│  │  ├─ test_subgraph.py
│  │  └─ test_composition.py
│  ├─ integration/
│  │  ├─ test_graph_persistence.py
│  │  ├─ test_extract_subgraph.py
│  │  ├─ test_bridge_edges.py
│  │  └─ test_hierarchy_cache.py
│  └─ fixtures/
│     ├─ documents.py
│     └─ graphs.py
└─ scripts/
   ├─ build_vault_graph.py
   ├─ rebuild_bridges.py
   └─ backfill_graph_config.py

For phase 1, the minimum useful subset is:
   src/graph_engine/
  db/
  features/
  graph_core/
  services/
  views/
tests/
migrations/
development_process/


Then add:

api/ when you want service mode

labels/ later

jobs/ when async/background work becomes real


package naming recommendation: graph_engine
