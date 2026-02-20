#!/usr/bin/env python3
"""
Tests for semantic_tree_v2 anchors and staging behavior.

Validates:
- Only one staging node per vault
- Anchored document never leaves anchor subtree
- Document without match goes to staging
- Reclustering subtree does not modify manual nodes
- Stability matching preserves node ids when overlap high
- Cross-vault constraints enforced

Prerequisites:
- Migrations 008, 009, 010, 011 applied
- DATABASE_URL set
- User test@example.com with vault and documents with embeddings

Usage:
    export DATABASE_URL=postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb
    python tests/test_semantic_tree_anchors_staging.py
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

import numpy as np
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

    print("\n--- semantic_tree_v2 anchors and staging tests ---\n")

    db = session_factory()
    try:
        from app.models import User, Document, Vault, VaultMembership, SemanticTreeNode, SemanticTreeNodeDocument
        from app.tree_management import (
            get_staging_node_id,
            get_document_anchor,
            set_document_anchor,
            get_subtree_node_ids,
            place_document,
            recluster_scope,
            build_tree_from_clustering,
            recompute_node_centroids,
            pad_to_reduced_dim,
        )
        from app.topics import reduce_embeddings, cluster_embeddings_multilevel, compute_document_embeddings, get_document_embeddings_from_table
        from app.agglomerative import get_cluster_hierarchy
        from app.db import ensure_embedding_ready
    except ImportError as e:
        log("Import", False, actual=str(e))
        return 1

    trans = db.begin()
    try:
        ensure_embedding_ready()

        user = db.query(User).filter(User.email == TEST_USER_EMAIL).first()
        if not user:
            log("User lookup", False, expected=TEST_USER_EMAIL, actual="Not found")
            trans.rollback()
            return 1
        user_id = user.id
        log("User lookup", True, actual=str(user_id))

        vault_ids = [
            v[0] for v in
            db.query(VaultMembership.vault_id)
            .filter(VaultMembership.user_id == user_id)
            .filter(VaultMembership.role.in_(("owner", "admin", "editor")))
            .all()
        ]
        if not vault_ids:
            log("Vault access", False, expected="At least 1 vault", actual="0")
            trans.rollback()
            return 1

        personal = db.query(Vault).filter(Vault.id.in_(vault_ids), Vault.is_personal == True).first()
        vault_id = personal.id if personal else vault_ids[0]

        docs = db.query(Document).filter(Document.vault_id == vault_id).all()
        if len(docs) < 3:
            log("Documents", False, expected="At least 3 documents", actual=f"{len(docs)}")
            trans.rollback()
            return 1

        doc_embeddings = get_document_embeddings_from_table(db, [d.id for d in docs])
        if not doc_embeddings:
            doc_embeddings = compute_document_embeddings(db, docs)
        docs_with_emb = [d for d in docs if d.id in doc_embeddings]
        if len(docs_with_emb) < 3:
            log("Documents with embeddings", False, expected="At least 3", actual=f"{len(docs_with_emb)}")
            trans.rollback()
            return 1

        doc_ids = [d.id for d in docs_with_emb[:5]]
        docs_ordered = [d for d in docs_with_emb if d.id in doc_ids]

        X = np.stack([doc_embeddings[d.id] for d in docs_ordered], axis=0)
        X_reduced = reduce_embeddings(X)
        for i, doc in enumerate(docs_ordered):
            vec = pad_to_reduced_dim(X_reduced[i])
            doc.reduced_embedding = vec
        db.commit()

        for d in docs_ordered:
            db.refresh(d)

        labels_by_level, structure, _ = cluster_embeddings_multilevel(
            X_reduced[:, :30] if X_reduced.shape[1] > 30 else X_reduced,
            doc_ids=doc_ids,
        )
        hierarchy = get_cluster_hierarchy(labels_by_level)

        root_ids = build_tree_from_clustering(
            db, vault_id, user_id,
            labels_by_level, structure, hierarchy, doc_ids,
        )
        recompute_node_centroids(db, vault_id)
        log("Build tree", True, actual=f"{len(root_ids)} roots")

        staging1 = get_staging_node_id(db, vault_id, user_id)
        staging2 = get_staging_node_id(db, vault_id, user_id)
        log("One staging per vault", staging1 == staging2, expected="Same node", actual=f"{staging1 == staging2}")

        staging_count = db.execute(
            text("SELECT COUNT(*) FROM semantic_tree_v2.tree_node WHERE vault_id = :v AND node_role = 'staging'"),
            {"v": str(vault_id)},
        ).scalar()
        log("Exactly one staging node", staging_count == 1, expected="1", actual=str(staging_count))

        root_id = root_ids[0]
        subtree_ids = get_subtree_node_ids(db, root_id)
        set_document_anchor(db, doc_ids[0], vault_id, root_id, user_id)
        vec = list(docs_ordered[0].reduced_embedding) if hasattr(docs_ordered[0].reduced_embedding, "__iter__") else []
        if not vec:
            vec = doc_embeddings[docs_ordered[0].id].tolist()[:30]
        vec = pad_to_reduced_dim(vec)
        placed_node = place_document(db, vault_id, doc_ids[0], vec, user_id)
        log("Anchored doc in subtree", placed_node in subtree_ids, expected="True", actual=str(placed_node in subtree_ids))

        random_vec = np.random.randn(30).astype(np.float32).tolist()
        placed_staging = place_document(db, vault_id, doc_ids[1], random_vec, user_id, min_similarity=0.99)
        log("Doc without match goes to staging", placed_staging == staging1, expected="True", actual=str(placed_staging == staging1))

        manual_node = db.execute(
            text("SELECT semantic_tree_v2.create_node(:uid, :vid, :pid, 'Manual Node')"),
            {"uid": str(user_id), "vid": str(vault_id), "pid": str(root_id)},
        ).scalar()
        db.commit()
        db.execute(
            text("SELECT semantic_tree_v2.attach_document(:uid, :vid, :nid, :did)"),
            {"uid": str(user_id), "vid": str(vault_id), "nid": str(manual_node), "did": str(doc_ids[2])},
        )
        db.commit()

        manual_before = db.execute(
            text("SELECT COUNT(*) FROM semantic_tree_v2.node_document WHERE node_id = :nid"),
            {"nid": str(manual_node)},
        ).scalar()
        recluster_scope(db, vault_id, root_id, "subtree", user_id)
        manual_after = db.execute(
            text("SELECT COUNT(*) FROM semantic_tree_v2.node_document WHERE node_id = :nid"),
            {"nid": str(manual_node)},
        ).scalar()
        log("Reclustering does not modify manual nodes", manual_before == manual_after == 1, expected="1", actual=f"{manual_after}")

        node_ids_before = set(
            row[0] for row in db.execute(
                text("SELECT id FROM semantic_tree_v2.tree_node WHERE vault_id = :v AND node_type = 'cluster'"),
                {"v": str(vault_id)},
            ).fetchall()
        )
        recluster_scope(db, vault_id, root_id, "subtree", user_id, overlap_threshold=0.0)
        node_ids_after = set(
            row[0] for row in db.execute(
                text("SELECT id FROM semantic_tree_v2.tree_node WHERE vault_id = :v AND node_type = 'cluster'"),
                {"v": str(vault_id)},
            ).fetchall()
        )
        overlap_count = len(node_ids_before & node_ids_after)
        log("Stability matching preserves node ids", overlap_count > 0, expected="Some overlap", actual=f"{overlap_count}")

        if len(vault_ids) > 1:
            vault_b_id = vault_ids[1]
            doc_b = db.query(Document).filter(Document.vault_id == vault_b_id).first()
            if doc_b:
                try:
                    place_document(db, vault_id, doc_b.id, random_vec, user_id)
                    log("Cross-vault constraints", False, expected="Reject", actual="Placed doc from other vault")
                except ValueError:
                    log("Cross-vault constraints", True, actual="Rejected")
            else:
                log("Cross-vault constraints", True, actual="Skipped (no doc in vault B)")
        else:
            log("Cross-vault constraints", True, actual="Skipped (single vault)")

    except Exception as e:
        import traceback
        log("Tests", False, actual=str(e), details=traceback.format_exc())
    finally:
        try:
            trans.rollback()
        except Exception:
            pass
        db.close()

    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed)
    print(f"\n--- Summary: {passed} passed, {failed} failed ---\n")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(run_tests())
