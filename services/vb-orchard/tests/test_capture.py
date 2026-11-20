import threading

from vb_orchard.capture import CaptureWriter, merge, normalize_payload
from vb_orchard.ids import capture_item_id, normalize_url


def test_normalize_url_and_id():
    assert normalize_url("HTTPS://Example.COM/") == "https://example.com"
    assert normalize_url("https://Example.com/a/b/#frag") == "https://example.com/a/b/"
    assert normalize_url("https://example.com/a?x=1#z") == "https://example.com/a?x=1"
    assert capture_item_id("https://X.com/") == capture_item_id("https://x.com#top")
    iid = capture_item_id("https://x.com/p")
    assert iid.startswith("vb:") and len(iid) == 3 + 16


def test_normalize_extension_shapes():
    page = normalize_payload({"url": "https://a.b/p", "title": " T ", "text": "body", "mode": "page",
                              "tags": ["z", "a", "a"], "captured_at": "2026-01-02T03:04:05+02:00"})
    assert page["full_text"] == "body" and page["tags"] == ["a", "z"]
    assert page["captured_at"].startswith("2026-01-02T01:04:05") and page["captured_at"].endswith("Z")
    sel = normalize_payload({"url": "https://a.b/p", "mode": "selection",
                             "text": "[Header] hi\n\n[captured]\nthe selected bit"})
    assert sel["highlight"] == "the selected bit" and sel["full_text"] == ""
    note = normalize_payload({"url": "note://t", "mode": "note", "text": "my note"})
    assert note["notes"] == "my note" and note["full_text"] == ""
    spec = normalize_payload({"url": "https://a.b", "full_text": "x", "highlight": "h", "notes": "n"})
    assert (spec["full_text"], spec["highlight"], spec["notes"]) == ("x", "h", "n")


def test_merge_rules():
    d1 = merge(None, {"url": "https://a.b/p", "title": "T", "full_text": "short", "highlight": "h1",
                      "notes": "n1", "tags": ["b"], "captured_at": "2026-02-01T00:00:00Z"},
               now="2026-02-01T00:00:01.000000Z")
    assert d1["metadata"]["source"] == "vaultbubbles"
    d2 = merge(d1, {"url": "https://a.b/p#x", "full_text": "a longer body", "highlight": "h2",
                    "notes": "n2", "tags": ["a"], "captured_at": "2026-01-01T00:00:00Z"},
               now="2026-03-01T00:00:00.000000Z")
    assert d2["text"] == "a longer body"
    assert d2["metadata"]["highlights"] == ["h1", "h2"]
    assert d2["metadata"]["notes"] == "n1\nn2"
    assert d2["metadata"]["tags"] == ["a", "b"]
    assert d2["metadata"]["captured_at"].startswith("2026-01-01")
    assert d2["metadata"]["updated_at"].startswith("2026-03-01")
    d3 = merge(d2, {"url": "https://a.b/p", "full_text": "tiny", "highlight": "h1", "notes": "n2"})
    assert d3["text"] == "a longer body" and d3["metadata"]["highlights"] == ["h1", "h2"]
    assert d3["metadata"]["notes"] == "n1\nn2"
    d4 = merge(d3, {"url": "https://a.b/p", "full_text": ""})
    assert d4["text"] == "a longer body"


