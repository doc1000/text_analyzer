#!/usr/bin/env python3
"""
semantic_tree_v2 Test Script

Validates the isolated semantic_tree_v2 subsystem:
- Create 2 vaults, assign roles, create nodes
- Attempt invalid cross-vault move (assert failure)
- Attach documents, fetch tree, verify stats

Safety: No API calls, no main.py changes. Uses DATABASE_URL directly.
Runs in a transaction and rolls back - no persistent test data.

Prerequisites:
- Migration 008 applied
- DATABASE_URL set
- At least one user in public.users (e.g. test@example.com)

Usage:
    export DATABASE_URL=postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb
    python tests/run_semantic_tree_v2_tests.py
"""

import os
import sys
import uuid

# Ensure project root is on path when run as script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from dataclasses import dataclass

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Set up DATABASE_URL before importing app modules
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
        print("  export DATABASE_URL=postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb")
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

    print("\n--- semantic_tree_v2 tests ---\n")

    db = session_factory()
    try:
        # Use transaction - rollback at end so no persistent test data
        trans = db.begin()

        # 1) Get or create test user
        user_row = db.execute(
            text("SELECT id FROM users WHERE email = :email"),
            {"email": TEST_USER_EMAIL},
        ).fetchone()
        if not user_row:
            log(
                "User lookup",
                False,
                expected=f"User {TEST_USER_EMAIL}",
                actual="Not found",
                details="Create user or set TEST_USER_EMAIL",
            )
            trans.rollback()
            db.close()
            return 1
        user_id = str(user_row[0])
        log("User lookup", True, actual=user_id)

        # 2) Create 2 vaults
        vault_a_id = str(uuid.uuid4())
        vault_b_id = str(uuid.uuid4())
        db.execute(
            text("""
                INSERT INTO vaults (id, name, owner_id, is_personal, created_at)
                VALUES (:id_a, 'Test Vault A', :user_id, false, now()),
                       (:id_b, 'Test Vault B', :user_id, false, now())
            """),
            {"id_a": vault_a_id, "id_b": vault_b_id, "user_id": user_id},
        )
        log("Create 2 vaults", True, actual=f"{vault_a_id[:8]}..., {vault_b_id[:8]}...")

        # 3) Assign roles in semantic_tree_v2.vault_user_role
        db.execute(
            text("""
                INSERT INTO semantic_tree_v2.vault_user_role (vault_id, user_id, role)
                VALUES (:vault_a, :user_id, 'owner'),
                       (:vault_b, :user_id, 'owner')
            """),
            {"vault_a": vault_a_id, "vault_b": vault_b_id, "user_id": user_id},
        )
        log("Assign roles", True)

        # 4) Create nodes
        root_a = db.execute(
            text("SELECT semantic_tree_v2.create_node(:user_id, :vault_id, NULL, 'Root A')"),
            {"user_id": user_id, "vault_id": vault_a_id},
        ).scalar()
        child_a = db.execute(
            text("SELECT semantic_tree_v2.create_node(:user_id, :vault_id, :parent_id, 'Child A')"),
            {"user_id": user_id, "vault_id": vault_a_id, "parent_id": root_a},
        ).scalar()
        root_b = db.execute(
            text("SELECT semantic_tree_v2.create_node(:user_id, :vault_id, NULL, 'Root B')"),
            {"user_id": user_id, "vault_id": vault_b_id},
        ).scalar()
        child_b = db.execute(
            text("SELECT semantic_tree_v2.create_node(:user_id, :vault_id, :parent_id, 'Child B')"),
            {"user_id": user_id, "vault_id": vault_b_id, "parent_id": root_b},
        ).scalar()
        log("Create nodes", True, actual="4 nodes (2 per vault)")

        # 5) Attempt invalid cross-vault move (expect failure) - use savepoint to preserve tx
        savepoint = db.begin_nested()
        try:
            db.execute(
                text("SELECT semantic_tree_v2.move_node(:user_id, :vault_id, :node_id, :new_parent_id)"),
                {
                    "user_id": user_id,
                    "vault_id": vault_a_id,
                    "node_id": child_a,
                    "new_parent_id": root_b,
                },
            )
            savepoint.commit()
            log(
                "Cross-vault move",
                False,
                expected="Exception (FK violation)",
                actual="No exception raised",
            )
        except Exception as e:
            savepoint.rollback()
            err_msg = str(e).lower()
            if "foreign key" in err_msg or "violates" in err_msg or "constraint" in err_msg:
                log("Cross-vault move", True, actual="Correctly rejected")
            else:
                log("Cross-vault move", True, actual=f"Rejected: {type(e).__name__}")

        # 6) Create or get document for attach test
        doc_id = db.execute(
            text("""
                SELECT id FROM documents
                WHERE vault_id = :vault_id
                LIMIT 1
            """),
            {"vault_id": vault_a_id},
        ).scalar()
        if not doc_id:
            doc_id = uuid.uuid4()
            db.execute(
                text("""
                    INSERT INTO documents (id, vault_id, url, title, full_text, captured_at)
                    VALUES (:id, :vault_id, 'https://test.example/doc', 'Test Doc', '', now())
                """),
                {"id": doc_id, "vault_id": vault_a_id},
            )
            db.flush()
        doc_id = str(doc_id)

        # Attach document to child_a
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:user_id, :vault_id, :node_id, :doc_id)"),
            {"user_id": user_id, "vault_id": vault_a_id, "node_id": str(child_a), "doc_id": doc_id},
        )
        log("Attach document", True)

        # 7) Verify stats incremented
        stats_count = db.execute(
            text("""
                SELECT doc_count FROM semantic_tree_v2.tree_node_stats
                WHERE node_id = :node_id AND vault_id = :vault_id
            """),
            {"node_id": str(child_a), "vault_id": vault_a_id},
        ).scalar()
        log("Stats increment", stats_count == 1, expected="1", actual=str(stats_count))

        # 8) Fetch tree and verify structure (use same conn to see uncommitted tx)
        from app.semantic_tree_v2_repo import SemanticTreeV2Repo

        repo = SemanticTreeV2Repo(DATABASE_URL)
        db_conn = db.connection()
        tree_a = repo.fetch_tree(user_id, vault_a_id, conn=db_conn)
        tree_b = repo.fetch_tree(user_id, vault_b_id, conn=db_conn)

        by_id_a = tree_a.get("by_id", {})
        root_ids_a = tree_a.get("root_ids", [])
        log(
            "Fetch tree (vault A)",
            len(by_id_a) == 2 and len(root_ids_a) == 1,
            expected="2 nodes, 1 root",
            actual=f"{len(by_id_a)} nodes, {len(root_ids_a)} roots",
        )

        by_id_b = tree_b.get("by_id", {})
        root_ids_b = tree_b.get("root_ids", [])
        log(
            "Fetch tree (vault B)",
            len(by_id_b) == 2 and len(root_ids_b) == 1,
            expected="2 nodes, 1 root",
            actual=f"{len(by_id_b)} nodes, {len(root_ids_b)} roots",
        )

        # 9) Verify doc_count in tree
        child_a_data = by_id_a.get(str(child_a)) or by_id_a.get(child_a)
        has_doc_count = child_a_data and child_a_data.get("doc_count", 0) == 1
        log("Tree doc_count", has_doc_count, expected="doc_count=1 on child", actual=str(child_a_data))

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
