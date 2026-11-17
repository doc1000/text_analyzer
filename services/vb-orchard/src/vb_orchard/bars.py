"""Success-bar measurements (spec 7 + A7 + A10). Measurement code only; no network, no LLM.

Needs treedb, orchard and orchard_harvest on PYTHONPATH. Child-process roles are run with
`python -m vb_orchard.bars writer|reader|readback ...` (vb-orchard installed).
"""
from __future__ import annotations

import datetime as dt
import json
import os
import random
import re
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Optional

from . import engine, store as vstore
from .adapter import to_vb_hierarchy
from .capture import CaptureWriter, normalize_payload
from .ids import capture_item_id

SEED = 20261116
ALIAS = "vb/topic"
WINDOWS = [7, 30, 90, 365, None]


def get_encoder():
    """MiniLM (local files only), or the fake encoder when DO_FAKE_ENCODER=1 (tests)."""
    if os.environ.get("DO_FAKE_ENCODER") == "1":
        from .fake_encoder import FakeEncoder

        return FakeEncoder()
    from orchard.backends.minilm import MiniLMEmbeddingBackend

    return MiniLMEmbeddingBackend(local_files_only=True)


def pct(xs, p):
    xs = sorted(xs)
    if not xs:
        return None
    k = (len(xs) - 1) * p / 100.0
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def _lat(xs):
    return {"p50_ms": round(pct(xs, 50) * 1000, 2), "p95_ms": round(pct(xs, 95) * 1000, 2), "n": len(xs)}


def make_store(path, *, encoder=None, now=None, keyword_only=False):
    from orchard_harvest.store import HarvestStore

    s = HarvestStore(str(path), encoder=encoder, now=now)
    if keyword_only:
        s._encoder, s._encoder_resolved = None, True
    return s


# ------------------------------------------------------------------ tree open
def tree_open(path, *, runs: int = 20, anchor: Optional[str] = None, alias: str = ALIAS) -> dict:
    """packed_tree, engine hierarchy (packed+adapter) and HTTP /api/tree, cold and warm.

    cold = first call on a freshly opened HarvestStore (snapshot load included; OS file cache is warm).
    anchor = ISO time to use as "now" (for datasets older than the windows), else the wall clock.
    """
    from orchard_harvest import packed_tree
    from orchard_harvest.view.server import make_server, serve_in_thread

    now = None
    if anchor:
        a = dt.datetime.fromisoformat(anchor.replace("Z", "+00:00"))
        now = lambda: a  # noqa: E731
    out = {"anchor": anchor or "wall clock", "windows": {}}

    def http_get(port, d):
        t = time.perf_counter()
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/tree?days={'all' if d is None else d}") as r:
            body = r.read()
        return time.perf_counter() - t, len(body)

    for d in WINDOWS:
        key = "all" if d is None else str(d)
        warm_store = make_store(path, now=now)
        tree = packed_tree(warm_store, alias, days=d)  # primes the cache
        row = {"items_in_window": tree.get("item_count"),
               "payload_bytes_packed": len(json.dumps(tree, ensure_ascii=False).encode("utf-8")),
               "payload_bytes_vb_hierarchy": len(json.dumps(to_vb_hierarchy(tree, d), ensure_ascii=False).encode("utf-8"))}
        cold_p, cold_h, cold_http, warm_p, warm_h, warm_http = [], [], [], [], [], []
        for _ in range(runs):
            s = make_store(path, now=now)
            t = time.perf_counter(); packed_tree(s, alias, days=d); cold_p.append(time.perf_counter() - t)
            s = make_store(path, now=now)
            t = time.perf_counter(); to_vb_hierarchy(packed_tree(s, alias, days=d), d); cold_h.append(time.perf_counter() - t)
            s = make_store(path, now=now)
            srv = make_server(s, alias, port=0)
            serve_in_thread(srv)
            try:
                dtm, nbytes = http_get(srv.server_address[1], d)
                cold_http.append(dtm)
            finally:
                srv.shutdown(); srv.server_close()
        for _ in range(runs):
            t = time.perf_counter(); packed_tree(warm_store, alias, days=d); warm_p.append(time.perf_counter() - t)
            t = time.perf_counter(); to_vb_hierarchy(packed_tree(warm_store, alias, days=d), d); warm_h.append(time.perf_counter() - t)
        srv = make_server(warm_store, alias, port=0)
        serve_in_thread(srv)
        try:
            http_get(srv.server_address[1], d)
            for _ in range(runs):
                dtm, nbytes = http_get(srv.server_address[1], d)
                warm_http.append(dtm)
            row["payload_bytes_http"] = nbytes
        finally:
            srv.shutdown(); srv.server_close()
        row.update({"cold_packed_tree": _lat(cold_p), "warm_packed_tree": _lat(warm_p),
                    "cold_hierarchy": _lat(cold_h), "warm_hierarchy": _lat(warm_h),
                    "cold_http_api_tree": _lat(cold_http), "warm_http_api_tree": _lat(warm_http)})
        out["windows"][key] = row
    return out


