"""
graph_engine.views — UI-ready payload builders for graph data.

Converts SubgraphResult slices into JSON-serializable payloads
suitable for D3 force layouts and grouped visualizations.

No layout computation is performed here; that is the frontend's
responsibility. These modules only shape the data.
"""
