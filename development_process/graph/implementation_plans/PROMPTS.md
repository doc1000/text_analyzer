
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