# ------------------------------------------------------------------ captures
_WORDS = ("alpha beta gamma delta orchard cluster notebook garden method review thinking zettel capture "
          "archive project area resource system flow habit memory graph signal note idea").split()


def make_payloads(m: int = 500, seed: int = SEED) -> list:
    """M extension-shaped payloads (real field names: url,title,text,mode,tags,captured_at) with a
    share in the spec shape (full_text/highlight/notes). Includes unicode, one 1 MB page, empty
    notes and repeated urls (re-captures across modes)."""
    rng = random.Random(seed)
    uniq = max(1, int(m * 0.8))
    out = []
    for i in range(m):
        u = i if i < uniq else rng.randrange(uniq)
        mode = ("page" if i % 20 < 12 else "selection" if i % 20 < 17 else "note")
        words = " ".join(rng.choice(_WORDS) for _ in range(rng.randrange(40, 160)))
        uni = f"naïve café 日本語 🙂 Ünïcödé {u}"
        ts = (dt.datetime(2026, 11, 1, tzinfo=dt.timezone.utc) + dt.timedelta(minutes=i)).isoformat().replace("+00:00", "Z")
        title = f"Capture {u} {uni}"
        if mode == "note":
            p = {"url": f"note://my note {u}", "title": f"Note {u} {uni}", "text": f"{words} {uni}",
                 "mode": "note", "tags": None, "captured_at": ts}
        elif mode == "selection":
            sel = f"{rng.choice(_WORDS)} {uni} {words[:80]}"
            p = {"url": f"https://Site{u}.Example/Page/{u}?q=1#frag{i}", "title": title,
                 "text": f"[Header] {sel[:80]}\n\n[captured]\n{sel}", "mode": "selection", "tags": None,
                 "captured_at": ts}
        else:
            body = f"{uni}. {words}."
            if i == 5:  # 1 MB page
                body = " ".join(f"Sentence {k} about {rng.choice(_WORDS)} and {rng.choice(_WORDS)}." for k in range(1, 30000))
                body = body[:1_000_000]
            p = {"url": f"https://Site{u}.Example/Page/{u}?q=1#frag{i}", "title": title, "text": body,
                 "mode": "page", "tags": None, "captured_at": ts}
        if i % 10 == 3 and mode != "note":  # spec-shaped payload (full_text/highlight/notes)
            q = {"url": p["url"], "title": p["title"], "full_text": f"{uni}. {words}. spec-shaped {i}",
                 "highlight": f"hl {uni} {i}", "notes": "" if i % 20 == 3 else f"note {i} {uni}",
                 "tags": [f"t{i % 4}", "spec"], "captured_at": ts}
            p = q
        out.append(p)
    return out


def expected_docs(payloads) -> dict:
    """Order-insensitive expected final state per item_id (merge rule of spec 6.3)."""
    exp = {}
    for raw in payloads:
        n = normalize_payload(raw)
        e = exp.setdefault(capture_item_id(n["url"]), {"url": n["url"], "titles": set(), "texts": [],
                                                        "highlights": set(), "notes": set(), "tags": set(),
                                                        "captured": []})
        e["titles"].add(n["title"])
        e["texts"].append(n["full_text"])
        if n["highlight"]:
            e["highlights"].add(n["highlight"])
        for ln in n["notes"].split("\n"):
            if ln:
                e["notes"].add(ln)
        e["tags"] |= set(n["tags"])
        e["captured"].append(n["captured_at"])
    return exp


