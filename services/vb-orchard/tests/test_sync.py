"""Sync logic against a fake Postgres layer (no database), fake encoder, temp DuckDB files."""
import hashlib
import json
import threading
import urllib.error
import urllib.request
import uuid

import pytest

from vb_orchard import pgsource, sync
from vb_orchard.config import Settings
from vb_orchard.copy import pg_row_to_doc
from vb_orchard.fake_encoder import FakeEncoder
from vb_orchard.lock import WriterBusy, WriterLock

WORDS = {
    "garden": "soil compost seeds tomato pruning greenhouse watering mulch harvest orchard",
    "kernel": "scheduler memory paging interrupt syscall driver mutex thread kernel cache",
    "music": "chord melody rhythm tempo harmony guitar scale octave rehearsal composition",
}


class FakePG:
    """Stands in for the psycopg connection; the pgsource functions are monkeypatched to use it."""

    def __init__(self):
        self.rows = {}

    def add(self, topic, i, text_extra=""):
        did = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{topic}-{i}"))
        text = f"{topic} note {i}. " + " ".join([WORDS[topic]] * 3) + text_extra
        self.rows[did] = (did, f"https://ex.com/{topic}/{i}", f"{topic.title()} note {i}", text,
                          f"2026-11-{1 + i % 28:02d}T10:00:00Z", [])
        return did

    def fp(self):
        return {k: hashlib.md5(json.dumps(v).encode()).hexdigest() for k, v in self.rows.items()}


VAULT = {"id": "11111111-1111-1111-1111-111111111111", "kind": "personal", "name": "Personal Vault"}
COLL = {"id": "22222222-2222-2222-2222-222222222222", "kind": "collection", "name": "Forte"}


@pytest.fixture
def env(tmp_path, monkeypatch):
    pg = FakePG()
    for t in WORDS:
        for i in range(8):
            pg.add(t, i)
    monkeypatch.setattr(pgsource, "resolve_vaults", lambda con, s: [VAULT])
    monkeypatch.setattr(pgsource, "fingerprints", lambda con, v: pg.fp())
    monkeypatch.setattr(pgsource, "fetch_docs", lambda con, ids: [pg_row_to_doc(*pg.rows[i]) for i in ids])
    s = Settings(database_url="postgresql://unused", db_dir=tmp_path, owner_email="a@b.c",
                 recut_after=5, encoder="fake")
    s.db_path = s.db_for(VAULT["id"])  # the personal vault's file, used by the tests below
    return pg, s, sync.Syncer(s, VAULT, encoder=FakeEncoder())


def test_first_pass_builds_then_idle(env):
    pg, s, sy = env
    r = sy.sync_once(None)
    assert r.action == "rebuild" and r.total == 24
    assert len(sync.read_state(s.db_path)) == 24
    assert sy.sync_once(None).action == "none"
    from vb_orchard.serve import VaultStores

    assert VaultStores(s.db_dir).info()[0]["kind"] == "personal"


def test_new_and_changed_docs_are_inserted_without_rebuild(env):
    pg, s, sy = env
    sy.sync_once(None)
    new = pg.add("music", 99)
    old = next(iter(pg.rows))
    pg.rows[old] = pg.rows[old][:3] + (pg.rows[old][3] + " edited",) + pg.rows[old][4:]
    r = sy.sync_once(None)
    assert (r.action, r.inserted, r.changed, r.total) == ("insert", 1, 1, 25)
    assert r.attach_failed == []
    st = sync.read_state(s.db_path)
    assert st["vb:" + new] == pg.fp()[new] and st["vb:" + old] == pg.fp()[old]
    from orchard_harvest import HarvestStore, packed_tree

    tree = packed_tree(HarvestStore(str(s.db_path)), s.alias, days=None)
    assert tree["item_count"] == 25


def test_delete_triggers_rebuild(env):
    pg, s, sy = env
    sy.sync_once(None)
    gone = next(iter(pg.rows))
    del pg.rows[gone]
    r = sy.sync_once(None)
    assert r.action == "rebuild" and r.removed == 1 and r.total == 23
    assert "vb:" + gone not in sync.read_state(s.db_path)


def test_recut_after_n_inserts(env):
    pg, s, sy = env
    sy.sync_once(None)
    for i in range(5):
        pg.add("garden", 100 + i)
    assert sy.sync_once(None).inserted == 5
    rc = sy.maybe_recut(None)
    assert rc is not None and rc.action == "rebuild" and rc.total == 29
    assert sy.maybe_recut(None) is None


