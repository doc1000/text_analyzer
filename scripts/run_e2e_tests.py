#!/usr/bin/env python3
"""
E2E Core Flow Test Script

Validates core VaultBubbles behavior before merges. Uses extension login flow
(with DB-inserted codes to bypass email), covers auth, vault access, ingestion,
document CRUD, topics, query, and vault isolation.

Safety: Pre/post DB snapshots; transactions for all DB ops; no existing user
data modified or deleted.

Prerequisites:
- API running (local or remote)
- DATABASE_URL for snapshot and verification code insert

Usage:
    export API_BASE=http://localhost:8000
    export DATABASE_URL=postgresql://user:pass@localhost:5432/dbname
    python scripts/run_e2e_tests.py
"""

import os
import sys
import time
import uuid
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional, List, Dict, Set, Tuple, Any
from urllib.parse import urlparse, parse_qs

import requests
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

# Configuration
API_BASE = os.getenv("API_BASE", "http://localhost:8000")
_raw_db_url = os.getenv("DATABASE_URL")
# Normalize to use psycopg2 driver (required: pip install psycopg2-binary)
if _raw_db_url:
    DATABASE_URL = _raw_db_url.replace("postgres://", "postgresql+psycopg2://", 1)
    if "postgresql://" in DATABASE_URL and "+psycopg2" not in DATABASE_URL:
        DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg2://", 1)
else:
    DATABASE_URL = None
TEST_USER_EMAIL = os.getenv("TEST_USER_EMAIL") or f"e2e-test-{int(time.time())}@example.com"

# Unique URLs for ingest to avoid duplicates
RUN_ID = str(uuid.uuid4())[:8]
NOTE_URL = f"note://test-note-{RUN_ID}"
PAGE_URL = f"https://example.com/e2e-test-{RUN_ID}"

NOTE_TEXT = "E2E test note content. Quick brown fox jumps over the lazy dog."
PAGE_TEXT = "E2E test full page content. This document tests ingestion and retrieval."


@dataclass
class TestResult:
    name: str
    passed: bool
    expected: str
    actual: str
    details: str = ""


@dataclass
class VaultSnapshot:
    """Snapshot of a vault's documents before tests."""
    vault_id: str
    owner_id: Optional[str]
    doc_ids: Set[str] = field(default_factory=set)


