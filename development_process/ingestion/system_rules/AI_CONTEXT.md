# AI Context

When working in this repo/module:

Primary references:
- development_process/implementation_plans/IMPLEMENTATION_PLAN.md
- graph-specific phased plan files in development_process/implementation_plans/

Architectural constraints:
- development_process/system_rules/GRAPH_ARCHITECTURE_RULES.md

System overview:
- README.md

Visual architecture:
- development_process/graph_architecture_map.html

Rules:
- Implement one phase at a time
- Do not expand scope
- Do not add dependencies unless necessary
- Do not modify canonical document ingestion contracts unless explicitly requested
- Do not make fixed clusters canonical truth
- Sparse graph is canonical relational state; clusters, Z caches, layouts, and labels are derived artifacts
- Use CURSOR_CONSTITUTION.md

Core operating assumptions:
- This module shares the primary PostgreSQL database
- SQL owns persistence, subset selection, and subgraph extraction
- Python owns matrix math, graph build/rebuild logic, and derived view generation when needed
- Normal query paths must not require full-corpus recomputation
- Initial implementation should use embeddings/summaries only unless the phase explicitly adds feature layers

Current implementation direction:
- vault-scoped sparse semantic graphs
- optional cross-vault bridge edges
- optional derived Z/hierarchy caches
- D3-ready graph/group payloads
- labeling remains a separate concern unless explicitly in scope for a phase

Do not assume:
- tags, categories, and NER are available in phase 1
- graph composition implies merging vaults canonically
- semantic_tree_v2 is canonical for the new graph system