def test_writer_lock_is_exclusive(tmp_path):
    db = tmp_path / "x.duckdb"
    with WriterLock(db):
        with pytest.raises(WriterBusy):
            WriterLock(db).acquire()
    WriterLock(db).acquire().release()  # free again after release


def test_settings_precedence(tmp_path):
    cfg = tmp_path / "config.env"
    cfg.write_text("VB_DATABASE_URL=postgresql://from-file\nVB_OWNER_EMAIL='me@x.y'\nVB_ORCHARD_PORT=9000\n# c\n",
                   encoding="utf-8")
    s = Settings.load(env={"VB_ORCHARD_PORT": "9100"}, config_path=cfg)
    assert s.database_url == "postgresql://from-file" and s.owner_email == "me@x.y" and s.port == 9100
    assert "database_url" not in s.describe() and s.describe()["database_url_set"] is True


def test_serve_do_routes(env):
    pg, s, sy = env
    sy.sync_once(None)
    from vb_orchard.serve import make_server

    srv = make_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        get = lambda p: json.loads(urllib.request.urlopen(base + p, timeout=120).read())  # noqa: E731
        h = get("/do/topics/hierarchy?days=all")
        assert h["type"] == "root" and h["children"]
        assert get("/do/health")["ok"] is True
        rec = get("/do/recent?days=all&limit=3")
        assert len(rec if isinstance(rec, list) else rec["results"]) == 3
        hits = get("/do/find?q=compost+tomato&k=3")
        hits = hits if isinstance(hits, list) else hits["results"]
        assert hits and "garden" in hits[0]["title"].lower()
        assert get("/api/version")["version"]
    finally:
        srv.shutdown()
        srv.server_close()


def test_tag_edit_is_synced_and_searchable(env):
    pg, s, sy = env
    sy.sync_once(None)
    did = next(iter(pg.rows))
    pg.rows[did] = pg.rows[did][:5] + (["zzqx-tag"],)
    r = sy.sync_once(None)
    assert (r.action, r.changed) == ("insert", 1)
    from orchard_harvest import HarvestStore, find

    hits = find(HarvestStore(str(s.db_path), encoder=FakeEncoder()), s.alias, "zzqx-tag", k=3)
    assert hits[0]["item_id"] == "vb:" + did and hits[0]["tags"] == ["zzqx-tag"]


def test_one_tree_per_vault_never_mixed(tmp_path, monkeypatch):
    """Two vaults -> two files with only their own items; a vault that disappears loses its file."""
    a, b = FakePG(), FakePG()
    for i in range(8):
        a.add("garden", i)
        a.add("kernel", i)
        b.add("music", i)
        b.add("garden", 100 + i)
    by_vault = {VAULT["id"]: a, COLL["id"]: b}
    present = [VAULT, COLL]
    monkeypatch.setattr(pgsource, "resolve_vaults", lambda con, s: list(present))
    monkeypatch.setattr(pgsource, "fingerprints", lambda con, v: by_vault[v[0]].fp())
    rows = {**a.rows, **b.rows}
    monkeypatch.setattr(pgsource, "fetch_docs", lambda con, ids: [pg_row_to_doc(*rows[i]) for i in ids])
    s = Settings(database_url="postgresql://unused", db_dir=tmp_path, owner_email="a@b.c", encoder="fake")
    multi = sync.MultiSyncer(s, encoder=FakeEncoder())
    res = dict((v["id"], r) for v, r in multi.sync_all(None))
    assert res[VAULT["id"]].total == 16 and res[COLL["id"]].total == 16
    sa, sb = sync.read_state(s.db_for(VAULT["id"])), sync.read_state(s.db_for(COLL["id"]))
    assert set(sa) == {"vb:" + k for k in a.rows} and set(sb) == {"vb:" + k for k in b.rows}
    assert not set(sa) & set(sb)
    from vb_orchard.serve import VaultStores

    vs = VaultStores(tmp_path)
    info = {v["id"]: v for v in vs.info()}
    assert info[COLL["id"]]["kind"] == "collection" and info[COLL["id"]]["name"] == "Forte"
    assert vs.default_vault() == VAULT["id"]
    present.remove(COLL)  # the collection was deleted in Postgres
    multi.sync_all(None)
    assert not s.db_for(COLL["id"]).exists() and s.db_for(VAULT["id"]).exists()


