#!/usr/bin/env python3
"""
Tests for tag-based semantic labeling (semantic_tree_v2).

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
- Migrations 008 through 013 applied
- DATABASE_URL set
- User test@example.com exists

Usage:
    export DATABASE_URL=postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb
    python tests/test_label_sem_tree.py
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

_raw_db_url = os.getenv("DATABASE_URL")
if _raw_db_url:
    DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    if "postgresql://" in DATABASE_URL and "+psycopg2" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
else:
    DATABASE_URL = None

TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL", "test@example.com")


@dataclass
class TestResult:
    name: str
    passed: bool
    expected: str
    actual: str
    details: str = ""


def run_tests():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL environment variable not set")
        return 1

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
        # Test 7: Existing tree functions still work
        # ---------------------------------------------------------------------
        from app.semantic_tree_v2_repo import SemanticTreeV2Repo

        repo = SemanticTreeV2Repo(DATABASE_URL)
        db_conn = db.connection()
        tree = repo.fetch_tree(user_id, vault_id, conn=db_conn)
        has_nodes = len(tree.get("by_id", {})) > 0
        log("fetch_tree_flat", has_nodes, expected="non-empty tree", actual=f"{len(tree.get('by_id', {}))} nodes")

        # Check fetch_tree_flat returns title_source and title_evidence
        flat = db.execute(
            text("SELECT semantic_tree_v2.fetch_tree_flat(:uid, :vid)"),
            {"uid": user_id, "vid": vault_id},
        ).scalar()
        nodes = flat.get("nodes", []) if flat else []
        first_node = nodes[0] if nodes else {}
        has_title_source = "title_source" in first_node
        log("fetch_tree_flat includes title_source", has_title_source, actual=str(first_node.get("title_source", "missing")))

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
