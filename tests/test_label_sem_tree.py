#!/usr/bin/env python3
"""
Tests for semantic labeling metadata + tag-based relabel safeguards (semantic_tree_v2).

Validates:
- Tag storage and GIN index
- Signal aggregation (aggregate_node_signals)
- Anchor detection (suggest_node_label with ratio >= 0.6)
- Large cluster generalization (ratio < 0.6, doc_count thresholds)
- Locked node protection (relabel_vault skips locked)
- Pinned title protection (relabel_vault skips title_source='pinned')
- Stability: recluster + relabel + scoped recluster
- Existing tree functions (attach_document, move_node, etc.) still work
- Integration: synthetic vault lifecycle

Prerequisites:
- Migrations 008 through 017 applied
- DATABASE_URL set
- User test@example.com exists

Usage:
    # Local (default: localhost:5432; use db:5432 when running inside Docker)
    python tests/test_label_sem_tree.py
    # Or: export DATABASE_URL=postgresql+psycopg2://badger:badgerpass@localhost:5432/badgerdb
"""

import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

_raw_db_url = os.getenv(
    "DATABASE_URL",
    "postgresql+psycopg2://badger:badgerpass@localhost:5432/badgerdb",
)
DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql+psycopg2://", 1)
if "postgresql://" in DATABASE_URL and "+psycopg2" not in DATABASE_URL:
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)

TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL", "test@example.com")


@dataclass
class TestResult:
    name: str
    passed: bool
    expected: str
    actual: str
    details: str = ""