def test_serve_routes_per_vault(tmp_path, monkeypatch):
    a, b = FakePG(), FakePG()
    for i in range(8):
        a.add("kernel", i)
        b.add("music", i)
    by_vault = {VAULT["id"]: a, COLL["id"]: b}
    monkeypatch.setattr(pgsource, "resolve_vaults", lambda con, s: [VAULT, COLL])
    monkeypatch.setattr(pgsource, "fingerprints", lambda con, v: by_vault[v[0]].fp())
    rows = {**a.rows, **b.rows}
    monkeypatch.setattr(pgsource, "fetch_docs", lambda con, ids: [pg_row_to_doc(*rows[i]) for i in ids])
    s = Settings(database_url="postgresql://unused", db_dir=tmp_path, owner_email="a@b.c", encoder="fake")
    sync.MultiSyncer(s, encoder=FakeEncoder()).sync_all(None)
    from vb_orchard.serve import make_server

    srv = make_server(s, port=0)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    get = lambda p: json.loads(urllib.request.urlopen(base + p, timeout=120).read())  # noqa: E731

    def titles(tree):
        out = []

        def walk(n):
            if n.get("type") == "document":
                out.append(n["name"])
            for c in n.get("children") or []:
                walk(c)
        walk(tree)
        return out

    try:
        h = get("/do/health")
        assert sorted(h["vaults"]) == sorted([VAULT["id"], COLL["id"]]) and h["default_vault"] == VAULT["id"]
        mine = titles(get("/do/topics/hierarchy?days=all"))
        coll = titles(get(f"/do/topics/hierarchy?days=all&vault={COLL['id']}"))
        assert mine and all(t.startswith("Kernel") for t in mine)
        assert coll and all(t.startswith("Music") for t in coll)
        hits = get(f"/do/find?vault={COLL['id']}&q=melody+chord&k=3")
        hits = hits if isinstance(hits, list) else hits["results"]
        assert hits and all(h["title"].startswith("Music") for h in hits)
        assert get(f"/api/tree?days=all&vault={COLL['id']}")["item_count"] == 8
        try:
            get("/do/find?vault=33333333-3333-3333-3333-333333333333&q=x")
            raise AssertionError("unknown vault must 404")
        except urllib.error.HTTPError as e:
            assert e.code == 404
    finally:
        srv.shutdown()
        srv.server_close()


def test_small_vaults_and_failure_isolation(tmp_path, monkeypatch):
    """3 docs -> a tree (cluster_count capped); 1 doc -> no tree, no error; a vault that raises
    does not stop the others."""
    small, tiny, good = FakePG(), FakePG(), FakePG()
    for i in range(3):
        small.add("garden", i)
    tiny.add("music", 0)
    for i in range(8):
        good.add("kernel", i)
    V3 = {"id": "33333333-3333-3333-3333-333333333333", "kind": "collection", "name": "small"}
    V4 = {"id": "44444444-4444-4444-4444-444444444444", "kind": "collection", "name": "tiny"}
    V5 = {"id": "55555555-5555-5555-5555-555555555555", "kind": "collection", "name": "broken"}
    by_vault = {V3["id"]: small, V4["id"]: tiny, VAULT["id"]: good, V5["id"]: good}
    rows = {**small.rows, **tiny.rows, **good.rows}

    def fps(con, v):
        if v[0] == V5["id"]:
            raise RuntimeError("boom")
        return by_vault[v[0]].fp()
    monkeypatch.setattr(pgsource, "resolve_vaults", lambda con, s: [V5, V3, V4, VAULT])
    monkeypatch.setattr(pgsource, "fingerprints", fps)
    monkeypatch.setattr(pgsource, "fetch_docs", lambda con, ids: [pg_row_to_doc(*rows[i]) for i in ids])
    s = Settings(database_url="postgresql://unused", db_dir=tmp_path, owner_email="a@b.c", encoder="fake")
    multi = sync.MultiSyncer(s, encoder=FakeEncoder())

    class PG:
        def rollback(self):
            pass
    res = {v["id"]: r for v, r in multi.sync_all(PG())}
    assert res[V3["id"]].action == "rebuild" and res[V3["id"]].total == 3
    assert res[V4["id"]].action == "none" and not s.db_for(V4["id"]).exists()
    assert res[VAULT["id"]].action == "rebuild" and res[VAULT["id"]].total == 8
    assert V5["id"] not in res and V5["id"] in multi.failing
    assert sync.perspective_for(3)["cuts"][0]["cluster_count"] == 3