def compare(exp: dict, got: dict) -> list:
    """Field-by-field comparison; returns a list of mismatch strings."""
    bad = []
    for iid, e in exp.items():
        d = got.get(iid)
        if d is None:
            bad.append(f"{iid}: missing")
            continue
        md = d["metadata"]
        mx = max(len(t) for t in e["texts"])
        if d["title"] not in e["titles"] and not (d["title"] == "" and "" in e["titles"]):
            bad.append(f"{iid}: title {d['title']!r}")
        if len(d["text"]) != mx or (mx and d["text"] not in e["texts"]):
            bad.append(f"{iid}: text len {len(d['text'])} != {mx}")
        if set(md.get("highlights") or []) != e["highlights"]:
            bad.append(f"{iid}: highlights")
        if {x for x in (md.get("notes") or "").split("\n") if x} != e["notes"]:
            bad.append(f"{iid}: notes")
        if set(md.get("tags") or []) != e["tags"]:
            bad.append(f"{iid}: tags")
        first = min(e["captured"])
        got_first = md.get("captured_at") or ""
        if got_first[:19] != first[:19]:  # storage may trim microseconds
            bad.append(f"{iid}: captured_at {got_first} != {first}")
        if not (md.get("updated_at") or "").endswith("Z"):
            bad.append(f"{iid}: updated_at {md.get('updated_at')}")
        if md.get("source") != "vaultbubbles" or (md.get("url") or "") == "":
            bad.append(f"{iid}: source/url")
    return bad


def readback(path) -> dict:
    """Fresh-process readback of vaultbubbles docs + the cut item ids (read-only)."""
    from treedb import cutstore, registry

    with vstore.connection(path, read_only=True) as con:
        rows = con.execute("SELECT item_id, title, text, metadata FROM treedb.documents").fetchall()
        docs = {}
        for iid, title, text, md in rows:
            md = json.loads(md) if isinstance(md, str) else md
            if (md or {}).get("source") == "vaultbubbles":
                docs[iid] = {"item_id": iid, "title": title or "", "text": text or "", "metadata": md}
        tid = registry.resolve(con, ALIAS)
        cut_items = [r["item_id"] for r in cutstore.read_cut(con, tid, "topics") if r["kind"] == "item"]
    return {"docs": docs, "cut_items": cut_items}


def run_readback_subprocess(path) -> dict:
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "rb.json"
        r = subprocess.run([sys.executable, "-m", "vb_orchard.bars", "readback", str(path), str(out)],
                           capture_output=True, text=True)
        if r.returncode:
            raise RuntimeError(r.stderr[-2000:])
        return json.loads(out.read_text(encoding="utf-8"))


