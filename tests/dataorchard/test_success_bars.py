"""Small-scale runs of the success-bar measurements with the fake encoder (no model download).

Needs treedb, orchard and orchard_harvest on PYTHONPATH (skipped otherwise). Temp DuckDB files only.
"""
import random

import pytest

pytest.importorskip("treedb")
pytest.importorskip("orchard.cultivate_db")
pytest.importorskip("orchard_harvest")

from app.dataorchard import bars, engine  # noqa: E402
from app.dataorchard.fake_encoder import FakeEncoder  # noqa: E402

TOPICS = {
    "garden": "soil compost seeds tomato pruning greenhouse watering mulch harvest orchard",
    "kernel": "scheduler memory paging interrupt syscall driver mutex thread kernel cache",
    "music": "chord melody rhythm tempo harmony guitar scale octave rehearsal composition",
    "cooking": "simmer saute dough whisk braise marinade oven spice broth caramelize",
}


def _docs(n=48):
    rng = random.Random(1)
    out = []
    for i in range(n):
        name, vocab = list(TOPICS.items())[i % 4]
        w = vocab.split()
        text = ". ".join(" ".join(rng.choice(w) for _ in range(12)) + f" {name}{i}" for _ in range(6)) + "."
        out.append({"item_id": f"vb:seed{i:03d}", "title": f"{name.title()} notes {i} {rng.choice(w)}", "text": text,
                    "metadata": {"url": f"https://seed.example/{i}", "captured_at": f"2026-11-{1 + i % 15:02d}T00:00:00Z",
                                 "updated_at": "2026-11-01T00:00:00Z", "highlights": [f"highlight {name} {i}"] if i % 3 == 0 else [],
                                 "notes": "", "tags": [], "source": "forte-copy"}})
    return out


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    p = tmp_path_factory.mktemp("bars") / "vb.duckdb"
    engine.rebuild(p, _docs(), encoder=FakeEncoder())
    return p


def _copy(db, tmp_path):
    import shutil

    d = tmp_path / "copy.duckdb"
    shutil.copy(db, d)
    return d


def test_payloads_cover_modes_and_shapes():
    ps = bars.make_payloads(500)
    assert len(ps) == 500 and len({tuple(sorted(p)) for p in ps}) >= 2
    assert {p.get("mode") for p in ps} == {"page", "selection", "note", None}
    assert max(len(p.get("text") or p.get("full_text")) for p in ps) == 1_000_000
    assert len(bars.expected_docs(ps)) < 500  # repeated urls merge


def test_captures_round_trip(db, tmp_path):
    r = bars.captures(_copy(db, tmp_path), m=60, threads=4, encoder=FakeEncoder())
    assert r["saved"] == r["submitted"] == 60, r["errors"]
    assert r["field_mismatches"] == 0, r["mismatch_examples"]
    assert r["unique_items_read_back"] == r["unique_items_expected"]
    assert r["placed_in_tree"] == r["unique_items_expected"]


def test_captures_without_insert(db, tmp_path):
    r = bars.captures(_copy(db, tmp_path), m=30, threads=2, insert_on_capture=False, encoder=FakeEncoder())
    assert r["saved"] == 30 and r["field_mismatches"] == 0 and r["placed_in_tree"] == 0


def test_tree_open_and_hierarchy(db):
    r = bars.tree_open(db, runs=2)
    assert set(r["windows"]) == {"7", "30", "90", "365", "all"}
    w = r["windows"]["all"]
    assert w["items_in_window"] == 48 and w["payload_bytes_http"] > 1000
    h = engine.hierarchy(db, None)
    assert h["type"] == "root" and h["children"]


def test_known_item_title_queries(db):
    r = bars.known_item(db, n=20, encoder=FakeEncoder())
    t = r["types"]["a_title"]
    assert t["keyword_only"]["top10"] >= 0.8 and t["hybrid"]["top10"] >= 0.8
    assert set(r["types"]) == {"a_title", "b_mid_body_sentence", "c_key_terms_minus_title",
                               "d_body_sentence_minus_title_terms", "e_highlight_or_notes"}


def test_title_quality(db):
    q = bars.title_quality(db, sample=10)
    assert q["clusters"] > 0 and 0 <= q["empty_title_share"] <= 1 and len(q["sample"]) == min(10, q["clusters"])


def test_two_process_short(db, tmp_path, monkeypatch):
    monkeypatch.setenv("DO_FAKE_ENCODER", "1")
    r = bars.two_process(_copy(db, tmp_path), seconds=4)
    assert "child_error" not in r, r.get("child_error")
    assert r["writer"]["ok"] > 0 and r["reader"]["requests"] > 0
    assert r["writer"]["failures"] == 0


def test_markdown_renders(db):
    md = bars.to_markdown({"generated_at": "x", "title_quality": bars.title_quality(db, sample=3)})
    assert "Criterion 3" in md and "do not cover" in md
