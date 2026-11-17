"""Sync logic against a fake Postgres layer (no database), fake encoder, temp DuckDB files."""
import hashlib
import json
import threading
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
                          f"2026-11-{1 + i % 28:02d}T10:00:00Z")
        return did

    def fp(self):
        return {k: hashlib.md5(json.dumps(v).encode()).hexdigest() for k, v in self.rows.items()}


@pytest.fixture
def env(tmp_path, monkeypatch):
    pg = FakePG()
    for t in WORDS:
        for i in range(8):
            pg.add(t, i)
    monkeypatch.setattr(pgsource, "resolve_vaults", lambda con, s: ["v1"])
    monkeypatch.setattr(pgsource, "fingerprints", lambda con, v: pg.fp())
    monkeypatch.setattr(pgsource, "fetch_docs", lambda con, ids: [pg_row_to_doc(*pg.rows[i]) for i in ids])
    s = Settings(database_url="postgresql://unused", db_path=tmp_path / "vb.duckdb", owner_email="a@b.c",
                 recut_after=5, encoder="fake")
    return pg, s, sync.Syncer(s, encoder=FakeEncoder())


def test_first_pass_builds_then_idle(env):
    pg, s, sy = env
    r = sy.sync_once(None)
    assert r.action == "rebuild" and r.total == 24
    assert len(sync.read_state(s.db_path)) == 24
    assert sy.sync_once(None).action == "none"


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