def run_tests():
    results: list[TestResult] = []

    def log(name: str, passed: bool, expected: str = "", actual: str = "", details: str = ""):
        results.append(TestResult(name=name, passed=passed, expected=expected, actual=actual, details=details))
        status = "PASS" if passed else "FAIL"
        print(f"  [{status}] {name}")
        if not passed:
            if expected:
                print(f"         Expected: {expected}")
            if actual:
                print(f"         Actual: {actual}")
            if details:
                print(f"         Details: {details}")

    # ---------------------------------------------------------------------
    # Unit tests (no DB required) - new labeling pipeline
    # ---------------------------------------------------------------------
    from app.labeling import (
        _deduplicate_phrases,
        _extract_phrases_per_doc,
        _compose_llm_refinement_prompt,
        VaultLabelContext,
    )

    dedup = _deduplicate_phrases(["machine learning", "machine", "learning", "deep learning"])
    log(
        "_deduplicate_phrases keeps longer terms",
        "machine" not in dedup and "machine learning" in dedup,
        expected="'machine' dropped when 'machine learning' present",
        actual=str(dedup),
    )

    ctx = VaultLabelContext(
        doc_index={},
        feature_names=[],
        tfidf_matrix=None,
        doc_texts={},
        doc_titles={},
        doc_tags={},
        doc_summaries={},
    )
    did1 = uuid.uuid4()
    did2 = uuid.uuid4()
    ctx.doc_titles[did1] = "Capetown travel guide"
    ctx.doc_summaries[did1] = "Capetown beaches and culture"
    ctx.doc_tags[did1] = ["travel"]
    ctx.doc_titles[did2] = "Winter Park skiing"
    ctx.doc_summaries[did2] = "Ski resorts in Winter Park"
    ctx.doc_tags[did2] = []
    phrases_out = _extract_phrases_per_doc(ctx, [did1, did2], top_per_doc=3, top_aggregate=6)
    phrases_strs = [p.get("phrase", "") for p in phrases_out]
    log(
        "_extract_phrases_per_doc normalizes per doc",
        len(phrases_strs) >= 2 and any("capetown" in p.lower() or "travel" in p.lower() for p in phrases_strs),
        expected="phrases from both docs (no single doc dominating)",
        actual=str(phrases_strs[:6]),
    )

    prompt = _compose_llm_refinement_prompt({
        "tags": [{"tag": "travel"}],
        "tfidf_terms": [{"phrase": "beach"}, {"phrase": "ski"}],
        "summary_phrases": [{"phrase": "vacation"}],
        "title_terms": [{"term": "guide"}],
        "doc_count": 5,
        "deterministic_label": "Travel",
    })
    has_phrases_only = "travel" in prompt and "beach" in prompt and "score=" not in prompt and "freq=" not in prompt
    log(
        "_compose_llm_refinement_prompt phrases only no numbers",
        has_phrases_only,
        expected="phrases in prompt without score/freq/ratio",
        actual="score= in prompt" if "score=" in prompt else ("freq= in prompt" if "freq=" in prompt else "ok"),
    )

    engine = create_engine(DATABASE_URL)
    session_factory = sessionmaker(bind=engine, autocommit=False, autoflush=False)

    print("\n--- label_sem_tree tests ---\n")

    db = session_factory()
    try:
        trans = db.begin()

        # 1) Get test user
        user_row = db.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": TEST_USER_EMAIL},
        ).fetchone()
        if not user_row:
            log("User lookup", False, expected=TEST_USER_EMAIL, actual="Not found")
            trans.rollback()
            db.close()
            return 1
        user_id = str(user_row[0])
        log("User lookup", True, actual=user_id)

        # 2) Create vault and assign role
        vault_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO vaults (id, name, owner_id, is_personal, created_at)
                VALUES (:id, 'Label Test Vault', :user_id, false, now())
            """),
            {"id": vault_id, "user_id": user_id},
        )
        db.execute(
            text("""
                INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role)
                VALUES (:vault_id, :user_id, 'owner')
            """),
            {"vault_id": vault_id, "user_id": user_id},
        )
        db.execute(
            text("""
                INSERT INTO vault_memberships (vault_id, user_id, role, created_at)
                VALUES (:vault_id, :user_id, 'owner', now())
            """),
            {"vault_id": vault_id, "user_id": user_id},
        )
        log("Create vault", True)

        # ---------------------------------------------------------------------
        # Test 1: Tag storage
        # ---------------------------------------------------------------------
        doc_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at, user_tags)
                VALUES (:id, :vault_id, 'https://test.example/tagged', 'Tagged Doc', '', now(), :tags)
            """),
            {"id": doc_id, "vault_id": vault_id, "tags": ["VaultBubbles", "Research"]},
        )
        db.flush()
        row = db.execute(
            text("SELECT user_tags FROM documents WHERE id = :id"),
            {"id": doc_id},
        ).fetchone()
        tags = row[0] if row else []
        log(
            "Tag storage",
            tags == ["VaultBubbles", "Research"] or set(tags) == {"VaultBubbles", "Research"},
            expected="['VaultBubbles', 'Research']",
            actual=str(tags),
        )

        # Verify GIN index exists (informational)
        idx = db.execute(
            text("""
                SELECT 1 FROM pg_indexes
                WHERE tablename = 'documents' AND indexname = 'documents_user_tags_gin_idx'
            """),
        ).fetchone()
        log("GIN index exists", idx is not None, actual="present" if idx else "missing")

        # ---------------------------------------------------------------------
        # Test 2: Signal aggregation
        # ---------------------------------------------------------------------
        root_id = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": vault_id},
        ).scalar()
        node_id = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": root_id},
        ).scalar()
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": vault_id, "nid": str(node_id), "did": doc_id},
        )
        # Add second doc with overlapping tag
        doc2_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at, user_tags)
                VALUES (:id, :vault_id, 'https://test.example/tagged2', 'Doc2', '', now(), :tags)
            """),
            {"id": doc2_id, "vault_id": vault_id, "tags": ["VaultBubbles"]},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": vault_id, "nid": str(node_id), "did": doc2_id},
        )
        db.flush()

        signals = db.execute(
            text("SELECT semantic_tree_v2.aggregate_node_signals(:vid, :nid)"),
            {"vid": vault_id, "nid": str(node_id)},
        ).scalar()
        doc_count = signals.get("doc_count", 0)
        tags_list = signals.get("tags", [])
        top_tag = tags_list[0]["tag"] if tags_list else None
        top_ratio = tags_list[0]["ratio"] if tags_list else 0
        log(
            "Signal aggregation",
            doc_count == 2 and top_tag == "VaultBubbles" and top_ratio >= 0.9,
            expected="doc_count=2, top_tag=VaultBubbles, ratio>=0.9",
            actual=f"doc_count={doc_count}, top_tag={top_tag}, ratio={top_ratio}",
        )

        # ---------------------------------------------------------------------
        # Test 2b: Cluster-only uniqueness (same doc cannot stay in 2 cluster nodes)
        # ---------------------------------------------------------------------
        uniq_node_a = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": root_id},
        ).scalar()
        uniq_node_b = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": root_id},
        ).scalar()
        uniq_doc = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at, user_tags)
                VALUES (:id, :vault_id, 'https://test.example/uniq', 'Uniq Doc', '', now(), ARRAY[]::text[])
            """),
            {"id": uniq_doc, "vault_id": vault_id},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": vault_id, "nid": str(uniq_node_a), "did": uniq_doc},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": vault_id, "nid": str(uniq_node_b), "did": uniq_doc},
        )
        uniq_rows = db.execute(
            text("""
                SELECT node_id
                FROM semantic_tree_v2.node_document nd
                JOIN semantic_tree_v2.tree_node tn
                  ON tn.id = nd.node_id AND tn.vault_id = nd.vault_id
                WHERE nd.vault_id = :vid
                  AND nd.document_id = :did
                  AND tn.node_type = 'cluster'
            """),
            {"vid": vault_id, "did": uniq_doc},
        ).fetchall()
        uniq_node_ids = [str(r[0]) for r in uniq_rows]
        log(
            "Cluster-only unique document assignment",
            len(uniq_node_ids) == 1 and uniq_node_ids[0] == str(uniq_node_b),
            expected=f"single cluster attachment on {uniq_node_b}",
            actual=str(uniq_node_ids),
        )

        # ---------------------------------------------------------------------
        # Test 3: Anchor detection (5 docs, 4 with VaultBubbles -> ratio 0.8)
        # ---------------------------------------------------------------------
        anchor_vault = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO vaults (id, name, owner_id, is_personal, created_at) VALUES (:id, 'Anchor', :uid, false, now())"),
            {"id": anchor_vault, "uid": user_id},
        )
        db.execute(
            text("INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role) VALUES (:vid, :uid, 'owner')"),
            {"vid": anchor_vault, "uid": user_id},
        )
        anchor_root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": anchor_vault},
        ).scalar()
        anchor_node = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": anchor_vault, "pid": anchor_root},
        ).scalar()
        for i in range(5):
            did = str(uuid.uuid4())
            tags_val = ["VaultBubbles"] if i < 4 else ["Other"]
            db.execute(
                text("""
                    INSERT INTO documents (id, vault_id, url, title, full_text, captured_at, user_tags)
                    VALUES (:id, :vault_id, :url, 'Doc', '', now(), :tags)
                """),
                {"id": did, "vault_id": anchor_vault, "url": f"https://ex.com/d{i}", "tags": tags_val},
            )
            db.execute(
                text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
                {"uid": user_id, "vid": anchor_vault, "nid": str(anchor_node), "did": did},
            )
        db.flush()

        suggested = db.execute(
            text("SELECT semantic_tree_v2.suggest_node_label(:vid, :nid)"),
            {"vid": anchor_vault, "nid": str(anchor_node)},
        ).scalar()
        label = suggested.get("label") if suggested else None
        log(
            "Anchor detection",
            label == "VaultBubbles",
            expected="VaultBubbles",
            actual=str(label),
        )

        # ---------------------------------------------------------------------
        # Test 4: Large cluster generalization (30 docs, ratio 0.25)
        # ---------------------------------------------------------------------
        gen_vault = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO vaults (id, name, owner_id, is_personal, created_at) VALUES (:id, 'Gen', :uid, false, now())"),
            {"id": gen_vault, "uid": user_id},
        )
        db.execute(
            text("INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role) VALUES (:vid, :uid, 'owner')"),
            {"vid": gen_vault, "uid": user_id},
        )
        gen_root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": gen_vault},
        ).scalar()
        gen_node = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": gen_vault, "pid": gen_root},
        ).scalar()
        # 30 docs: 8 with "TopTag" (ratio 8/30=0.27), 22 with other tags
        for i in range(30):
            did = str(uuid.uuid4())
            tags_val = ["TopTag"] if i < 8 else [f"Tag{i}"]
            db.execute(
                text("""
                    INSERT INTO documents (id, vault_id, url, title, full_text, captured_at, user_tags)
                    VALUES (:id, :vault_id, :url, 'Doc', '', now(), :tags)
                """),
                {"id": did, "vault_id": gen_vault, "url": f"https://ex.com/g{i}", "tags": tags_val},
            )
            db.execute(
                text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
                {"uid": user_id, "vid": gen_vault, "nid": str(gen_node), "did": did},
            )
        db.flush()

        gen_suggested = db.execute(
            text("SELECT semantic_tree_v2.suggest_node_label(:vid, :nid)"),
            {"vid": gen_vault, "nid": str(gen_node)},
        ).scalar()
        gen_label = gen_suggested.get("label") if gen_suggested else None
        log(
            "Large cluster generalization",
            gen_label == "TopTag",
            expected="TopTag (generalization rule, not anchor)",
            actual=str(gen_label),
        )

        # ---------------------------------------------------------------------
        # Test 5: Locked node protection
        # ---------------------------------------------------------------------
        locked_vault = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO vaults (id, name, owner_id, is_personal, created_at) VALUES (:id, 'Locked', :uid, false, now())"),
            {"id": locked_vault, "uid": user_id},
        )
        db.execute(
            text("INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role) VALUES (:vid, :uid, 'owner')"),
            {"vid": locked_vault, "uid": user_id},
        )
        locked_root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": locked_vault},
        ).scalar()
        locked_node = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": locked_vault, "pid": locked_root},
        ).scalar()
        locked_doc = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at, user_tags)
                VALUES (:id, :vault_id, 'https://x.com/l', 'L', '', now(), ARRAY['WouldRelabel']::text[])
            """),
            {"id": locked_doc, "vault_id": locked_vault},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": locked_vault, "nid": str(locked_node), "did": locked_doc},
        )
        db.execute(
            text("UPDATE semantic_tree_v2.tree_node SET title = 'BeforeRelabel', locked = true WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(locked_node), "vid": locked_vault},
        )
        db.flush()

        db.execute(text("SELECT semantic_tree_v2.relabel_vault(:vid)"), {"vid": locked_vault})
        db.flush()
        locked_row = db.execute(
            text("SELECT title FROM semantic_tree_v2.tree_node WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(locked_node), "vid": locked_vault},
        ).fetchone()
        locked_title = locked_row[0] if locked_row else None
        log(
            "Locked node protection",
            locked_title == "BeforeRelabel",
            expected="BeforeRelabel (unchanged)",
            actual=str(locked_title),
        )

        # ---------------------------------------------------------------------
        # Test 6: Pinned title protection
        # ---------------------------------------------------------------------
        pinned_vault = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO vaults (id, name, owner_id, is_personal, created_at) VALUES (:id, 'Pinned', :uid, false, now())"),
            {"id": pinned_vault, "uid": user_id},
        )
        db.execute(
            text("INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role) VALUES (:vid, :uid, 'owner')"),
            {"vid": pinned_vault, "uid": user_id},
        )
        pinned_root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": pinned_vault},
        ).scalar()
        pinned_node = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": pinned_vault, "pid": pinned_root},
        ).scalar()
        pinned_doc = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at, user_tags)
                VALUES (:id, :vault_id, 'https://x.com/p', 'P', '', now(), ARRAY['WouldRelabel']::text[])
            """),
            {"id": pinned_doc, "vault_id": pinned_vault},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": pinned_vault, "nid": str(pinned_node), "did": pinned_doc},
        )
        db.execute(
            text("UPDATE semantic_tree_v2.tree_node SET title = 'PinnedTitle', title_source = 'pinned' WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(pinned_node), "vid": pinned_vault},
        )
        db.flush()

        db.execute(text("SELECT semantic_tree_v2.relabel_vault(:vid)"), {"vid": pinned_vault})
        db.flush()
        pinned_row = db.execute(
            text("SELECT title FROM semantic_tree_v2.tree_node WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(pinned_node), "vid": pinned_vault},
        ).fetchone()
        pinned_title = pinned_row[0] if pinned_row else None
        log(
            "Pinned title protection",
            pinned_title == "PinnedTitle",
            expected="PinnedTitle (unchanged)",
            actual=str(pinned_title),
        )

        # ---------------------------------------------------------------------
        # Test 7: update_node_label guards + no-op on unchanged signature
        # ---------------------------------------------------------------------
        upd_vault = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO vaults (id, name, owner_id, is_personal, created_at) VALUES (:id, 'UpdateLabel', :uid, false, now())"),
            {"id": upd_vault, "uid": user_id},
        )
        db.execute(
            text("INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role) VALUES (:vid, :uid, 'owner')"),
            {"vid": upd_vault, "uid": user_id},
        )
        upd_root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": upd_vault},
        ).scalar()
        upd_node = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": upd_vault, "pid": upd_root},
        ).scalar()

        # First update should apply.
        db.execute(
            text("""
                SELECT semantic_tree_v2.update_node_label(
                    :vid, :nid, :label, CAST(:signals AS jsonb), :sig, :status, :src
                )
            """),
            {
                "vid": upd_vault,
                "nid": str(upd_node),
                "label": "Deterministic Label",
                "signals": '{"doc_count": 3, "tags": [{"tag":"X","freq":2,"ratio":0.66}]}',
                "sig": "sig-1",
                "status": "auto",
                "src": None,
            },
        )
        first_row = db.execute(
            text("""
                SELECT title, title_source, label_signature_hash, label_status
                FROM semantic_tree_v2.tree_node
                WHERE id = :nid AND vault_id = :vid
            """),
            {"nid": str(upd_node), "vid": upd_vault},
        ).fetchone()
        log(
            "update_node_label applies update",
            first_row is not None
            and first_row[0] == "Deterministic Label"
            and first_row[1] == "auto"
            and first_row[2] == "sig-1"
            and first_row[3] == "auto",
            expected="title updated, title_source=auto, signature/status persisted",
            actual=str(first_row),
        )

        # Same signature should no-op (title unchanged).
        db.execute(
            text("""
                SELECT semantic_tree_v2.update_node_label(
                    :vid, :nid, :label, CAST(:signals AS jsonb), :sig, :status, :src
                )
            """),
            {
                "vid": upd_vault,
                "nid": str(upd_node),
                "label": "ShouldNotApply",
                "signals": '{"doc_count": 3}',
                "sig": "sig-1",
                "status": "needs_llm",
                "src": None,
            },
        )
        same_sig_row = db.execute(
            text("SELECT title, label_status FROM semantic_tree_v2.tree_node WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(upd_node), "vid": upd_vault},
        ).fetchone()
        log(
            "update_node_label skips unchanged signature",
            same_sig_row is not None and same_sig_row[0] == "Deterministic Label" and same_sig_row[1] == "auto",
            expected="no-op when signature unchanged",
            actual=str(same_sig_row),
        )

        # Pinned node should remain unchanged.
        db.execute(
            text("UPDATE semantic_tree_v2.tree_node SET title_source = 'pinned', title = 'PinnedLockedIn' WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(upd_node), "vid": upd_vault},
        )
        db.execute(
            text("""
                SELECT semantic_tree_v2.update_node_label(
                    :vid, :nid, :label, CAST(:signals AS jsonb), :sig, :status, :src
                )
            """),
            {
                "vid": upd_vault,
                "nid": str(upd_node),
                "label": "PinnedShouldNotChange",
                "signals": '{"doc_count": 4}',
                "sig": "sig-2",
                "status": "needs_llm",
                "src": "llm",
            },
        )
        pinned_guard_row = db.execute(
            text("SELECT title, title_source, label_signature_hash FROM semantic_tree_v2.tree_node WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(upd_node), "vid": upd_vault},
        ).fetchone()
        log(
            "update_node_label skips pinned nodes",
            pinned_guard_row is not None
            and pinned_guard_row[0] == "PinnedLockedIn"
            and pinned_guard_row[1] == "pinned"
            and pinned_guard_row[2] == "sig-1",
            expected="pinned node unchanged",
            actual=str(pinned_guard_row),
        )

        # Manual node should remain unchanged.
        db.execute(
            text("UPDATE semantic_tree_v2.tree_node SET title_source = 'manual', title = 'ManualLockedIn' WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(upd_node), "vid": upd_vault},
        )
        db.execute(
            text("""
                SELECT semantic_tree_v2.update_node_label(
                    :vid, :nid, :label, CAST(:signals AS jsonb), :sig, :status, :src
                )
            """),
            {
                "vid": upd_vault,
                "nid": str(upd_node),
                "label": "ManualShouldNotChange",
                "signals": '{"doc_count": 5}',
                "sig": "sig-3",
                "status": "needs_llm",
                "src": None,
            },
        )
        manual_guard_row = db.execute(
            text("SELECT title, title_source, label_signature_hash FROM semantic_tree_v2.tree_node WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(upd_node), "vid": upd_vault},
        ).fetchone()
        log(
            "update_node_label skips manual nodes",
            manual_guard_row is not None
            and manual_guard_row[0] == "ManualLockedIn"
            and manual_guard_row[1] == "manual"
            and manual_guard_row[2] == "sig-1",
            expected="manual node unchanged",
            actual=str(manual_guard_row),
        )

        # Locked node should remain unchanged.
        db.execute(
            text("UPDATE semantic_tree_v2.tree_node SET title_source = 'auto', locked = true, title = 'LockedIn' WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(upd_node), "vid": upd_vault},
        )
        db.execute(
            text("""
                SELECT semantic_tree_v2.update_node_label(
                    :vid, :nid, :label, CAST(:signals AS jsonb), :sig, :status, :src
                )
            """),
            {
                "vid": upd_vault,
                "nid": str(upd_node),
                "label": "LockedShouldNotChange",
                "signals": '{"doc_count": 6}',
                "sig": "sig-4",
                "status": "needs_llm",
                "src": None,
            },
        )
        locked_guard_row = db.execute(
            text("SELECT title, locked, label_signature_hash FROM semantic_tree_v2.tree_node WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(upd_node), "vid": upd_vault},
        ).fetchone()
        log(
            "update_node_label skips locked nodes",
            locked_guard_row is not None
            and locked_guard_row[0] == "LockedIn"
            and bool(locked_guard_row[1]) is True
            and locked_guard_row[2] == "sig-1",
            expected="locked node unchanged",
            actual=str(locked_guard_row),
        )

        # ---------------------------------------------------------------------
        # Test 8: Deterministic relabel uses content signals
        # ---------------------------------------------------------------------
        from app.labeling import (
            relabel_vault_deterministic,
            relabel_vault_deterministic_with_status,
            refine_pending_llm_labels,
        )

        det_vault = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO vaults (id, name, owner_id, is_personal, created_at) VALUES (:id, 'Deterministic', :uid, false, now())"),
            {"id": det_vault, "uid": user_id},
        )
        db.execute(
            text("INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role) VALUES (:vid, :uid, 'owner')"),
            {"vid": det_vault, "uid": user_id},
        )
        det_root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": det_vault},
        ).scalar()
        det_node = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": det_vault, "pid": det_root},
        ).scalar()
        for i in range(3):
            did = str(uuid.uuid4())
            db.execute(
                text("""
                    INSERT INTO documents (id, vault_id, url, title, extracted_text, full_text, captured_at, user_tags)
                    VALUES (:id, :vault_id, :url, :title, :text, '', now(), ARRAY[]::text[])
                """),
                {
                    "id": did,
                    "vault_id": det_vault,
                    "url": f"https://det.example/{i}",
                    "title": "Postgres migration checklist",
                    "text": "Postgres migration rollout checklist for schema migration and index tuning.",
                },
            )
            db.execute(
                text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
                {"uid": user_id, "vid": det_vault, "nid": str(det_node), "did": did},
            )

        stats = relabel_vault_deterministic(db, uuid.UUID(det_vault))
        det_row = db.execute(
            text("""
                SELECT title, label_signature_hash, label_status, label_signals
                FROM semantic_tree_v2.tree_node
                WHERE id = :nid AND vault_id = :vid
            """),
            {"nid": str(det_node), "vid": det_vault},
        ).fetchone()
        det_title = (det_row[0] or "").lower() if det_row else ""
        det_hash = det_row[1] if det_row else None
        det_status = det_row[2] if det_row else None
        det_signals = det_row[3] if det_row else {}
        log(
            "Deterministic relabel content signals",
            det_row is not None
            and bool(det_hash)
            and det_status == "auto"
            and "tfidf_terms" in (det_signals or {})
            and ("postgres" in det_title or "migration" in det_title),
            expected="non-generic content-derived label with hash/status",
            actual=f"title={det_title}, status={det_status}, hash={det_hash}, stats={stats}",
        )

        # ---------------------------------------------------------------------
        # Test 9: needs_llm -> final async refinement lifecycle
        # ---------------------------------------------------------------------
        # Change node content so signature changes and deterministic pass requeues.
        det_extra_doc = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, extracted_text, full_text, captured_at, user_tags)
                VALUES (:id, :vault_id, :url, :title, :text, '', now(), ARRAY[]::text[])
            """),
            {
                "id": det_extra_doc,
                "vault_id": det_vault,
                "url": "https://det.example/extra",
                "title": "Database migration playbook",
                "text": "Database migration playbook with rollout checks and schema changes.",
            },
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": det_vault, "nid": str(det_node), "did": det_extra_doc},
        )

        lifecycle_stats = relabel_vault_deterministic_with_status(
            db,
            uuid.UUID(det_vault),
            status="needs_llm",
        )
        queued_row = db.execute(
            text("""
                SELECT label_status, title_source, label_signature_hash, label_signals
                FROM semantic_tree_v2.tree_node
                WHERE id = :nid AND vault_id = :vid
            """),
            {"nid": str(det_node), "vid": det_vault},
        ).fetchone()
        queued_ok = (
            queued_row is not None
            and queued_row[0] == "needs_llm"
            and queued_row[1] == "auto"
            and queued_row[3] is not None
        )
        log(
            "Deterministic relabel queues needs_llm",
            queued_ok,
            expected="status=needs_llm with stored label_signals",
            actual=f"queued_row={queued_row}, stats={lifecycle_stats}",
        )

        refine_stats = refine_pending_llm_labels(
            db,
            vault_id=uuid.UUID(det_vault),
            limit=10,
            llm_callable=lambda _signals: "LLM Refined Migration Topic",
        )
        refined_row = db.execute(
            text("""
                SELECT title, label_status, title_source, label_signature_hash
                FROM semantic_tree_v2.tree_node
                WHERE id = :nid AND vault_id = :vid
            """),
            {"nid": str(det_node), "vid": det_vault},
        ).fetchone()
        refined_ok = (
            refined_row is not None
            and refined_row[0] == "LLM Refined Migration Topic"
            and refined_row[1] == "final"
            and refined_row[2] == "llm"
            and bool(refined_row[3])
        )
        log(
            "Async refinement finalizes needs_llm nodes",
            refined_ok,
            expected="title updated, status=final, title_source=llm",
            actual=f"row={refined_row}, stats={refine_stats}",
        )

        # ---------------------------------------------------------------------
        # Test 10: Subtree-aware signals aggregate descendant documents
        # ---------------------------------------------------------------------
        subtree_vault = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO vaults (id, name, owner_id, is_personal, created_at) VALUES (:id, 'Subtree', :uid, false, now())"),
            {"id": subtree_vault, "uid": user_id},
        )
        db.execute(
            text("INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role) VALUES (:vid, :uid, 'owner')"),
            {"vid": subtree_vault, "uid": user_id},
        )
        subtree_root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": subtree_vault},
        ).scalar()
        subtree_parent = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": subtree_vault, "pid": subtree_root},
        ).scalar()
        subtree_child = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": subtree_vault, "pid": subtree_parent},
        ).scalar()
        for i in range(3):
            did = str(uuid.uuid4())
            db.execute(
                text("""
                    INSERT INTO documents (id, vault_id, url, title, extracted_text, full_text, captured_at, user_tags)
                    VALUES (:id, :vault_id, :url, :title, :text, '', now(), ARRAY[]::text[])
                """),
                {
                    "id": did,
                    "vault_id": subtree_vault,
                    "url": f"https://subtree.example/{i}",
                    "title": "Vector database migration notes",
                    "text": "Vector database migration playbook and schema rollout steps.",
                },
            )
            db.execute(
                text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
                {"uid": user_id, "vid": subtree_vault, "nid": str(subtree_child), "did": did},
            )

        subtree_stats = relabel_vault_deterministic(db, uuid.UUID(subtree_vault))
        subtree_parent_row = db.execute(
            text("""
                SELECT label_signals, label_signature_hash
                FROM semantic_tree_v2.tree_node
                WHERE id = :nid AND vault_id = :vid
            """),
            {"nid": str(subtree_parent), "vid": subtree_vault},
        ).fetchone()
        subtree_signals = subtree_parent_row[0] if subtree_parent_row else {}
        subtree_doc_count = int((subtree_signals or {}).get("doc_count", 0))
        log(
            "Subtree-aware parent doc_count",
            subtree_parent_row is not None and subtree_doc_count >= 3 and bool(subtree_parent_row[1]),
            expected="parent cluster should include descendant docs in doc_count/signature",
            actual=f"doc_count={subtree_doc_count}, stats={subtree_stats}, row={subtree_parent_row}",
        )

        # ---------------------------------------------------------------------
        # Test 11: Fetch-layer compression lifts trivial auto-generated wrapper
        # ---------------------------------------------------------------------
        from app.semantic_tree_v2_repo import SemanticTreeV2Repo, compress_tree

        subtree_repo = SemanticTreeV2Repo(DATABASE_URL)
        subtree_tree = subtree_repo.fetch_tree(user_id, subtree_vault, conn=db.connection())
        compressed = compress_tree(subtree_tree)
        parent_exists = str(subtree_parent) in compressed.get("by_id", {})
        child_node = compressed.get("by_id", {}).get(str(subtree_child))
        child_parent = child_node.get("parent_id") if child_node else None
        log(
            "Compression lifts single-child auto-generated node",
            (not parent_exists) and str(child_parent) == str(subtree_root),
            expected="parent removed and child reattached to root",
            actual=f"parent_exists={parent_exists}, child_parent={child_parent}, root={subtree_root}",
        )

        # ---------------------------------------------------------------------
        # Test 12: Generic LLM output guard keeps strong deterministic label
        # ---------------------------------------------------------------------
        guard_vault = str(uuid.uuid4())
        db.execute(
            text("INSERT INTO vaults (id, name, owner_id, is_personal, created_at) VALUES (:id, 'Guard', :uid, false, now())"),
            {"id": guard_vault, "uid": user_id},
        )
        db.execute(
            text("INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role) VALUES (:vid, :uid, 'owner')"),
            {"vid": guard_vault, "uid": user_id},
        )
        guard_root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": guard_vault},
        ).scalar()
        guard_node = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": guard_vault, "pid": guard_root},
        ).scalar()
        for i in range(2):
            did = str(uuid.uuid4())
            db.execute(
                text("""
                    INSERT INTO documents (id, vault_id, url, title, extracted_text, full_text, captured_at, user_tags)
                    VALUES (:id, :vault_id, :url, :title, :text, '', now(), ARRAY[]::text[])
                """),
                {
                    "id": did,
                    "vault_id": guard_vault,
                    "url": f"https://guard.example/{i}",
                    "title": "Postgres tuning guide",
                    "text": "Postgres tuning guide guide guide for index strategy and query performance. Guide to optimization.",
                },
            )
            db.execute(
                text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
                {"uid": user_id, "vid": guard_vault, "nid": str(guard_node), "did": did},
            )
        relabel_vault_deterministic_with_status(db, uuid.UUID(guard_vault), status="needs_llm")
        deterministic_before = db.execute(
            text("SELECT title FROM semantic_tree_v2.tree_node WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(guard_node), "vid": guard_vault},
        ).scalar()
        guard_stats = refine_pending_llm_labels(
            db,
            vault_id=uuid.UUID(guard_vault),
            limit=10,
            llm_callable=lambda _signals: "Undefined Cluster Insights",
        )
        guard_row = db.execute(
            text("SELECT title, label_status, title_source FROM semantic_tree_v2.tree_node WHERE id = :nid AND vault_id = :vid"),
            {"nid": str(guard_node), "vid": guard_vault},
        ).fetchone()
        log(
            "Generic LLM output guard",
            guard_row is not None
            and guard_row[0] == deterministic_before
            and guard_row[1] == "final"
            and guard_row[2] == "llm",
            expected="generic LLM label should not replace strong deterministic label",
            actual=f"before={deterministic_before}, row={guard_row}, stats={guard_stats}",
        )

        # ---------------------------------------------------------------------
        # Test 12b: /topics/recluster mode routing behavior
        # ---------------------------------------------------------------------
        from fastapi import HTTPException
        from app.main import recluster_topics

        class _User:
            def __init__(self, uid: str):
                self.id = uuid.UUID(uid)

        route_user = _User(user_id)
        subtree_400 = False
        try:
            recluster_topics(
                mode="subtree",
                db=db,
                user=route_user,
                _=None,
            )
        except HTTPException as exc:
            subtree_400 = exc.status_code == 400
        log(
            "Recluster mode routing validates subtree params",
            subtree_400,
            expected="HTTP 400 when subtree mode lacks vault_id/root_node_id",
            actual=str(subtree_400),
        )

        # ---------------------------------------------------------------------
        # Test 13: Existing tree functions still work
        # ---------------------------------------------------------------------
        from app.semantic_tree_v2_repo import SemanticTreeV2Repo

        repo = SemanticTreeV2Repo(DATABASE_URL)
        db_conn = db.connection()
        tree = repo.fetch_tree(user_id, vault_id, conn=db_conn)
        has_nodes = len(tree.get("by_id", {})) > 0
        log("fetch_tree_flat", has_nodes, expected="non-empty tree", actual=f"{len(tree.get('by_id', {}))} nodes")

        # Check fetch_tree_flat returns title_source/title_evidence + new label metadata
        flat = db.execute(
            text("SELECT semantic_tree_v2.fetch_tree_flat(:uid, :vid)"),
            {"uid": user_id, "vid": vault_id},
        ).scalar()
        nodes = flat.get("nodes", []) if flat else []
        first_node = nodes[0] if nodes else {}
        has_title_source = "title_source" in first_node
        log("fetch_tree_flat includes title_source", has_title_source, actual=str(first_node.get("title_source", "missing")))
        has_label_fields = (
            "label_signals" in first_node
            and "label_signature_hash" in first_node
            and "label_status" in first_node
        )
        log(
            "fetch_tree_flat includes label fields",
            has_label_fields,
            expected="label_signals, label_signature_hash, label_status present",
            actual=str(first_node),
        )
        has_compression_metadata = (
            "node_type" in first_node
            and "locked" in first_node
            and "auto_generated" in first_node
        )
        log(
            "fetch_tree_flat includes compression metadata",
            has_compression_metadata,
            expected="node_type, locked, auto_generated present",
            actual=str(first_node),
        )

        trans.rollback()

    except Exception as e:
        log("Test run", False, actual=str(e), details=str(type(e)))
        trans.rollback()
    finally:
        db.close()

    failed = sum(1 for r in results if not r.passed)
    print(f"\n--- {len(results)} tests, {failed} failed ---")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(run_tests())
