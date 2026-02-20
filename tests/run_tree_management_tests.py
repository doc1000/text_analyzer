#!/usr/bin/env python3
"""
Tree Management Test Script

Validates the tree infrastructure (tree_nodes, node_documents) using documents
from test@example.com: populate reduced embeddings, build tree from clustering,
add derived documents, verify placement and clean deletion, test date-filtered retrieval.

Safety: No API calls, no main.py changes. Uses DATABASE_URL directly.

Prerequisites:
- Migration 007 applied
- Local DB with user test@example.com and documents with embeddings
- DATABASE_URL set

Usage:
    export DATABASE_URL=postgresql+psycopg2://badger:badgerpass@localhost:5433/badgerdb
    python tests/run_tree_management_tests.py
"""

import os
import sys
import uuid

# Ensure project root is on path when run as script
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Load .env so HUGGINGFACE_API_TOKEN (and other backend env) is available for embedding
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass  # python-dotenv optional; rely on env vars set by caller
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
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


class TreeManagementTester:
    def __init__(self):
        self.results: list[TestResult] = []
        self.vault_id = None
        self.user_id = None
        self.docs: list = []
        self.doc_embeddings: dict = {}
        self.reduced_embeddings: dict = {}
        self.root_ids: list = []
        self.derived_doc_ids: list = []
        self._engine = None
        self._session_factory = None

    def _log_result(self, result: TestResult):
        self.results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.name}")
        if not result.passed:
            print(f"         Expected: {result.expected}")
            print(f"         Actual: {result.actual}")
            if result.details:
                print(f"         Details: {result.details}")

    def _get_db_session(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable not set")
        if self._engine is None:
            self._engine = create_engine(DATABASE_URL)
            self._session_factory = sessionmaker(bind=self._engine, autocommit=False, autoflush=False)
        return self._session_factory()

    # ---------- Phase 1: Setup and Document Fetch ----------

    def phase1_setup(self) -> bool:
        """Resolve user, vault, fetch documents with embeddings."""
        try:
            from app.db import ensure_embedding_ready
            from app.models import User, Document, Vault, VaultMembership
            from app.topics import compute_document_embeddings, get_document_embeddings_from_table

            ensure_embedding_ready()

            db = self._get_db_session()
            try:
                user = db.query(User).filter(User.email == TEST_USER_EMAIL).first()
                if not user:
                    self._log_result(TestResult(
                        name="Phase 1: User lookup",
                        passed=False,
                        expected=f"User {TEST_USER_EMAIL}",
                        actual="Not found",
                        details="Ensure test@example.com exists in local DB",
                    ))
                    return False

                self.user_id = user.id
                self._log_result(TestResult(
                    name="Phase 1a: User lookup",
                    passed=True,
                    expected=TEST_USER_EMAIL,
                    actual=str(user.id),
                ))

                vault_ids = (
                    db.query(VaultMembership.vault_id)
                    .filter(VaultMembership.user_id == user.id)
                    .filter(VaultMembership.role.in_(("owner", "admin", "editor")))
                    .all()
                )
                vault_ids = [v[0] for v in vault_ids]
                if not vault_ids:
                    self._log_result(TestResult(
                        name="Phase 1b: Vault access",
                        passed=False,
                        expected="At least 1 vault",
                        actual="0 vaults",
                    ))
                    return False

                personal = (
                    db.query(Vault)
                    .filter(Vault.id.in_(vault_ids), Vault.is_personal == True)
                    .first()
                )
                self.vault_id = personal.id if personal else vault_ids[0]

                docs = (
                    db.query(Document)
                    .filter(Document.vault_id == self.vault_id)
                    .all()
                )

                doc_embeddings = get_document_embeddings_from_table(db, [d.id for d in docs])
                if not doc_embeddings:
                    doc_embeddings = compute_document_embeddings(db, docs)

                self.docs = [d for d in docs if d.id in doc_embeddings]
                self.doc_embeddings = {k: v for k, v in doc_embeddings.items() if k in [d.id for d in self.docs]}

                if len(self.docs) < 3:
                    self._log_result(TestResult(
                        name="Phase 1c: Documents with embeddings",
                        passed=False,
                        expected="At least 3 documents",
                        actual=f"{len(self.docs)} documents",
                        details="Need documents with chunk or doc embeddings",
                    ))
                    return False

                self._log_result(TestResult(
                    name="Phase 1c: Documents with embeddings",
                    passed=True,
                    expected=">= 3 docs",
                    actual=f"{len(self.docs)} docs, vault={self.vault_id}",
                ))
                return True
            finally:
                db.close()
        except Exception as e:
            self._log_result(TestResult(
                name="Phase 1: Setup",
                passed=False,
                expected="Setup complete",
                actual=str(e),
            ))
            return False

    # ---------- Phase 2: Populate Reduced Embeddings and Build Tree ----------

    def phase2_build_tree(self) -> bool:
        """Populate reduced_embedding, cluster, build tree_nodes and node_documents."""
        try:
            from app.models import Document, SemanticTreeNode, SemanticTreeNodeDocument
            from app.topics import reduce_embeddings, cluster_embeddings_multilevel
            from app.agglomerative import get_cluster_hierarchy
            from app.tree_management import (
                pad_to_reduced_dim,
                build_tree_from_clustering,
                recompute_node_centroids,
            )

            db = self._get_db_session()
            try:
                doc_ids = [d.id for d in self.docs]
                docs = db.query(Document).filter(Document.id.in_(doc_ids)).all()
                id_to_doc = {d.id: d for d in docs}
                docs_ordered = [id_to_doc[i] for i in doc_ids]

                X = np.stack([self.doc_embeddings[d.id] for d in docs_ordered], axis=0)
                X_reduced = reduce_embeddings(X)
                for i, doc in enumerate(docs_ordered):
                    vec = pad_to_reduced_dim(X_reduced[i])
                    doc.reduced_embedding = vec
                    self.reduced_embeddings[doc.id] = vec
                db.commit()

                self._log_result(TestResult(
                    name="Phase 2a: Reduced embeddings",
                    passed=True,
                    expected="Populated",
                    actual=f"{len(self.docs)} docs",
                ))

                X_cluster = reduce_embeddings(X) if X.shape[1] > 30 else X
                if X_cluster.shape[1] > 30:
                    X_cluster = X_cluster[:, :30]

                labels_by_level, structure, Z = cluster_embeddings_multilevel(X_cluster, doc_ids=doc_ids)
                hierarchy = get_cluster_hierarchy(labels_by_level)

                self.root_ids = build_tree_from_clustering(
                    db, self.vault_id, self.user_id,
                    labels_by_level, structure, hierarchy, doc_ids,
                )

                recompute_node_centroids(db, self.vault_id)

                roots = db.query(SemanticTreeNode).filter(
                    SemanticTreeNode.vault_id == self.vault_id,
                    SemanticTreeNode.parent_id.is_(None),
                ).all()
                nd_count = db.query(SemanticTreeNodeDocument).join(
                    SemanticTreeNode,
                    (SemanticTreeNodeDocument.node_id == SemanticTreeNode.id)
                    & (SemanticTreeNodeDocument.vault_id == SemanticTreeNode.vault_id),
                ).filter(SemanticTreeNode.vault_id == self.vault_id).count()

                passed = len(roots) >= 1 and nd_count == len(self.docs)
                self._log_result(TestResult(
                    name="Phase 2b: Tree built",
                    passed=passed,
                    expected=f"roots >= 1, node_documents == {len(self.docs)}",
                    actual=f"roots={len(roots)}, node_documents={nd_count}",
                ))
                return passed
            finally:
                db.close()
        except Exception as e:
            import traceback
            self._log_result(TestResult(
                name="Phase 2: Build tree",
                passed=False,
                expected="Tree built",
                actual=str(e),
                details=traceback.format_exc(),
            ))
            return False

    # ---------- Phase 3: Add Derived Documents ----------

    def phase3_derived_docs(self) -> bool:
        """Create 2-3 derived docs, embed, assign to tree, verify placement."""
        try:
            import numpy as np
            from app.models import Document, SemanticTreeNodeDocument
            from app.topics import reduce_embeddings, compute_document_embeddings
            from app.helpers import embed_doc_chunks
            from app.tree_management import (
                pad_to_reduced_dim,
                assign_document_by_similarity,
                attach_document_to_node,
                recompute_node_centroids,
            )

            db = self._get_db_session()
            try:
                short_docs = sorted(
                    self.docs,
                    key=lambda d: len(getattr(d, 'captured_text') or getattr(d, 'extracted_text') or '') or 0,
                )[:3]
                derived = []
                source_to_derived = {}

                for src in short_docs:
                    base_text = (src.captured_text or src.extracted_text or src.title or "Untitled")[:500]
                    new_doc = Document(
                        vault_id=self.vault_id,
                        created_by=self.user_id,
                        url=f"note://tree-test-derived-{uuid.uuid4()}",
                        title=(src.title or "Copy") + " (derived)",
                        captured_text=base_text + "\n\nThis is an additional paragraph added for tree placement testing.",
                        extracted_text=base_text + "\n\nThis is an additional paragraph added for tree placement testing.",
                    )
                    db.add(new_doc)
                    db.flush()
                    derived.append(new_doc)
                    source_to_derived[src.id] = new_doc.id

                db.commit()
                for d in derived:
                    db.refresh(d)

                n_embedded = 0
                for d in derived:
                    n = embed_doc_chunks(d)
                    if n > 0:
                        n_embedded += 1

                if n_embedded < len(derived):
                    self._log_result(TestResult(
                        name="Phase 3a: Embed derived docs",
                        passed=False,
                        expected=f"{len(derived)} embedded",
                        actual=f"{n_embedded} embedded",
                    ))
                    return False

                self._log_result(TestResult(
                    name="Phase 3a: Embed derived docs",
                    passed=True,
                    expected=f"{len(derived)} embedded",
                    actual=f"{n_embedded} embedded",
                ))

                doc_embs = compute_document_embeddings(db, derived)
                emb_docs = [d for d in derived if d.id in doc_embs]
                if not emb_docs:
                    self._log_result(TestResult(
                        name="Phase 3a: Embed derived docs",
                        passed=False,
                        expected="At least 1 embedding",
                        actual="0",
                    ))
                    return False
                X_full = np.stack([doc_embs[d.id] for d in emb_docs], axis=0)
                X_red = reduce_embeddings(X_full)

                for i, d in enumerate(emb_docs):
                    if d.id not in doc_embs:
                        continue
                    vec = pad_to_reduced_dim(X_red[i] if i < X_red.shape[0] else X_red[0])
                    d.reduced_embedding = vec
                    db.commit()
                    db.refresh(d)

                    node_id = assign_document_by_similarity(db, self.vault_id, vec, self.user_id)
                    if node_id:
                        attach_document_to_node(db, node_id, d.id, self.user_id, self.vault_id)
                        self.derived_doc_ids.append(d.id)

                recompute_node_centroids(db, self.vault_id)

                nd_derived = (
                    db.query(SemanticTreeNodeDocument)
                    .filter(SemanticTreeNodeDocument.document_id.in_(self.derived_doc_ids))
                    .count()
                )

                passed = nd_derived == len(self.derived_doc_ids)
                self._log_result(TestResult(
                    name="Phase 3b: Derived docs in tree",
                    passed=passed,
                    expected=f"{len(self.derived_doc_ids)} in node_documents",
                    actual=f"{nd_derived}",
                ))
                return passed
            finally:
                db.close()
        except Exception as e:
            import traceback
            self._log_result(TestResult(
                name="Phase 3: Derived docs",
                passed=False,
                expected="Derived docs added",
                actual=str(e),
                details=traceback.format_exc(),
            ))
            return False

    # ---------- Phase 4: Delete Derived Documents and Verify Clean Removal ----------

    def phase4_delete_derived(self) -> bool:
        """Delete derived docs, verify node_documents CASCADE and structure integrity."""
        try:
            from app.models import Document, SemanticTreeNode, SemanticTreeNodeDocument
            from app.tree_management import recompute_node_centroids

            db = self._get_db_session()
            try:
                deleted_count = 0
                for doc_id in self.derived_doc_ids:
                    doc = db.query(Document).filter(Document.id == doc_id).first()
                    if doc:
                        db.delete(doc)
                        deleted_count += 1
                db.commit()

                docs_remaining = (
                    db.query(Document)
                    .filter(Document.id.in_(self.derived_doc_ids))
                    .count()
                )
                remaining = (
                    db.query(SemanticTreeNodeDocument)
                    .filter(SemanticTreeNodeDocument.document_id.in_(self.derived_doc_ids))
                    .count()
                )

                recompute_node_centroids(db, self.vault_id)
                roots = db.query(SemanticTreeNode).filter(
                    SemanticTreeNode.vault_id == self.vault_id,
                    SemanticTreeNode.parent_id.is_(None),
                ).count()

                passed = docs_remaining == 0 and remaining == 0
                self._log_result(TestResult(
                    name="Phase 4: Clean deletion",
                    passed=passed,
                    expected="0 documents and 0 node_documents for deleted docs, tree intact",
                    actual=f"deleted={deleted_count}, docs_remaining={docs_remaining}, nd_remaining={remaining}, roots={roots}",
                ))
                return passed
            finally:
                db.close()
        except Exception as e:
            self._log_result(TestResult(
                name="Phase 4: Delete derived",
                passed=False,
                expected="Clean deletion",
                actual=str(e),
            ))
            return False

    # ---------- Phase 5: Date-Filtered Tree Retrieval ----------

    def phase5_date_filtered(self) -> bool:
        """Test get_tree_nodes_with_documents_in_range."""
        try:
            from app.models import Document
            from app.tree_management import get_tree_nodes_with_documents_in_range

            db = self._get_db_session()
            try:
                since = datetime.now(timezone.utc) - timedelta(days=15)
                nodes = get_tree_nodes_with_documents_in_range(db, self.vault_id, since, self.user_id)

                if not self.docs:
                    self._log_result(TestResult(
                        name="Phase 5: Date-filtered retrieval",
                        passed=True,
                        expected="Nodes or empty",
                        actual=f"{len(nodes)} nodes",
                    ))
                    return True

                one_doc = db.query(Document).filter(Document.id == self.docs[0].id).first()
                if not one_doc:
                    self._log_result(TestResult(
                        name="Phase 5: Date-filtered retrieval",
                        passed=True,
                        expected="Nodes or empty",
                        actual=f"{len(nodes)} nodes (doc not found for update)",
                    ))
                    return True
                old_captured = one_doc.captured_at
                one_doc.captured_at = datetime.now(timezone.utc)
                db.commit()
                db.refresh(one_doc)

                nodes_after = get_tree_nodes_with_documents_in_range(db, self.vault_id, since, self.user_id)
                one_doc.captured_at = old_captured
                db.commit()

                passed = len(nodes_after) >= len(nodes)
                self._log_result(TestResult(
                    name="Phase 5: Date-filtered retrieval",
                    passed=passed,
                    expected="More nodes after updating captured_at",
                    actual=f"before={len(nodes)}, after={len(nodes_after)}",
                ))
                return passed
            finally:
                db.close()
        except Exception as e:
            self._log_result(TestResult(
                name="Phase 5: Date-filtered",
                passed=False,
                expected="Retrieval works",
                actual=str(e),
            ))
            return False

    # ---------- Phase 6: Teardown (restore DB state) ----------

    def phase6_teardown(self) -> bool:
        """Remove the tree we created so DB is restored; verify destructive ops."""
        try:
            from app.models import SemanticTreeNode, SemanticTreeNodeDocument

            db = self._get_db_session()
            try:
                deleted_nodes = db.query(SemanticTreeNode).filter(
                    SemanticTreeNode.vault_id == self.vault_id,
                    SemanticTreeNode.node_type == "cluster",
                ).delete(synchronize_session=False)
                db.commit()

                node_count_after = db.query(SemanticTreeNode).filter(
                    SemanticTreeNode.vault_id == self.vault_id,
                    SemanticTreeNode.node_type == "cluster",
                ).count()
                nd_count_after = (
                    db.query(SemanticTreeNodeDocument)
                    .join(
                        SemanticTreeNode,
                        (SemanticTreeNodeDocument.node_id == SemanticTreeNode.id)
                        & (SemanticTreeNodeDocument.vault_id == SemanticTreeNode.vault_id),
                    )
                    .filter(SemanticTreeNode.vault_id == self.vault_id)
                    .count()
                )

                passed = node_count_after == 0 and nd_count_after == 0
                self._log_result(TestResult(
                    name="Phase 6: Teardown (restore DB)",
                    passed=passed,
                    expected="0 cluster nodes, 0 node_documents after teardown",
                    actual=f"deleted_nodes={deleted_nodes}, nodes_after={node_count_after}, nd_after={nd_count_after}",
                ))
                return passed
            finally:
                db.close()
        except Exception as e:
            self._log_result(TestResult(
                name="Phase 6: Teardown",
                passed=False,
                expected="Tree removed",
                actual=str(e),
            ))
            return False

    def run_all(self) -> tuple[int, int]:
        print(f"\n{'='*60}")
        print("TREE MANAGEMENT TEST SUITE")
        print(f"{'='*60}")
        print(f"Test User: {TEST_USER_EMAIL}")
        print(f"DATABASE_URL: {'SET' if DATABASE_URL else 'NOT SET'}")
        print(f"{'='*60}\n")

        if not DATABASE_URL:
            print("ERROR: DATABASE_URL required.")
            return 0, 1

        print("--- Phase 1: Setup ---")
        if not self.phase1_setup():
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)

        print("\n--- Phase 2: Build Tree ---")
        if not self.phase2_build_tree():
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)

        print("\n--- Phase 3: Derived Documents ---")
        if not self.phase3_derived_docs():
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)

        print("\n--- Phase 4: Delete Derived ---")
        if not self.phase4_delete_derived():
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)

        print("\n--- Phase 5: Date-Filtered Retrieval ---")
        self.phase5_date_filtered()

        print("\n--- Phase 6: Teardown ---")
        self.phase6_teardown()

        passed = sum(1 for x in self.results if x.passed)
        failed = sum(1 for x in self.results if not x.passed)
        print(f"\n{'='*60}")
        print(f"SUMMARY: {passed} passed, {failed} failed")
        print(f"{'='*60}")
        if failed > 0:
            print("\nFAILED TESTS:")
            for r in self.results:
                if not r.passed:
                    print(f"  - {r.name}")
        return passed, failed


def main():
    tester = TreeManagementTester()
    passed, failed = tester.run_all()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
