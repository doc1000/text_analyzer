# app/dataorchard

Usage-only Data Orchard code for Vault Bubbles (spec 6.3). The live engine is untouched:
nothing in `app/main.py`, `app/models.py`, `app/db.py`, migrations or the extension changes,
and Postgres is never contacted. `store.py` is a stub seam to orchard-db (phase 1).

Modules: `ids` (A8), `capture` (normalize/merge/CaptureWriter), `copy` (Forte copy, PG CSV, synthetic scale),
`adapter` (packed_tree -> /topics/hierarchy), `routes` (optional router, not mounted).

To mount the router (a human decision, not done here), add to `app/main.py`:

    app.include_router(__import__("app.dataorchard.routes", fromlist=["x"]).make_router(hier_fn, capture_fn, find_fn))

Tests: `python -m pytest -q tests/dataorchard` (no fastapi, sqlalchemy or database needed).