def captures(db_path, *, m: int = 500, threads: int = 8, insert_on_capture: bool = True, encoder=None) -> dict:
    """Submit m payloads from `threads` threads through one CaptureWriter into the DuckDB copy."""
    payloads = make_payloads(m)
    stats: dict = {}
    enc = encoder or get_encoder()
    w = CaptureWriter(vstore.make_write_batch(db_path, insert_on_capture=insert_on_capture, encoder=enc, stats=stats),
                      lookup=vstore.make_lookup(db_path))
    futs, lock = [], threading.Lock()
    chunks = [payloads[t::threads] for t in range(threads)]

    def work(chunk):
        local = [(w.submit(p), p) for p in chunk]
        with lock:
            futs.extend(local)

    t0 = time.perf_counter()
    ts = [threading.Thread(target=work, args=(c,)) for c in chunks]
    [t.start() for t in ts]
    [t.join() for t in ts]
    w.flush(1800)
    w.close()
    elapsed = time.perf_counter() - t0
    saved, errors = 0, []
    for f, _ in futs:
        try:
            f.result(0)
            saved += 1
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")
    locks = stats.pop("write_lock_seconds", [])
    if locks:
        stats["write_lock_p95_ms"] = round(pct(locks, 95) * 1000, 1)
        stats["write_lock_max_ms"] = round(max(locks) * 1000, 1)
    exp = expected_docs(payloads)
    rb = run_readback_subprocess(db_path)
    mism = compare(exp, rb["docs"])
    in_cut = len(set(exp) & set(rb["cut_items"]))
    modes = {}
    for p in payloads:
        modes[p.get("mode", "spec-shape")] = modes.get(p.get("mode", "spec-shape"), 0) + 1
    return {"submitted": len(payloads), "saved": saved, "saved_over_submitted": f"{saved}/{len(payloads)}",
            "unique_items_expected": len(exp), "unique_items_read_back": len(rb["docs"]),
            "field_mismatches": len(mism), "mismatch_examples": mism[:10], "errors": errors[:5],
            "placed_in_tree": in_cut, "writer_stats": stats, "threads": threads,
            "insert_on_capture": insert_on_capture, "seconds": round(elapsed, 1),
            "payload_modes": modes, "largest_text_chars": max((len(d["text"]) for d in rb["docs"].values()), default=0),
            "bypasses": "extension and HTTP /ingest endpoint (payloads go straight to CaptureWriter)"}


# ------------------------------------------------------------------ two process (A7)
def _child_writer(db, seconds, pause=0.3):
    stats: dict = {}
    w = CaptureWriter(vstore.make_write_batch(db, encoder=get_encoder(), stats=stats), lookup=vstore.make_lookup(db))
    end, n, ok, fail, busy, lat = time.time() + seconds, 0, 0, 0, 0, []
    rng = random.Random(SEED)
    while time.time() < end:
        futs = []
        for _ in range(3):
            n += 1
            u = rng.randrange(200)
            futs.append((time.perf_counter(), w.submit({
                "url": f"https://twoproc{u}.example/p/{u}", "title": f"Two process {u}",
                "text": " ".join(rng.choice(_WORDS) for _ in range(80)), "mode": "page",
                "captured_at": dt.datetime.now(dt.timezone.utc).isoformat()})))
        for t0, f in futs:
            try:
                f.result(120)
                ok += 1
                lat.append(time.perf_counter() - t0)
            except Exception as e:  # noqa: BLE001
                fail += 1
                busy += type(e).__name__ == "StoreBusyError"
        time.sleep(pause)
    w.close()
    return {"role": "writer", "submitted": n, "ok": ok, "failures": fail, "store_busy": busy,
            "capture_latency_p95_ms": round(pct(lat, 95) * 1000, 1) if lat else None,
            "write_lock_p95_ms": round(pct(stats.get("write_lock_seconds") or [0], 95) * 1000, 1),
            "write_lock_max_ms": round(max(stats.get("write_lock_seconds") or [0]) * 1000, 1),
            "stats": {k: v for k, v in stats.items() if k != "write_lock_seconds"}}


def _child_reader(db, seconds):
    enc = get_encoder()
    s = make_store(db, encoder=enc)
    from orchard_harvest import find, packed_tree

    end, n, fail, busy, lat, errs = time.time() + seconds, 0, 0, 0, {"find": [], "packed_tree": []}, []
    fail = busy = 0
    qs = ["second brain", "orchard cluster", "note idea method", "capture archive", "zettel memory"]
    i = 0
    while time.time() < end:
        for name, fn in (("find", lambda: find(s, ALIAS, qs[i % len(qs)], k=5)),
                         ("packed_tree", lambda: packed_tree(s, ALIAS, days=None))):
            t = time.perf_counter()
            try:
                fn()
                lat[name].append(time.perf_counter() - t)
            except Exception as e:  # noqa: BLE001
                fail += 1
                busy += type(e).__name__ == "StoreBusyError"
                if len(errs) < 5:
                    errs.append(f"{type(e).__name__}: {str(e)[:160]}")
            n += 1
        i += 1
        time.sleep(0.05)
    allv = lat["find"] + lat["packed_tree"]
    return {"role": "reader", "requests": n, "failures": fail, "store_busy": busy, "errors": errs,
            "stale_served": getattr(s, "stale_served", None),
            "find_p95_ms": round(pct(lat["find"], 95) * 1000, 1) if lat["find"] else None,
            "packed_tree_p95_ms": round(pct(lat["packed_tree"], 95) * 1000, 1) if lat["packed_tree"] else None,
            "reader_p95_ms": round(pct(allv, 95) * 1000, 1) if allv else None,
            "reader_p50_ms": round(pct(allv, 50) * 1000, 1) if allv else None}


