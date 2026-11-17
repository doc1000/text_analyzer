"""Measure the Data Orchard success bars on DuckDB copies; write success_bars.json / .md.

Needs PYTHONPATH = treedb, orchard, orchard_harvest src dirs; HF_HUB_OFFLINE=1.
Sections (--only a,b,...): tree279, tree10k, captures, twoproc, known, titles.
Read-only on --forte-db / --synth-db (captures and twoproc work on temp copies).
"""
import argparse
import datetime as dt
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

EXP = Path(r"C:\Users\doste\projects\appworld-orchard-workspace\exp")


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--forte-db", default=str(EXP / "data" / "vb" / "forte.duckdb"))
    ap.add_argument("--synth-db", default=str(EXP / "data" / "vb" / "synth10k.duckdb"))
    ap.add_argument("--out-dir", default=str(EXP / "results"))
    ap.add_argument("--only", default="tree279,tree10k,captures,twoproc,known,titles")
    ap.add_argument("--runs", type=int, default=20)
    ap.add_argument("--m", type=int, default=500)
    ap.add_argument("--seconds", type=float, default=25)
    a = ap.parse_args(argv)
    from app.dataorchard import bars

    out = Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    jp = out / "success_bars.json"
    res = json.loads(jp.read_text(encoding="utf-8")) if jp.exists() else {}
    want = set(a.only.split(","))
    work = Path(tempfile.mkdtemp(prefix="vb-bars-", dir=EXP / "data" / "vb"))

    def copy_db(src, name):
        dst = work / name
        shutil.copy(src, dst)
        return dst

    def save():
        res["generated_at"] = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        jp.write_text(json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8")
        (out / "success_bars.md").write_text(bars.to_markdown(res), encoding="utf-8")

    try:
        if "tree279" in want:
            res["tree_open_279"] = bars.tree_open(a.forte_db, runs=a.runs)
            res["tree_open_279_anchored"] = bars.tree_open(a.forte_db, runs=a.runs, anchor="2026-08-15T00:00:00Z")
            save()
        if "tree10k" in want:
            res["tree_open_10k"] = bars.tree_open(a.synth_db, runs=a.runs)
            log = Path(a.synth_db).with_suffix(".log")
            if log.exists():
                res["build_10k"] = log.read_text(encoding="utf-8").strip().splitlines()[-1]
            save()
        if "captures" in want:
            res["captures"] = bars.captures(copy_db(a.forte_db, "captures.duckdb"), m=a.m)
            save()
        if "twoproc" in want:
            res["two_process"] = bars.two_process(copy_db(a.forte_db, "twoproc.duckdb"), seconds=a.seconds, pause=3.0)
            res["two_process_stress"] = bars.two_process(copy_db(a.forte_db, "twoproc2.duckdb"), seconds=a.seconds, pause=0.3)
            save()
        if "known" in want:
            res["known_item"] = bars.known_item(a.forte_db)
            save()
        if "titles" in want:
            res["title_quality"] = bars.title_quality(a.forte_db)
            save()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    print("wrote", jp)


if __name__ == "__main__":
    main()