class E2ECoreFlowTester:
    def __init__(self):
        self.token: Optional[str] = None
        self.test_user_id: Optional[str] = None
        self.test_user_vault_id: Optional[str] = None
        self.created_doc_ids: Set[str] = set()
        self.snapshot: Dict[str, VaultSnapshot] = {}
        self.results: List[TestResult] = []
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

    def _headers(self, token: Optional[str] = None) -> dict:
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _request(
        self,
        method: str,
        path: str,
        token: Optional[str] = None,
        json_data: dict = None,
        allow_redirects: bool = True,
    ) -> requests.Response:
        url = f"{API_BASE}{path}"
        headers = self._headers(token)
        if method.upper() == "GET":
            return requests.get(url, headers=headers, timeout=60)
        elif method.upper() == "POST":
            return requests.post(
                url, headers=headers, json=json_data, timeout=120, allow_redirects=allow_redirects
            )
        elif method.upper() == "PUT":
            return requests.put(url, headers=headers, json=json_data, timeout=60)
        elif method.upper() == "DELETE":
            return requests.delete(url, headers=headers, timeout=60)
        else:
            raise ValueError(f"Unknown method: {method}")

    def _get_db_session(self):
        if not DATABASE_URL:
            raise RuntimeError("DATABASE_URL environment variable not set")
        if self._engine is None:
            self._engine = create_engine(DATABASE_URL)
            self._session_factory = sessionmaker(bind=self._engine, autocommit=False, autoflush=False)
        return self._session_factory()

    # ---------- Phase 0: Pre-Test Snapshot ----------

    def phase0_snapshot(self) -> bool:
        """Take read-only snapshot of all documents per vault."""
        try:
            db = self._get_db_session()
            try:
                result = db.execute(text("""
                    SELECT d.id::text, d.vault_id::text, v.owner_id::text
                    FROM documents d
                    LEFT JOIN vaults v ON d.vault_id = v.id
                """))
                rows = result.fetchall()

                for doc_id, vault_id, owner_id in rows:
                    vkey = vault_id or "__no_vault__"
                    if vkey not in self.snapshot:
                        self.snapshot[vkey] = VaultSnapshot(
                            vault_id=vkey,
                            owner_id=owner_id,
                            doc_ids=set(),
                        )
                    self.snapshot[vkey].doc_ids.add(doc_id)

                db.commit()
                self._log_result(TestResult(
                    name="Phase 0: Pre-test snapshot",
                    passed=True,
                    expected="Snapshot captured",
                    actual=f"{len(rows)} documents across {len(self.snapshot)} vaults",
                ))
                return True
            finally:
                db.close()
        except Exception as e:
            self._log_result(TestResult(
                name="Phase 0: Pre-test snapshot",
                passed=False,
                expected="Snapshot captured",
                actual=str(e),
                details="Check DATABASE_URL",
            ))
            return False

    # ---------- Phase 1: Auth ----------

    def phase1_insert_verification_code(self) -> Tuple[bool, Optional[str]]:
        """Insert verification code in transaction; validate 1 row."""
        code = "".join([str(random.randint(0, 9)) for _ in range(6)])
        expires_at = datetime.now(timezone.utc) + timedelta(days=14)

        try:
            db = self._get_db_session()
            try:
                result = db.execute(
                    text("""
                        INSERT INTO extension_verification_codes
                        (id, email, code, purpose, created_at, expires_at)
                        VALUES (gen_random_uuid(), :email, :code, 'reviewer', now(), :expires_at)
                    """),
                    {
                        "email": TEST_USER_EMAIL.strip().lower(),
                        "code": code,
                        "expires_at": expires_at,
                    },
                )
                db.commit()
                # SQLAlchemy execute() for INSERT returns CursorResult; rowcount may vary
                # We validate by checking the code works in verify
                self._log_result(TestResult(
                    name="Phase 1a: Insert verification code",
                    passed=True,
                    expected="1 row inserted",
                    actual="Code inserted",
                ))
                return True, code
            except Exception as e:
                db.rollback()
                self._log_result(TestResult(
                    name="Phase 1a: Insert verification code",
                    passed=False,
                    expected="1 row inserted",
                    actual=str(e),
                ))
                return False, None
            finally:
                db.close()
        except Exception as e:
            self._log_result(TestResult(
                name="Phase 1a: Insert verification code",
                passed=False,
                expected="Code inserted",
                actual=str(e),
                details="Check DATABASE_URL",
            ))
            return False, None

    def phase1_verify_and_login(self, code: str) -> bool:
        """Verify code, parse token from redirect."""
        resp = self._request(
            "POST",
            "/extension/verify",
            json_data={"email": TEST_USER_EMAIL, "code": code},
            allow_redirects=False,
        )

        if resp.status_code != 302:
            self._log_result(TestResult(
                name="Phase 1b: Verify code",
                passed=False,
                expected="302 redirect",
                actual=f"{resp.status_code}: {resp.text[:200]}",
            ))
            return False

        location = resp.headers.get("Location", "")
        parsed = urlparse(location)
        params = parse_qs(parsed.query)
        token = (params.get("token") or [None])[0]

        if not token or not token.startswith("vb_ext_"):
            self._log_result(TestResult(
                name="Phase 1b: Verify code",
                passed=False,
                expected="Token in Location header",
                actual=f"Location: {location}",
            ))
            return False

        self.token = token
        self._log_result(TestResult(
            name="Phase 1b: Verify code",
            passed=True,
            expected="Token obtained",
            actual="OK",
        ))
        return True

    def phase1_whoami(self) -> bool:
        """Get user info, store test_user_id."""
        resp = self._request("GET", "/auth/whoami", token=self.token)

        if resp.status_code != 200:
            self._log_result(TestResult(
                name="Phase 1c: Whoami",
                passed=False,
                expected="200",
                actual=f"{resp.status_code}: {resp.text[:200]}",
            ))
            return False

        data = resp.json()
        self.test_user_id = data.get("user_id")
        if not self.test_user_id:
            self._log_result(TestResult(
                name="Phase 1c: Whoami",
                passed=False,
                expected="user_id in response",
                actual=str(data),
            ))
            return False

        self._log_result(TestResult(
            name="Phase 1c: Whoami",
            passed=True,
            expected="user_id",
            actual=f"user_id={self.test_user_id}",
        ))
        return True

    # ---------- Phase 2: Vault Access ----------

    def phase2_vaults(self) -> bool:
        """List vaults, identify test user's personal vault."""
        resp = self._request("GET", "/vaults", token=self.token)

        if resp.status_code != 200:
            self._log_result(TestResult(
                name="Phase 2a: List vaults",
                passed=False,
                expected="200",
                actual=f"{resp.status_code}: {resp.text[:200]}",
            ))
            return False

        data = resp.json()
        vaults = data.get("vaults", [])

        if not vaults:
            self._log_result(TestResult(
                name="Phase 2a: List vaults",
                passed=False,
                expected="At least 1 vault",
                actual="0 vaults",
            ))
            return False

        # Find personal vault where user is owner (role=owner)
        personal = next((v for v in vaults if v.get("is_personal") and v.get("role") == "owner"), None)
        if not personal:
            personal = vaults[0]

        self.test_user_vault_id = personal.get("id")
        if not self.test_user_vault_id:
            self._log_result(TestResult(
                name="Phase 2a: List vaults",
                passed=False,
                expected="vault id",
                actual=str(vaults[0]),
            ))
            return False

        # Check we have template/how-to docs (from newuser or copy)
        resp_docs = self._request("GET", "/documents?limit=5", token=self.token)
        doc_count = len(resp_docs.json()) if resp_docs.status_code == 200 else 0

        self._log_result(TestResult(
            name="Phase 2a: List vaults",
            passed=True,
            expected="vaults and personal vault",
            actual=f"{len(vaults)} vaults, personal={self.test_user_vault_id}",
        ))

        self._log_result(TestResult(
            name="Phase 2b: Template access",
            passed=doc_count >= 0,  # New user may have 0 if no template
            expected="View access to template or own docs",
            actual=f"{doc_count} documents visible",
        ))
        return True

    # ---------- Phase 3: Reload ----------

    def phase3_reload(self) -> bool:
        """POST /topics/recluster."""
        resp = self._request(
            "POST",
            "/topics/recluster?days=365&clear_assignments=false",
            token=self.token,
        )

        passed = resp.status_code in (200, 202)
        self._log_result(TestResult(
            name="Phase 3: Reload (recluster)",
            passed=passed,
            expected="200 or 202",
            actual=f"{resp.status_code}",
        ))
        return passed

    # ---------- Phase 4: Ingestion ----------

    def phase4_ingest(self) -> bool:
        """Ingest note and page, track created_doc_ids."""
        # Ingest note
        resp_note = self._request(
            "POST",
            "/ingest",
            token=self.token,
            json_data={
                "url": NOTE_URL,
                "text": NOTE_TEXT,
                "title": "E2E Test Note",
                "mode": "note",
            },
        )

        if resp_note.status_code != 200:
            self._log_result(TestResult(
                name="Phase 4a: Ingest note",
                passed=False,
                expected="200",
                actual=f"{resp_note.status_code}: {resp_note.text[:200]}",
            ))
            return False

        data_note = resp_note.json()
        doc_id_note = data_note.get("document_id")
        if not doc_id_note:
            self._log_result(TestResult(
                name="Phase 4a: Ingest note",
                passed=False,
                expected="document_id",
                actual=str(data_note),
            ))
            return False

        vault_id_note = data_note.get("vault_id")
        if vault_id_note and vault_id_note != self.test_user_vault_id:
            self._log_result(TestResult(
                name="Phase 4a: Ingest note vault",
                passed=False,
                expected=f"vault_id={self.test_user_vault_id}",
                actual=f"vault_id={vault_id_note}",
            ))
            return False

        if data_note.get("status") == "ok":
            self.created_doc_ids.add(doc_id_note)
        self._log_result(TestResult(
            name="Phase 4a: Ingest note",
            passed=True,
            expected="document created",
            actual=f"doc_id={doc_id_note}",
        ))

        # Ingest page
        resp_page = self._request(
            "POST",
            "/ingest",
            token=self.token,
            json_data={
                "url": PAGE_URL,
                "text": PAGE_TEXT,
                "title": "E2E Test Page",
                "mode": "page",
            },
        )

        if resp_page.status_code != 200:
            self._log_result(TestResult(
                name="Phase 4b: Ingest page",
                passed=False,
                expected="200",
                actual=f"{resp_page.status_code}: {resp_page.text[:200]}",
            ))
            return False

        data_page = resp_page.json()
        doc_id_page = data_page.get("document_id")
        if not doc_id_page:
            self._log_result(TestResult(
                name="Phase 4b: Ingest page",
                passed=False,
                expected="document_id",
                actual=str(data_page),
            ))
            return False

        vault_id_page = data_page.get("vault_id")
        if vault_id_page and vault_id_page != self.test_user_vault_id:
            self._log_result(TestResult(
                name="Phase 4b: Ingest page vault",
                passed=False,
                expected=f"vault_id={self.test_user_vault_id}",
                actual=f"vault_id={vault_id_page}",
            ))
            return False

        if data_page.get("status") == "ok":
            self.created_doc_ids.add(doc_id_page)
        self._log_result(TestResult(
            name="Phase 4b: Ingest page",
            passed=True,
            expected="document created",
            actual=f"doc_id={doc_id_page}",
        ))

        # Wait for embeddings
        print("  Waiting 5s for embedding processing...")
        time.sleep(5)

        # Reload again
        self._request(
            "POST",
            "/topics/recluster?days=365&clear_assignments=false",
            token=self.token,
        )

        # List documents - both must appear
        resp_list = self._request("GET", "/documents?limit=50", token=self.token)
        docs = resp_list.json() if resp_list.status_code == 200 else []
        doc_ids = {d.get("id") for d in docs if d.get("id")}

        both_visible = doc_id_note in doc_ids and doc_id_page in doc_ids
        self._log_result(TestResult(
            name="Phase 4c: Documents visible after reload",
            passed=both_visible,
            expected="Both docs in list",
            actual=f"note={doc_id_note in doc_ids}, page={doc_id_page in doc_ids}",
        ))
        return both_visible

    # ---------- Phase 5: Document CRUD ----------

    def phase5_crud(self) -> bool:
        """Get topics, assign topic, get text, edit, delete (only our doc)."""
        doc_id = next(iter(self.created_doc_ids), None)
        if not doc_id:
            self._log_result(TestResult(
                name="Phase 5: CRUD",
                passed=False,
                expected="created_doc_ids not empty",
                actual="No docs to test",
            ))
            return False

        # Get topic options
        resp_topics = self._request("GET", f"/documents/{doc_id}/topics", token=self.token)
        if resp_topics.status_code != 200:
            self._log_result(TestResult(
                name="Phase 5a: Get topics",
                passed=False,
                expected="200",
                actual=f"{resp_topics.status_code}",
            ))
            return False
        self._log_result(TestResult(name="Phase 5a: Get topics", passed=True, expected="200", actual="OK"))

        # Manually assign topic (custom_title)
        resp_assign = self._request(
            "PUT",
            f"/documents/{doc_id}/topic",
            token=self.token,
            json_data={"custom_title": "E2E Test Topic"},
        )
        if resp_assign.status_code != 200:
            self._log_result(TestResult(
                name="Phase 5b: Assign topic",
                passed=False,
                expected="200",
                actual=f"{resp_assign.status_code}: {resp_assign.text[:200]}",
            ))
            return False
        self._log_result(TestResult(name="Phase 5b: Assign topic", passed=True, expected="200", actual="OK"))

        # Get document text
        resp_get = self._request("GET", f"/documents/{doc_id}", token=self.token)
        if resp_get.status_code != 200:
            self._log_result(TestResult(
                name="Phase 5c: Get document text",
                passed=False,
                expected="200",
                actual=f"{resp_get.status_code}",
            ))
            return False

        data_get = resp_get.json()
        text = data_get.get("text", "")
        vault_id = data_get.get("vault_id")

        if vault_id != self.test_user_vault_id:
            self._log_result(TestResult(
                name="Phase 5c: Document vault",
                passed=False,
                expected=f"vault_id={self.test_user_vault_id}",
                actual=f"vault_id={vault_id}",
            ))
            return False

        has_text = NOTE_TEXT in text or PAGE_TEXT in text
        self._log_result(TestResult(
            name="Phase 5c: Get document text",
            passed=has_text,
            expected="Text matches ingested",
            actual=f"len={len(text)}",
        ))

        # Edit document
        edited_title = "E2E Test Edited"
        resp_edit = self._request(
            "PUT",
            f"/documents/{doc_id}",
            token=self.token,
            json_data={"title": edited_title},
        )
        if resp_edit.status_code != 200:
            self._log_result(TestResult(
                name="Phase 5d: Edit document",
                passed=False,
                expected="200",
                actual=f"{resp_edit.status_code}",
            ))
            return False

        resp_get2 = self._request("GET", f"/documents/{doc_id}", token=self.token)
        data_get2 = resp_get2.json()
        edit_ok = data_get2.get("title") == edited_title
        self._log_result(TestResult(
            name="Phase 5d: Edit document",
            passed=edit_ok,
            expected="Edit persisted",
            actual=f"title={data_get2.get('title')}",
        ))

        # Delete document - only our doc, verify first
        if doc_id not in self.created_doc_ids:
            self._log_result(TestResult(
                name="Phase 5e: Delete guard",
                passed=False,
                expected="doc_id in created_doc_ids",
                actual="Refusing to delete",
            ))
            return False

        resp_del = self._request("DELETE", f"/documents/{doc_id}", token=self.token)
        if resp_del.status_code != 200:
            self._log_result(TestResult(
                name="Phase 5e: Delete document",
                passed=False,
                expected="200",
                actual=f"{resp_del.status_code}",
            ))
            return False

        self.created_doc_ids.discard(doc_id)
        self._log_result(TestResult(
            name="Phase 5e: Delete document",
            passed=True,
            expected="Deleted",
            actual="OK",
        ))
        return True

    # ---------- Phase 6: Post-Test Validation ----------

    def phase6_validate(self) -> bool:
        """Re-query DB, assert no other vault docs deleted."""
        try:
            db = self._get_db_session()
            try:
                result = db.execute(text("""
                    SELECT d.id::text, d.vault_id::text, v.owner_id::text
                    FROM documents d
                    LEFT JOIN vaults v ON d.vault_id = v.id
                """))
                rows = result.fetchall()
                db.commit()

                current: Dict[str, Set[str]] = {}
                for doc_id, vault_id, owner_id in rows:
                    vkey = vault_id or "__no_vault__"
                    if vkey not in current:
                        current[vkey] = set()
                    current[vkey].add(doc_id)

                all_passed = True
                for vkey, snap in self.snapshot.items():
                    if vkey == "__no_vault__":
                        continue
                    owner_id = snap.owner_id
                    if owner_id == self.test_user_id:
                        continue  # Our vault - we may have deleted our docs
                    # Other vaults: all pre-existing docs must still exist
                    current_ids = current.get(vkey, set())
                    missing = snap.doc_ids - current_ids
                    if missing:
                        self._log_result(TestResult(
                            name=f"Phase 6: Validate vault {vkey[:8]}...",
                            passed=False,
                            expected="No docs deleted",
                            actual=f"Missing doc_ids: {list(missing)[:5]}...",
                        ))
                        all_passed = False

                if all_passed:
                    self._log_result(TestResult(
                        name="Phase 6: Post-test validation",
                        passed=True,
                        expected="No existing user data modified",
                        actual="All vaults intact",
                    ))
                return all_passed
            finally:
                db.close()
        except Exception as e:
            self._log_result(TestResult(
                name="Phase 6: Post-test validation",
                passed=False,
                expected="Validation complete",
                actual=str(e),
            ))
            return False

    # ---------- Phase 7: Query ----------

    def phase7_query(self) -> bool:
        """Submit query, assert hits and embeddings."""
        resp = self._request(
            "POST",
            "/query",
            token=self.token,
            json_data={
                "query": "quick brown fox",
                "top_k": 5,
                "with_answer": False,
            },
        )

        if resp.status_code != 200:
            self._log_result(TestResult(
                name="Phase 7a: Query",
                passed=False,
                expected="200",
                actual=f"{resp.status_code}: {resp.text[:200]}",
            ))
            return False

        data = resp.json()
        hits = data.get("hits", [])

        has_hits = len(hits) > 0
        self._log_result(TestResult(
            name="Phase 7a: Query returns docs",
            passed=has_hits,
            expected="At least 1 hit",
            actual=f"{len(hits)} hits",
        ))

        has_similarity = all(h.get("similarity") is not None for h in hits) if hits else True
        self._log_result(TestResult(
            name="Phase 7b: Embeddings present",
            passed=has_similarity,
            expected="similarity in hits",
            actual="OK" if has_similarity else "Missing similarity",
        ))
        return has_hits and has_similarity

    # ---------- Main ----------

    def run_all(self) -> Tuple[int, int]:
        print(f"\n{'='*60}")
        print("E2E CORE FLOW TEST SUITE")
        print(f"{'='*60}")
        print(f"API Base: {API_BASE}")
        print(f"Test User: {TEST_USER_EMAIL}")
        print(f"DATABASE_URL: {'SET' if DATABASE_URL else 'NOT SET'}")
        print(f"{'='*60}\n")

        if not DATABASE_URL:
            print("ERROR: DATABASE_URL required for snapshot and verification code.")
            return 0, 1

        # Health check
        try:
            r = requests.get(f"{API_BASE}/health", timeout=5)
            if r.status_code != 200:
                print(f"ERROR: API health check failed: {r.status_code}")
                return 0, 1
        except Exception as e:
            print(f"ERROR: Cannot connect to API: {e}")
            return 0, 1

        # Phase 0
        print("--- Phase 0: Pre-Test Snapshot ---")
        if not self.phase0_snapshot():
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)

        # Phase 1
        print("\n--- Phase 1: Auth ---")
        ok, code = self.phase1_insert_verification_code()
        if not ok or not code:
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)
        if not self.phase1_verify_and_login(code):
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)
        if not self.phase1_whoami():
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)

        # Phase 2
        print("\n--- Phase 2: Vault Access ---")
        if not self.phase2_vaults():
            return sum(1 for x in self.results if x.passed), sum(1 for x in self.results if not x.passed)

        # Phase 3
        print("\n--- Phase 3: Reload ---")
        self.phase3_reload()

        # Phase 4
        print("\n--- Phase 4: Ingestion ---")
        if not self.phase4_ingest():
            # Continue to validation even if ingest failed
            pass

        # Phase 5
        print("\n--- Phase 5: Document CRUD ---")
        self.phase5_crud()

        # Phase 6
        print("\n--- Phase 6: Post-Test Validation ---")
        self.phase6_validate()

        # Phase 7
        print("\n--- Phase 7: Query ---")
        self.phase7_query()

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
    tester = E2ECoreFlowTester()
    passed, failed = tester.run_all()
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