def two_process(db_path, *, seconds: float = 25, pause: float = 0.3) -> dict:
    env = dict(os.environ)
    procs = [subprocess.Popen([sys.executable, "-m", "vb_orchard.bars", role, str(db_path), str(seconds), str(pause)],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env)
             for role in ("writer", "reader")]
    res = {}
    for p in procs:
        out, err = p.communicate(timeout=seconds + 600)
        lines = [ln for ln in out.splitlines() if ln.startswith("{")]
        if p.returncode or not lines:
            res["child_error"] = (err or out)[-1500:]
            continue
        j = json.loads(lines[-1])
        res[j["role"]] = j
    return {"seconds": seconds, "writer_pause_s": pause, **res}


# ------------------------------------------------------------------ known item
_TOK = re.compile(r"[a-z0-9]{3,}")


def _clean(text: str) -> str:
    text = re.sub(r"!?\[([^\]]*)\]\([^)]*\)", r"\1", text)
    return re.sub(r"[*_`>#]+", "", text)


def _sentences(text: str) -> list:
    out = []
    for s in re.split(r"(?<=[.!?])\s+", _clean(text)):
        s = " ".join(s.split())
        if 8 <= len(s.split()) <= 40 and "http" not in s and "|" not in s:
            out.append(s)
    return out