def test_writer_500_captures_8_threads():
    store = {}
    lock = threading.Lock()
    calls = []

    def write_batch(docs):
        with lock:
            calls.append(len(docs))
            for d in docs:
                store[d["item_id"]] = d

    w = CaptureWriter(write_batch)
    submitted = {}
    futs = []
    flock = threading.Lock()

    def worker(t):
        for i in range(t * 63, min(500, (t + 1) * 63)):
            if i == 7:
                text = "x" * 1_000_000  # 1 MB page
            elif i % 3 == 0:
                text = f"naïve café 日本語 🙂 #{i}"
            else:
                text = f"body {i}"
            url = f"https://Site{i % 400}.example/page/{i % 400}"  # 100 repeated urls
            payload = {"url": url, "title": f"Titel ünï {i % 400}", "full_text": text,
                       "highlight": f"hl{i}", "notes": "" if i % 5 == 0 else f"note{i}",
                       "tags": [f"t{i % 3}"], "captured_at": "2026-05-01T00:00:00Z"}
            f = w.submit(payload)
            with flock:
                futs.append((f, payload))

    ts = [threading.Thread(target=worker, args=(t,)) for t in range(8)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert w.flush(30)
    w.close()
    assert len(futs) == 500
    ids = {f.result(5) for f, _ in futs}
    assert all(f.result() == f.item_id for f, _ in futs)
    assert ids == set(store)
    assert len(store) == 400
    # every payload's fields are present in its merged doc
    for f, p in futs:
        d = store[f.item_id]
        md = d["metadata"]
        assert p["highlight"] in md["highlights"]
        assert p["tags"][0] in md["tags"]
        if p["notes"]:
            assert p["notes"] in md["notes"].split("\n")
        assert md["url"] and md["source"] == "vaultbubbles"
        assert len(d["text"]) >= len(p["full_text"])
        assert d["title"] == p["title"]
    assert max(len(d["text"]) for d in store.values()) == 1_000_000
    assert any("日本語 🙂" in d["text"] for d in store.values())
    assert sum(calls) >= 400


def test_writer_returns_legacy_item_id_when_merged():
    legacy = {"item_id": "vb:11111111-2222-3333-4444-555555555555", "title": "Old", "text": "short",
              "metadata": {"url": "https://a.b/legacy", "captured_at": "2026-01-01T00:00:00.000000Z",
                           "updated_at": "2026-01-01T00:00:00.000000Z", "highlights": [], "notes": "",
                           "tags": [], "source": "vaultbubbles"}}
    written = []
    w = CaptureWriter(lambda docs: written.extend(docs),
                      lookup=lambda nurl, iid: legacy if nurl == "https://a.b/legacy" else None)
    f = w.submit({"url": "https://A.b/legacy#x", "full_text": "a longer body"})
    assert f.result(5) == legacy["item_id"] and f.item_id == legacy["item_id"]
    assert written[0]["item_id"] == legacy["item_id"] and written[0]["text"] == "a longer body"
    g = w.submit({"url": "https://a.b/legacy", "highlight": "h"})  # cached: still the legacy id
    assert g.result(5) == legacy["item_id"]
    h = w.submit({"url": "https://a.b/fresh", "full_text": "x"})
    assert h.result(5) == capture_item_id("https://a.b/fresh")
    w.close()


def test_writer_flushes_at_exit_without_close(tmp_path):
    import subprocess
    import sys
    from pathlib import Path

    out = tmp_path / "written.txt"
    code = (
        "import time, sys\n"
        "from vb_orchard.capture import CaptureWriter\n"
        "def wb(docs):\n"
        "    time.sleep(0.3)\n"
        "    open(sys.argv[1], 'a', encoding='utf-8').write(''.join(d['item_id'] + '\\n' for d in docs))\n"
        "w = CaptureWriter(wb, linger=0.01)\n"
        "for i in range(5):\n"
        "    w.submit({'url': 'https://a.b/%d' % i, 'full_text': 't'})\n"
        "# exit without flush() or close()\n"
    )
    r = subprocess.run([sys.executable, "-c", code, str(out)], capture_output=True, text=True,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    assert r.returncode == 0, r.stderr
    assert len(out.read_text(encoding="utf-8").split()) == 5


def test_writer_retries_and_failure():
    n = {"c": 0}

    def flaky(docs):
        n["c"] += 1
        if n["c"] < 3:
            raise IOError("busy")

    w = CaptureWriter(flaky, retries=3)
    f = w.submit({"url": "https://a.b/1", "full_text": "t"})
    assert f.result(5) == f.item_id
    w.close()

    def broken(docs):
        raise IOError("down")

    w = CaptureWriter(broken, retries=2)
    f = w.submit({"url": "https://a.b/2", "full_text": "t"})
    try:
        f.result(5)
        assert False
    except IOError:
        pass
    w.close()
    try:
        w.submit({"url": "https://a.b/3"})
        assert False
    except RuntimeError:
        pass
