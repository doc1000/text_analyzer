from pathlib import Path

import pytest

from app.dataorchard.adapter import to_vb_hierarchy
from app.dataorchard.copy import load_forte_copy, load_pg_csv, synthetic_scale
from app.dataorchard.ids import capture_item_id

FORTE = Path(r"C:\Users\doste\projects\appworld-orchard-workspace\forte_second_brain_scraper\forte-building-a-second-brain")


@pytest.mark.skipif(not FORTE.exists(), reason="forte copy not present")
def test_forte_copy():
    docs = load_forte_copy(FORTE)
    assert len(docs) == 279
    d = docs[0]
    assert d["item_id"] == capture_item_id(d["metadata"]["url"])
    assert d["metadata"]["source"] == "forte-copy" and d["metadata"]["notes"] == ""
    assert d["metadata"]["tags"] == ["Building a Second Brain"]
    assert d["metadata"]["captured_at"].endswith("Z") and "&#" not in d["title"]
    assert len({x["item_id"] for x in docs}) == 279
    assert d["text"] and not d["text"].startswith("---")


def test_pg_csv(tmp_path):
    p = tmp_path / "docs.csv"
    p.write_bytes(
        "id,url,title,full_text,captured_at\n"
        '11111111-2222-3333-4444-555555555555,https://a.b/x,"Tit, le","line1\nline2 ü",2026-01-02 03:04:05.5+00\n'
        "66666666-2222-3333-4444-555555555555,https://a.b/y,T2,body,2026-01-02 03:04:05\n"
        .encode("utf-8"))
    docs = load_pg_csv(p)
    assert docs[0]["item_id"] == "vb:11111111-2222-3333-4444-555555555555"
    assert docs[0]["title"] == "Tit, le" and docs[0]["text"] == "line1\nline2 ü"
    assert docs[0]["metadata"]["captured_at"] == "2026-01-02T03:04:05.500000Z"
    assert docs[1]["metadata"]["captured_at"] == "2026-01-02T03:04:05.000000Z"


def test_synthetic_scale_deterministic():
    base = [{"item_id": "vb:a", "title": "A", "text": " ".join(f"w{i}" for i in range(100)),
             "metadata": {"url": "https://a.b/a", "captured_at": "2026-01-01T00:00:00Z",
                          "updated_at": "2026-01-01T00:00:00Z", "highlights": [], "notes": "",
                          "tags": ["x"], "source": "forte-copy"}}]
    a = synthetic_scale(base, n=50)
    b = synthetic_scale(base, n=50)
    assert a == b and len({d["item_id"] for d in a}) == 50
    ts = sorted(d["metadata"]["captured_at"] for d in a)
    assert ts[0] < "2025" or ts[0] < "2026-01" and ts[-1] > "2026-06"
    assert all(d["text"] != base[0]["text"] for d in a)


def _packed():
    def item(i, title):
        return {"type": "item", "name": title, "item_id": f"vb:{i}", "url": f"https://x/{i}",
                "captured_at": "2026-11-01T00:00:00Z", "summary": "s", "size": 1}
    return {"name": "root", "type": "root", "node_key": "r", "title": "R", "children": [
        {"type": "cluster", "name": "n1", "node_key": "member_1", "title": "Alpha", "summary": "sa",
         "item_count": 3, "children": [
             {"type": "cluster", "name": "n2", "node_key": "member_2", "title": "Beta", "summary": "sb",
              "item_count": 2, "children": [item("d1", "Doc 1"), item("d2", "Doc 2")]},
             {"type": "cluster", "name": "empty", "node_key": "member_e", "title": "E", "children": []},
             item("d3", "")]},
        item("d4", "Direct")]}


def test_adapter():
    out = to_vb_hierarchy(_packed(), 30)
    assert out["name"] == "Topics" and out["type"] == "root" and out["time_range_days"] == 30
    alpha, unc = out["children"]
    assert alpha["type"] == "topic" and alpha["name"] == "Alpha" and alpha["topic_id"] == "member_1"
    assert alpha["level"] == 1 and alpha["level_name"] == "Topic" and alpha["doc_count"] == 3
    assert alpha["summary"] == "sa" and alpha["size"] is None
    beta, d3 = alpha["children"]
    assert beta["level"] == 0 and beta["level_name"] == "Fine" and beta["doc_count"] == 2
    assert d3 == {"name": "(no title)", "doc_id": "d3", "url": "https://x/d3",
                  "captured_at": "2026-11-01T00:00:00Z", "size": 1, "type": "document"}
    assert beta["children"][0]["doc_id"] == "d1" and beta["children"][0]["type"] == "document"
    assert unc["topic_id"] == "uncategorized" and unc["doc_count"] == 1
    assert to_vb_hierarchy({"children": []}, 7)["children"] == []


def test_routes_import_is_lazy():
    import app.dataorchard.routes as r  # must import without fastapi
    assert callable(r.make_router)
