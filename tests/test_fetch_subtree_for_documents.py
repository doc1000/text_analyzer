#!/usr/bin/env python3
"""
Tests for semantic_tree_v2 fetch_subtree_for_documents.

Validates:
- Minimal subtree returned for document set
- Compression collapses trivial cluster chains
- Manual nodes preserved
- Locked nodes preserved
- Cross-vault isolation enforced
- Empty document_ids returns empty tree

Prerequisites:
- Migrations 008, 009, 010, 011, 012 applied
- DATABASE_URL set
- User test@example.com exists

Usage:
    export DATABASE_URL=postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb
    python tests/test_fetch_subtree_for_documents.py
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

    print("\n--- fetch_subtree_for_documents tests ---\n")

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
                VALUES (:id, 'Test Vault', :user_id, false, now())
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

        # 3) Minimal subtree: root -> A -> B -> C, doc on C
        root = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root')"),
            {"uid": user_id, "vid": vault_id},
        ).scalar()
        node_a = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, :pid, 'A')"),
            {"uid": user_id, "vid": vault_id, "pid": root},
        ).scalar()
        node_b = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, :pid, 'B')"),
            {"uid": user_id, "vid": vault_id, "pid": node_a},
        ).scalar()
        node_c = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, :pid, 'C')"),
            {"uid": user_id, "vid": vault_id, "pid": node_b},
        ).scalar()

        doc_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at)
                VALUES (:id, :vault_id, 'https://test.example/doc', 'Doc', '', now())
            """),
            {"id": doc_id, "vault_id": vault_id},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": vault_id, "nid": str(node_c), "did": doc_id},
        )
        db.flush()

        from app.semantic_tree_v2_repo import SemanticTreeV2Repo

        repo = SemanticTreeV2Repo(DATABASE_URL)
        db_conn = db.connection()

        tree = repo.fetch_subtree_for_documents(user_id, vault_id, [doc_id], conn=db_conn)
        by_id = tree.get("by_id", {})
        expected_ids = {str(root), str(node_a), str(node_b), str(node_c)}
        actual_ids = set(by_id.keys())
        log(
            "Minimal subtree",
            actual_ids == expected_ids and len(by_id) == 4,
            expected=f"4 nodes: {expected_ids}",
            actual=f"{len(by_id)} nodes: {actual_ids}",
        )

        # 4) Compression: root -> cluster1 -> cluster2 -> C (doc), collapse cluster1 and cluster2
        root2 = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root2')"),
            {"uid": user_id, "vid": vault_id},
        ).scalar()
        cluster1 = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": root2},
        ).scalar()
        cluster2 = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": cluster1},
        ).scalar()
        cluster_c = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": cluster2},
        ).scalar()

        doc2_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at)
                VALUES (:id, :vault_id, 'https://test.example/doc2', 'Doc2', '', now())
            """),
            {"id": doc2_id, "vault_id": vault_id},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": vault_id, "nid": str(cluster_c), "did": doc2_id},
        )
        db.flush()

        tree2 = repo.fetch_subtree_for_documents(user_id, vault_id, [doc2_id], conn=db_conn, compress=True)
        by_id2 = tree2.get("by_id", {})
        # After compression: root2 -> cluster_c (cluster1 and cluster2 collapsed)
        compress_ok = str(root2) in by_id2 and str(cluster_c) in by_id2
        compress_ok = compress_ok and str(cluster1) not in by_id2 and str(cluster2) not in by_id2
        log(
            "Compression collapses trivial clusters",
            compress_ok,
            expected="root2 and cluster_c only",
            actual=f"nodes: {list(by_id2.keys())}",
        )

        # 5) Manual node preserved: root -> manual -> cluster -> C (doc), manual must stay
        root3 = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root3')"),
            {"uid": user_id, "vid": vault_id},
        ).scalar()
        manual_mid = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, :pid, 'Manual')"),
            {"uid": user_id, "vid": vault_id, "pid": root3},
        ).scalar()
        cluster_leaf = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": manual_mid},
        ).scalar()

        doc3_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at)
                VALUES (:id, :vault_id, 'https://test.example/doc3', 'Doc3', '', now())
            """),
            {"id": doc3_id, "vault_id": vault_id},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": vault_id, "nid": str(cluster_leaf), "did": doc3_id},
        )
        db.flush()

        tree3 = repo.fetch_subtree_for_documents(user_id, vault_id, [doc3_id], conn=db_conn, compress=True)
        by_id3 = tree3.get("by_id", {})
        manual_preserved = str(manual_mid) in by_id3
        log(
            "Manual node preserved",
            manual_preserved,
            expected="manual_mid in tree",
            actual=f"manual in tree: {manual_preserved}",
        )

        # 6) Locked node preserved: root -> locked_cluster -> cluster -> C (doc)
        root4 = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root4')"),
            {"uid": user_id, "vid": vault_id},
        ).scalar()
        locked_node = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": root4},
        ).scalar()
        db.execute(
            text("UPDATE semantic_tree_v2.tree_node SET locked = TRUE WHERE id = :id"),
            {"id": str(locked_node)},
        )
        cluster_under = db.execute(
            text("SELECT semantic_tree_v2.create_cluster_node(:uid, :vid, :pid)"),
            {"uid": user_id, "vid": vault_id, "pid": locked_node},
        ).scalar()

        doc4_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO documents (id, vault_id, url, title, full_text, captured_at)
                VALUES (:id, :vault_id, 'https://test.example/doc4', 'Doc4', '', now())
            """),
            {"id": doc4_id, "vault_id": vault_id},
        )
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": user_id, "vid": vault_id, "nid": str(cluster_under), "did": doc4_id},
        )
        db.flush()

        tree4 = repo.fetch_subtree_for_documents(user_id, vault_id, [doc4_id], conn=db_conn, compress=True)
        by_id4 = tree4.get("by_id", {})
        locked_preserved = str(locked_node) in by_id4
        log(
            "Locked node preserved",
            locked_preserved,
            expected="locked_node in tree",
            actual=f"locked in tree: {locked_preserved}",
        )

        # 7) Cross-vault isolation: vault_c owned by user2 only, user has no access
        user2_row = db.execute(
            text("SELECT id FROM users WHERE email != :email LIMIT 1"),
            {"email": TEST_USER_EMAIL},
        ).fetchone()
        if user2_row:
            user2_id = str(user2_row[0])
            vault_c_id = str(uuid.uuid4())
            db.execute(
                text("""
                    INSERT INTO vaults (id, name, owner_id, is_personal, created_at)
                    VALUES (:id, 'Vault C', :user_id, false, now())
                """),
                {"id": vault_c_id, "user_id": user2_id},
            )
            db.execute(
                text("""
                    INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role)
                    VALUES (:vault_id, :user_id, 'owner')
                """),
                {"vault_id": vault_c_id, "user_id": user2_id},
            )
            root_c = db.execute(
                text("SELECT semantic_tree_v2.create_node(:uid, :vid, NULL, 'Root C')"),
                {"uid": user2_id, "vid": vault_c_id},
            ).scalar()
            doc_c_id = str(uuid.uuid4())
            db.execute(
                text("""
                    INSERT INTO documents (id, vault_id, url, title, full_text, captured_at)
                    VALUES (:id, :vault_id, 'https://test.example/docc', 'DocC', '', now())
                """),
                {"id": doc_c_id, "vault_id": vault_c_id},
            )
            db.execute(
                text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
                {"uid": user2_id, "vid": vault_c_id, "nid": str(root_c), "did": doc_c_id},
            )
            db.flush()

            savepoint = db.begin_nested()
            try:
                repo.fetch_subtree_for_documents(user_id, vault_c_id, [doc_c_id], conn=db_conn)
                savepoint.commit()
                log("Cross-vault isolation", False, expected="Exception", actual="No exception")
            except Exception as e:
                savepoint.rollback()
                has_priv = "insufficient_privilege" in str(e).lower() or "privilege" in str(e).lower()
                log("Cross-vault isolation", has_priv, expected="insufficient_privilege", actual=str(e))
        else:
            log("Cross-vault isolation", True, details="Skipped (no second user)")

        # 8) Empty document_ids
        tree_empty = repo.fetch_subtree_for_documents(user_id, vault_id, [], conn=db_conn)
        empty_ok = tree_empty.get("root_ids") == [] and tree_empty.get("by_id") == {}
        log("Empty document_ids", empty_ok, expected="empty tree", actual=str(tree_empty))

        trans.rollback()
    except Exception as e:
        if "trans" in dir() and trans:
            trans.rollback()
        raise
    finally:
        db.close()

    passed = sum(1 for r in results if r.passed)
    total = len(results)
    print(f"\n--- {passed}/{total} passed ---\n")
    return 0 if passed == total else 1


if __name__ == "__main__":
    sys.exit(run_tests())