def known_item(db_path, *, n: int = 100, seed: int = SEED, encoder=None, ks=(1, 3, 10)) -> dict:
    """Top-k hit rates and MRR for 5 query types, hybrid vs keyword-only (A10; seed fixed)."""
    from treedb import enrichment

    enc = encoder or get_encoder()
    with vstore.connection(db_path, read_only=True) as con:
        rows = con.execute("SELECT item_id, title, text, metadata FROM treedb.documents ORDER BY item_id").fetchall()
        outs = enrichment.get_item_outputs(con, "item.extract@1")
    docs = []
    for iid, title, text, md in rows:
        md = json.loads(md) if isinstance(md, str) else md
        docs.append({"item_id": iid, "title": title or "", "text": text or "", "md": md or {}})
    rng = random.Random(seed)
    sample = rng.sample(docs, min(n, len(docs)))
    with_hl = [d for d in docs if (d["md"].get("highlights") or [""])[0] or (d["md"].get("notes") or "")]
    sample_e = rng.sample(with_hl, min(n, len(with_hl)))

    def q_title(d):
        return d["title"] or None

    def q_mid(d):
        s = _sentences(d["text"])
        return s[len(s) // 2] if s else None

    def q_terms(d):
        tw = set(_TOK.findall(d["title"].lower()))
        terms = [t for t in (outs.get(d["item_id"]) or {}).get("key_terms", []) if t.lower() not in tw]
        return " ".join(terms[:4]) if len(terms) >= 3 else None

    def q_body_notitle(d):
        s = _sentences(d["text"])
        if not s:
            return None
        tw = set(_TOK.findall(d["title"].lower()))
        words = [w for w in s[len(s) // 2].split() if not (set(_TOK.findall(w.lower())) & tw)]
        return " ".join(words) if len(words) >= 3 else None

    def q_hl(d):
        h = (d["md"].get("highlights") or [""])[0] or (d["md"].get("notes") or "")
        return h or None

    types = [("a_title", q_title, sample), ("b_mid_body_sentence", q_mid, sample),
             ("c_key_terms_minus_title", q_terms, sample), ("d_body_sentence_minus_title_terms", q_body_notitle, sample),
             ("e_highlight_or_notes", q_hl, sample_e)]
    modes = {"hybrid": make_store(db_path, encoder=enc), "keyword_only": make_store(db_path, keyword_only=True)}
    from orchard_harvest import find

    out = {"seed": seed, "sample_size": len(sample), "highlight_pool": len(with_hl), "types": {}}
    for tname, fn, pool in types:
        entry = {}
        qs = [(d, fn(d)) for d in pool]
        qs = [(d, q) for d, q in qs if q]
        for mname, st in modes.items():
            ranks = []
            for d, q in qs:
                res = find(st, ALIAS, q, k=50)
                ids = [r["item_id"] for r in res]
                ranks.append(ids.index(d["item_id"]) + 1 if d["item_id"] in ids else None)
            nq = len(ranks) or 1
            entry[mname] = {"n": len(ranks), **{f"top{k}": round(sum(1 for r in ranks if r and r <= k) / nq, 3) for k in ks},
                            "mrr": round(sum(1 / r for r in ranks if r) / nq, 3)}
        out["types"][tname] = entry
    return out


# ------------------------------------------------------------------ criterion 3
def title_quality(db_path, *, sample: int = 30, seed: int = SEED) -> dict:
    from orchard_harvest import packed_tree

    tree = packed_tree(make_store(db_path), ALIAS, days=None)
    nodes, dup_sibling_nodes, sibling_groups, dup_groups = [], 0, 0, 0

    def walk(node, depth):
        nonlocal dup_sibling_nodes, sibling_groups, dup_groups
        kids = [c for c in node.get("children") or [] if c.get("type") != "item"]
        nodes.append((depth, node))
        titles = [(c.get("title") or "").strip().lower() for c in kids]
        if len(kids) > 1:
            sibling_groups += 1
            dups = {t for t in titles if t and titles.count(t) > 1}
            dup_groups += bool(dups)
            dup_sibling_nodes += sum(1 for t in titles if t in dups)
        for c in kids:
            walk(c, depth + 1)

    walk(tree, 0)
    clusters = [(d, x) for d, x in nodes if x.get("type") == "cluster"]
    empty_t = sum(1 for _, x in clusters if not (x.get("title") or "").strip())
    empty_s = sum(1 for _, x in clusters if not (x.get("summary") or "").strip())
    picked = random.Random(seed).sample(clusters, min(sample, len(clusters)))
    return {"clusters": len(clusters), "empty_title_share": round(empty_t / max(1, len(clusters)), 3),
            "empty_summary_share": round(empty_s / max(1, len(clusters)), 3),
            "sibling_groups": sibling_groups, "sibling_groups_with_duplicate_titles": dup_groups,
            "duplicate_sibling_title_share": round(dup_sibling_nodes / max(1, len(clusters)), 3),
            "sample": [{"depth": d, "title": x.get("title"), "summary": (x.get("summary") or "")[:200],
                        "item_count": x.get("item_count")} for d, x in picked]}


# ------------------------------------------------------------------ markdown
def delta_table(r1: dict, r2: dict) -> list:
    def g(r, *path):
        for p in path:
            r = (r or {}).get(p) if isinstance(r, dict) else None
        return r

    def kn(r, t, m, k):
        return g(r, "known_item", "types", t, m, k)

    rows = [("tree 279 all, warm packed p50 ms", ("tree_open_279", "windows", "all", "warm_packed_tree", "p50_ms")),
            ("tree 279 all, cold packed p50 ms", ("tree_open_279", "windows", "all", "cold_packed_tree", "p50_ms")),
            ("tree 10k all, warm packed p50 ms", ("tree_open_10k", "windows", "all", "warm_packed_tree", "p50_ms")),
            ("tree 10k all, cold packed p50 ms", ("tree_open_10k", "windows", "all", "cold_packed_tree", "p50_ms")),
            ("tree 10k all, warm HTTP p50 ms", ("tree_open_10k", "windows", "all", "warm_http_api_tree", "p50_ms")),
            ("captures saved", ("captures", "saved_over_submitted")),
            ("capture field mismatches", ("captures", "field_mismatches")),
            ("captures placed in tree", ("captures", "placed_in_tree")),
            ("two-proc realistic reader failures", ("two_process", "reader", "failures")),
            ("two-proc realistic reader p95 ms", ("two_process", "reader", "reader_p95_ms")),
            ("two-proc stress reader failures", ("two_process_stress", "reader", "failures")),
            ("two-proc stress reader p95 ms", ("two_process_stress", "reader", "reader_p95_ms")),
            ("empty cluster titles", ("title_quality", "empty_title_share")),
            ("duplicate sibling title share", ("title_quality", "duplicate_sibling_title_share"))]
    for t in ("a_title", "b_mid_body_sentence", "c_key_terms_minus_title", "d_body_sentence_minus_title_terms", "e_highlight_or_notes"):
        for m in ("hybrid", "keyword_only"):
            rows.append((f"known {t} {m} top3 / MRR", None, t, m))
    L = ["| measure | run 1 | run 2 |", "|---|---|---|"]
    for row in rows:
        if row[1] is None:
            _, _, t, m = row
            v1 = f"{kn(r1, t, m, 'top3')} / {kn(r1, t, m, 'mrr')}"
            v2 = f"{kn(r2, t, m, 'top3')} / {kn(r2, t, m, 'mrr')}"
        else:
            v1, v2 = g(r1, *row[1]), g(r2, *row[1])
        L.append(f"| {row[0]} | {v1} | {v2} |")
    return L


def to_markdown(r: dict, run1: Optional[dict] = None) -> str:
    L = ["# Vault Bubbles x Data Orchard: success bars", "",
         f"Generated {r.get('generated_at')}. Seed {SEED}. No LLM, no network, DuckDB temp copies only.", ""]
    if run1:
        L += ["## Run 1 to run 2", "", "Run 1 = first wiring (perspective v1, hybrid 0.65/0.35, one long write lock); run 2 = final code of all three packages "
              "(perspective v2, short write lock, stale-snapshot reads, hybrid 0.5/0.5). Run 1 files: success_bars_run1.json / .md.", ""]
        L += delta_table(run1, r) + [""]
    for key, label in (("tree_open_279", "Tree open, N=279 (Forte copy, real MiniLM)"),
                       ("tree_open_279_anchored", "Tree open, N=279, windows anchored at the newest item"),
                       ("tree_open_10k", "Tree open, N=10,000 (synthetic)")):
        t = r.get(key)
        if not t:
            continue
        L += [f"## {label}", "", f"now = {t['anchor']}. Times in ms (p50 / p95 over 20 runs).", "",
              "| days | items | packed cold | packed warm | hierarchy cold | hierarchy warm | HTTP cold | HTTP warm | bytes packed / VB / HTTP |",
              "|---|---|---|---|---|---|---|---|---|"]
        for d, w in t["windows"].items():
            f = lambda k: f"{w[k]['p50_ms']} / {w[k]['p95_ms']}"  # noqa: E731
            L.append(f"| {d} | {w['items_in_window']} | {f('cold_packed_tree')} | {f('warm_packed_tree')} | "
                     f"{f('cold_hierarchy')} | {f('warm_hierarchy')} | {f('cold_http_api_tree')} | {f('warm_http_api_tree')} | "
                     f"{w['payload_bytes_packed']} / {w['payload_bytes_vb_hierarchy']} / {w['payload_bytes_http']} |")
        L.append("")
    if r.get("build_10k"):
        L += ["10k build: " + r["build_10k"], ""]
    c = r.get("captures")
    if c:
        L += ["## Captures saved", "",
              f"saved/submitted: **{c['saved_over_submitted']}**; unique items {c['unique_items_expected']} expected, "
              f"{c['unique_items_read_back']} read back (fresh process); field mismatches: **{c['field_mismatches']}**; "
              f"placed into the tree: {c['placed_in_tree']}; threads {c['threads']}; {c['seconds']} s; "
              f"largest text {c['largest_text_chars']} chars; modes {c['payload_modes']}; writer stats {c['writer_stats']}.", ""]
        if c["mismatch_examples"]:
            L += ["Mismatch examples: " + "; ".join(c["mismatch_examples"]), ""]
    for tkey, tlabel in (("two_process", "realistic cadence"), ("two_process_stress", "stress, tight writer loop")):
        tp = r.get(tkey)
        if not tp:
            continue
        wr, rd = tp.get("writer", {}), tp.get("reader", {})
        L += [f"## Two processes (A7), {tlabel}", "",
              f"{tp['seconds']} s, writer pause {tp.get('writer_pause_s')} s between 3-capture batches. Writer: {wr.get('submitted')} captures submitted, {wr.get('ok')} ok, "
              f"{wr.get('failures')} failures ({wr.get('store_busy')} StoreBusyError), capture latency p95 {wr.get('capture_latency_p95_ms')} ms, write lock p95 {wr.get('write_lock_p95_ms')} ms (max {wr.get('write_lock_max_ms')}). "
              f"Reader: {rd.get('requests')} requests, {rd.get('failures')} failures ({rd.get('store_busy')} StoreBusyError), "
              f"{rd.get('stale_served')} served from a stale snapshot, p50 {rd.get('reader_p50_ms')} ms, p95 {rd.get('reader_p95_ms')} ms "
              f"(find p95 {rd.get('find_p95_ms')}, packed_tree p95 {rd.get('packed_tree_p95_ms')}). Reader errors: {rd.get('errors')}", ""]
        if tp.get("child_error"):
            L += [f"Child error: {tp['child_error']}", ""]
    k = r.get("known_item")
    if k:
        L += ["## Known item in top 3", "", f"{k['sample_size']} sampled items (seed {k['seed']}); (e) uses items with a highlight/notes "
              f"(pool {k['highlight_pool']}). Rank searched to 50.", "",
              "| query type | mode | n | top1 | top3 | top10 | MRR |", "|---|---|---|---|---|---|---|"]
        for t, e in k["types"].items():
            for mname, v in e.items():
                L.append(f"| {t} | {mname} | {v['n']} | {v['top1']} | {v['top3']} | {v['top10']} | {v['mrr']} |")
        L.append("")
    q = r.get("title_quality")
    if q:
        L += ["## Criterion 3: node titles and summaries", "",
              f"{q['clusters']} cluster nodes; empty titles {q['empty_title_share']:.1%}; empty summaries {q['empty_summary_share']:.1%}; "
              f"sibling groups with duplicate titles {q['sibling_groups_with_duplicate_titles']}/{q['sibling_groups']} "
              f"({q['duplicate_sibling_title_share']:.1%} of clusters are duplicates of a sibling).", "",
              "30-node sample (random, seeded):", "", "| depth | items | title | summary |", "|---|---|---|---|"]
        for s in q["sample"]:
            L.append(f"| {s['depth']} | {s['item_count']} | {str(s['title']).replace('|', '/')} | {s['summary'].replace('|', '/')} |")
        L.append("")
    L += ["## What these numbers do not cover", "",
          "- The extension and the HTTP `/ingest` endpoint are bypassed: payloads with the extension's field names go straight to `CaptureWriter`.",
          "- Browser render time is not measured; HTTP numbers are the local stdlib server round trip only.",
          "- The data is the public Forte article copy (a stand-in), not the user's own captures, so topic structure, title quality and retrieval rates may differ on real data.",
          "- Cold means a freshly opened store (snapshot load included) with a warm OS file cache.",
          "- The 279-item windows depend on the dates: the Forte items are 2026-07/08, so with the wall clock most short windows are empty (see item counts); the anchored table moves 'now' to the newest item.",
          "- Two-process test is a short soak (about 25 s) on one machine, not a long-running soak.",
          "- Known-item queries are synthetic derivations of the item itself, a sanity bound rather than real user queries."]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ child entry points
def _main(argv):
    role = argv[0]
    if role == "readback":
        rb = readback(argv[1])
        Path(argv[2]).write_text(json.dumps(rb, ensure_ascii=False), encoding="utf-8")
    elif role == "writer":
        print(json.dumps(_child_writer(argv[1], float(argv[2]), float(argv[3]) if len(argv) > 3 else 0.3)))
    elif role == "reader":
        print(json.dumps(_child_reader(argv[1], float(argv[2]))))
    else:
        raise SystemExit(f"unknown role {role}")


if __name__ == "__main__":
    _main(sys.argv[1:])
