"""Build an orchard-db DuckDB file from the Forte copy (or a human-made PG CSV export).

  python scripts/dataorchard_build_copy.py --out C:/.../data/vb/forte.duckdb [--pg-csv docs.csv]

Refuses to overwrite an existing file unless --force. Never touches Postgres.
Set HF_HUB_OFFLINE=1 and PYTHONPATH to the treedb, orchard and orchard_harvest src dirs.
"""
import argparse
import os
import sys
import time
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DEFAULT_FORTE = r"C:\Users\doste\projects\appworld-orchard-workspace\forte_second_brain_scraper\forte-building-a-second-brain"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--forte", default=DEFAULT_FORTE)
    ap.add_argument("--pg-csv", default=None)
    ap.add_argument("--alias", default="vb/topic")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--synthetic", type=int, default=0, help="replace docs by N synthetic perturbed copies")
    ap.add_argument("--max-chars", type=int, default=0, help="embedding max_chars override (new vector space)")
    ap.add_argument("--fake-encoder", action="store_true", help="hashed bag-of-words instead of MiniLM (says so)")
    a = ap.parse_args(argv)
    from app.dataorchard import copy, engine

    out = Path(a.out)
    if out.exists():
        if not a.force:
            sys.exit(f"{out} exists; pass --force to replace it")
        out.unlink()
    out.parent.mkdir(parents=True, exist_ok=True)
    docs = copy.load_pg_csv(a.pg_csv) if a.pg_csv else copy.load_forte_copy(a.forte)
    if a.synthetic:
        docs = copy.synthetic_scale(docs, n=a.synthetic)
    persp, enc = None, None
    if a.max_chars or a.fake_encoder:
        from orchard.perspectives import minilm_space_name, topic_perspective

        tag = ("fake" if a.fake_encoder else "") + (f"c{a.max_chars}" if a.max_chars else "")
        persp = topic_perspective(version=f"1-{tag}", embedding={
            "space": (f"fake-hash384/{tag}" if a.fake_encoder else minilm_space_name(template_version=f"tt1{tag}")),
            **({"max_chars": a.max_chars} if a.max_chars else {})})
    if a.fake_encoder:
        from app.dataorchard.fake_encoder import FakeEncoder

        enc = FakeEncoder()
    t = time.time()
    res = engine.rebuild(out, docs, alias=a.alias, perspective=persp, encoder=enc)
    print(f"built {len(docs)} docs into {out} in {time.time() - t:.1f}s; artifact {res.artifact_sha256[:12]}")


if __name__ == "__main__":
    main()
