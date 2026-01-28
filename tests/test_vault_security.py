#!/usr/bin/env python3
"""
Vault Security Test Script

Tests that vault access controls properly prevent unauthorized access.
Creates two test users and verifies that one cannot access the other's data.

Prerequisites:
- Local Docker environment running (docker-compose up)
- VB_BOOTSTRAP_TOKEN environment variable set

Usage:
    export VB_BOOTSTRAP_TOKEN="your-token-here"
    python tests/test_vault_security.py
"""

import os
import sys
import json
import time
import requests
from dataclasses import dataclass
from typing import Optional, List, Tuple

# Configuration
API_BASE = os.getenv("API_BASE", "http://localhost:8000")
BOOTSTRAP_TOKEN = os.getenv("VB_BOOTSTRAP_TOKEN")

# Test users
TESTUSER_EMAIL = "testuser@example.com"
WRONGUSER_EMAIL = "wronguser@example.com"

# Test document content
TEST_DOCUMENT_URL = "https://example.com/test-document"
TEST_DOCUMENT_TEXT = """
This is a test document for vault security testing.
It contains some sample text that will be used to verify
that unauthorized users cannot access documents in other vaults.
The quick brown fox jumps over the lazy dog.
"""


@dataclass
class TestUser:
    email: str
    api_key: Optional[str] = None
    user_id: Optional[str] = None
    vault_id: Optional[str] = None
    document_id: Optional[str] = None


@dataclass
class TestResult:
    name: str
    passed: bool
    expected: str
    actual: str
    details: str = ""


class VaultSecurityTester:
    def __init__(self):
        self.testuser = TestUser(email=TESTUSER_EMAIL)
        self.wronguser = TestUser(email=WRONGUSER_EMAIL)
        self.results: List[TestResult] = []
        
    def _headers(self, api_key: Optional[str] = None, bootstrap: bool = False) -> dict:
        """Build request headers."""
        headers = {"Content-Type": "application/json"}
        if bootstrap and BOOTSTRAP_TOKEN:
            headers["X-Bootstrap-Token"] = BOOTSTRAP_TOKEN
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return headers
    
    def _log_result(self, result: TestResult):
        """Log and store test result."""
        self.results.append(result)
        status = "PASS" if result.passed else "FAIL"
        print(f"  [{status}] {result.name}")
        if not result.passed:
            print(f"         Expected: {result.expected}")
            print(f"         Actual: {result.actual}")
            if result.details:
                print(f"         Details: {result.details}")
    
    def _request(self, method: str, path: str, api_key: Optional[str] = None, 
                 bootstrap: bool = False, json_data: dict = None) -> requests.Response:
        """Make an API request."""
        url = f"{API_BASE}{path}"
        headers = self._headers(api_key, bootstrap)
        
        if method.upper() == "GET":
            return requests.get(url, headers=headers, timeout=30)
        elif method.upper() == "POST":
            return requests.post(url, headers=headers, json=json_data, timeout=30)
        elif method.upper() == "PUT":
            return requests.put(url, headers=headers, json=json_data, timeout=30)
        elif method.upper() == "DELETE":
            return requests.delete(url, headers=headers, timeout=30)
        else:
            raise ValueError(f"Unknown method: {method}")

    # ============ SETUP TESTS ============
    
    def test_01_setup_testuser(self) -> bool:
        """Create API key for testuser."""
        resp = self._request("POST", "/auth/bootstrap/create-key", 
                            bootstrap=True, 
                            json_data={"email": TESTUSER_EMAIL, "name": "test-key"})
        
        if resp.status_code == 200:
            data = resp.json()
            self.testuser.api_key = data.get("api_key")
            self.testuser.user_id = data.get("user_id")
            passed = bool(self.testuser.api_key)
        else:
            passed = False
            
        self._log_result(TestResult(
            name="Setup testuser",
            passed=passed,
            expected="200 with API key",
            actual=f"{resp.status_code}: {resp.text[:100] if not passed else 'OK'}"
        ))
        return passed
    
    def test_02_setup_wronguser(self) -> bool:
        """Create API key for wronguser."""
        resp = self._request("POST", "/auth/bootstrap/create-key",
                            bootstrap=True,
                            json_data={"email": WRONGUSER_EMAIL, "name": "test-key"})
        
        if resp.status_code == 200:
            data = resp.json()
            self.wronguser.api_key = data.get("api_key")
            self.wronguser.user_id = data.get("user_id")
            passed = bool(self.wronguser.api_key)
        else:
            passed = False
            
        self._log_result(TestResult(
            name="Setup wronguser",
            passed=passed,
            expected="200 with API key",
            actual=f"{resp.status_code}: {resp.text[:100] if not passed else 'OK'}"
        ))
        return passed

    def test_03_testuser_ingest_document(self) -> bool:
        """testuser ingests a document into their vault."""
        resp = self._request("POST", "/ingest",
                            api_key=self.testuser.api_key,
                            json_data={
                                "url": TEST_DOCUMENT_URL,
                                "text": TEST_DOCUMENT_TEXT,
                                "title": "Test Security Document"
                            })
        
        if resp.status_code == 200:
            data = resp.json()
            self.testuser.document_id = data.get("document_id")
            self.testuser.vault_id = data.get("vault_id")
            passed = bool(self.testuser.document_id)
        else:
            passed = False
            
        self._log_result(TestResult(
            name="testuser ingests document",
            passed=passed,
            expected="200 with document_id",
            actual=f"{resp.status_code}: {resp.text[:100] if not passed else 'OK'}",
            details=f"doc_id={self.testuser.document_id}, vault_id={self.testuser.vault_id}"
        ))
        return passed

    # ============ DOCUMENT ACCESS TESTS ============
    
    def test_04_testuser_lists_documents(self) -> bool:
        """testuser can see their own documents."""
        resp = self._request("GET", "/documents", api_key=self.testuser.api_key)
        
        if resp.status_code == 200:
            data = resp.json()
            # Should see at least their document
            doc_ids = [d.get("id") for d in data]
            passed = self.testuser.document_id in doc_ids
        else:
            passed = False
            data = []
            
        self._log_result(TestResult(
            name="testuser lists documents",
            passed=passed,
            expected=f"200 with document {self.testuser.document_id}",
            actual=f"{resp.status_code}: found {len(data)} docs",
            details=f"Looking for {self.testuser.document_id}"
        ))
        return passed
    
    def test_05_wronguser_lists_documents(self) -> bool:
        """wronguser should NOT see testuser's documents."""
        resp = self._request("GET", "/documents", api_key=self.wronguser.api_key)
        
        if resp.status_code == 200:
            data = resp.json()
            # Should NOT see testuser's document
            doc_ids = [d.get("id") for d in data]
            passed = self.testuser.document_id not in doc_ids
        else:
            passed = False
            data = []
            
        self._log_result(TestResult(
            name="wronguser lists documents (should not see testuser's)",
            passed=passed,
            expected="200 with empty list or no testuser docs",
            actual=f"{resp.status_code}: found {len(data)} docs",
            details=f"testuser doc {self.testuser.document_id} visible: {self.testuser.document_id in doc_ids}" if resp.status_code == 200 else ""
        ))
        return passed
    
    def test_06_wronguser_gets_testuser_document(self) -> bool:
        """wronguser should NOT be able to get testuser's document."""
        resp = self._request("GET", f"/documents/{self.testuser.document_id}", 
                            api_key=self.wronguser.api_key)
        
        # Expect 403 Forbidden
        passed = resp.status_code == 403
            
        self._log_result(TestResult(
            name="wronguser gets testuser document",
            passed=passed,
            expected="403 Forbidden",
            actual=f"{resp.status_code}: {resp.text[:100]}"
        ))
        return passed
    
    def test_07_wronguser_edits_testuser_document(self) -> bool:
        """wronguser should NOT be able to edit testuser's document."""
        resp = self._request("PUT", f"/documents/{self.testuser.document_id}",
                            api_key=self.wronguser.api_key,
                            json_data={"title": "HACKED!", "text": "HACKED!"})
        
        # Expect 403 Forbidden
        passed = resp.status_code == 403
            
        self._log_result(TestResult(
            name="wronguser edits testuser document",
            passed=passed,
            expected="403 Forbidden",
            actual=f"{resp.status_code}: {resp.text[:100]}"
        ))
        return passed
    
    def test_08_wronguser_deletes_testuser_document(self) -> bool:
        """wronguser should NOT be able to delete testuser's document."""
        resp = self._request("DELETE", f"/documents/{self.testuser.document_id}",
                            api_key=self.wronguser.api_key)
        
        # Expect 403 Forbidden
        passed = resp.status_code == 403
            
        self._log_result(TestResult(
            name="wronguser deletes testuser document",
            passed=passed,
            expected="403 Forbidden",
            actual=f"{resp.status_code}: {resp.text[:100]}"
        ))
        return passed

    # ============ QUERY TESTS ============
    
    def test_09_wronguser_query(self) -> bool:
        """wronguser's query should NOT return testuser's documents."""
        resp = self._request("POST", "/query",
                            api_key=self.wronguser.api_key,
                            json_data={
                                "query": "quick brown fox lazy dog",
                                "top_k": 10,
                                "with_answer": False
                            })
        
        if resp.status_code == 200:
            data = resp.json()
            hits = data.get("hits", [])
            # Check if any hits are from testuser's document
            testuser_doc_in_results = any(
                h.get("document_id") == self.testuser.document_id 
                for h in hits
            )
            passed = not testuser_doc_in_results
        else:
            passed = False
            hits = []
            
        self._log_result(TestResult(
            name="wronguser query (should not find testuser docs)",
            passed=passed,
            expected="200 with no testuser documents in results",
            actual=f"{resp.status_code}: {len(hits)} hits",
            details=f"testuser doc found in results: {not passed}" if resp.status_code == 200 else ""
        ))
        return passed

    # ============ TOPICS TESTS ============
    
    def test_10_wronguser_gets_topics_hierarchy(self) -> bool:
        """wronguser's topics hierarchy should NOT include testuser's topics."""
        resp = self._request("GET", "/topics/hierarchy?days=365",
                            api_key=self.wronguser.api_key)
        
        if resp.status_code == 200:
            data = resp.json()
            # Recursively search for testuser's document in the hierarchy
            def find_doc_in_hierarchy(node, doc_id):
                if node.get("document_id") == doc_id:
                    return True
                for child in node.get("children", []):
                    if find_doc_in_hierarchy(child, doc_id):
                        return True
                return False
            
            testuser_doc_found = find_doc_in_hierarchy(data, self.testuser.document_id)
            passed = not testuser_doc_found
        else:
            passed = False
            testuser_doc_found = False
            
        self._log_result(TestResult(
            name="wronguser gets topics hierarchy",
            passed=passed,
            expected="200 with no testuser documents in hierarchy",
            actual=f"{resp.status_code}",
            details=f"testuser doc found in hierarchy: {testuser_doc_found}" if resp.status_code == 200 else ""
        ))
        return passed
    
    def test_11_wronguser_gets_document_topics(self) -> bool:
        """wronguser should NOT be able to get topics for testuser's document."""
        resp = self._request("GET", f"/documents/{self.testuser.document_id}/topics",
                            api_key=self.wronguser.api_key)
        
        # Expect 403 Forbidden
        passed = resp.status_code == 403
            
        self._log_result(TestResult(
            name="wronguser gets document topics",
            passed=passed,
            expected="403 Forbidden",
            actual=f"{resp.status_code}: {resp.text[:100]}"
        ))
        return passed
    
    def test_12_wronguser_assigns_topic(self) -> bool:
        """wronguser should NOT be able to assign topic to testuser's document."""
        # First, we need a topic ID - use a fake one for the test
        resp = self._request("PUT", f"/documents/{self.testuser.document_id}/topic",
                            api_key=self.wronguser.api_key,
                            json_data={"topic_id": "00000000-0000-0000-0000-000000000000"})
        
        # Expect 403 Forbidden
        passed = resp.status_code == 403
            
        self._log_result(TestResult(
            name="wronguser assigns topic to testuser document",
            passed=passed,
            expected="403 Forbidden",
            actual=f"{resp.status_code}: {resp.text[:100]}"
        ))
        return passed

    # ============ VAULT TESTS ============
    
    def test_13_testuser_lists_vaults(self) -> bool:
        """testuser can list their vaults."""
        resp = self._request("GET", "/vaults", api_key=self.testuser.api_key)
        
        if resp.status_code == 200:
            data = resp.json()
            vaults = data.get("vaults", [])
            # Should have at least one vault (personal)
            passed = len(vaults) >= 1
            # Store vault_id if not already set
            if passed and not self.testuser.vault_id:
                self.testuser.vault_id = vaults[0].get("id")
        else:
            passed = False
            vaults = []
            
        self._log_result(TestResult(
            name="testuser lists vaults",
            passed=passed,
            expected="200 with at least 1 vault",
            actual=f"{resp.status_code}: {len(vaults)} vaults"
        ))
        return passed
    
    def test_14_wronguser_lists_vaults(self) -> bool:
        """wronguser lists only their own vaults."""
        resp = self._request("GET", "/vaults", api_key=self.wronguser.api_key)
        
        if resp.status_code == 200:
            data = resp.json()
            vaults = data.get("vaults", [])
            # Should NOT see testuser's vault
            vault_ids = [v.get("id") for v in vaults]
            testuser_vault_visible = self.testuser.vault_id in vault_ids
            passed = not testuser_vault_visible
            # Store wronguser's vault_id
            if vaults:
                self.wronguser.vault_id = vaults[0].get("id")
        else:
            passed = False
            vaults = []
            testuser_vault_visible = False
            
        self._log_result(TestResult(
            name="wronguser lists vaults (should not see testuser's)",
            passed=passed,
            expected="200 with only wronguser's vaults",
            actual=f"{resp.status_code}: {len(vaults)} vaults",
            details=f"testuser vault visible: {testuser_vault_visible}" if resp.status_code == 200 else ""
        ))
        return passed
    
    def test_15_wronguser_gets_testuser_vault(self) -> bool:
        """wronguser should NOT be able to get testuser's vault details."""
        if not self.testuser.vault_id:
            self._log_result(TestResult(
                name="wronguser gets testuser vault",
                passed=False,
                expected="403 Forbidden",
                actual="SKIP: testuser vault_id not available"
            ))
            return False
            
        resp = self._request("GET", f"/vaults/{self.testuser.vault_id}",
                            api_key=self.wronguser.api_key)
        
        # Expect 403 Forbidden
        passed = resp.status_code == 403
            
        self._log_result(TestResult(
            name="wronguser gets testuser vault",
            passed=passed,
            expected="403 Forbidden",
            actual=f"{resp.status_code}: {resp.text[:100]}"
        ))
        return passed

    # ============ CLEANUP ============
    
    def cleanup(self):
        """Clean up test data (optional - run manually if needed)."""
        print("\n--- Cleanup ---")
        
        # Delete testuser's document
        if self.testuser.document_id and self.testuser.api_key:
            resp = self._request("DELETE", f"/documents/{self.testuser.document_id}",
                                api_key=self.testuser.api_key)
            print(f"  Delete testuser document: {resp.status_code}")
    
    # ============ MAIN ============
    
    def run_all_tests(self) -> Tuple[int, int]:
        """Run all tests and return (passed, failed) counts."""
        print(f"\n{'='*60}")
        print("VAULT SECURITY TEST SUITE")
        print(f"{'='*60}")
        print(f"API Base: {API_BASE}")
        print(f"Bootstrap Token: {'SET' if BOOTSTRAP_TOKEN else 'NOT SET'}")
        print(f"testuser: {TESTUSER_EMAIL}")
        print(f"wronguser: {WRONGUSER_EMAIL}")
        print(f"{'='*60}\n")
        
        if not BOOTSTRAP_TOKEN:
            print("ERROR: VB_BOOTSTRAP_TOKEN environment variable not set!")
            print("Please set it and try again:")
            print("  export VB_BOOTSTRAP_TOKEN='your-token-here'")
            return 0, 1
        
        # Check API is available
        try:
            resp = requests.get(f"{API_BASE}/health", timeout=5)
            if resp.status_code != 200:
                print(f"ERROR: API health check failed: {resp.status_code}")
                return 0, 1
        except Exception as e:
            print(f"ERROR: Cannot connect to API at {API_BASE}: {e}")
            return 0, 1
        
        print("--- Setup Tests ---")
        setup_ok = self.test_01_setup_testuser() and self.test_02_setup_wronguser()
        
        if not setup_ok:
            print("\nERROR: Setup failed. Cannot continue.")
            return sum(1 for r in self.results if r.passed), sum(1 for r in self.results if not r.passed)
        
        # Ingest test document
        if not self.test_03_testuser_ingest_document():
            print("\nERROR: Document ingestion failed. Cannot continue.")
            return sum(1 for r in self.results if r.passed), sum(1 for r in self.results if not r.passed)
        
        # Wait a moment for document to be processed
        print("\n  Waiting for document processing...")
        time.sleep(2)
        
        print("\n--- Document Access Tests ---")
        self.test_04_testuser_lists_documents()
        self.test_05_wronguser_lists_documents()
        self.test_06_wronguser_gets_testuser_document()
        self.test_07_wronguser_edits_testuser_document()
        self.test_08_wronguser_deletes_testuser_document()
        
        print("\n--- Query Tests ---")
        self.test_09_wronguser_query()
        
        print("\n--- Topics Tests ---")
        self.test_10_wronguser_gets_topics_hierarchy()
        self.test_11_wronguser_gets_document_topics()
        self.test_12_wronguser_assigns_topic()
        
        print("\n--- Vault Tests ---")
        self.test_13_testuser_lists_vaults()
        self.test_14_wronguser_lists_vaults()
        self.test_15_wronguser_gets_testuser_vault()
        
        # Summary
        passed = sum(1 for r in self.results if r.passed)
        failed = sum(1 for r in self.results if not r.passed)
        
        print(f"\n{'='*60}")
        print(f"SUMMARY: {passed} passed, {failed} failed")
        print(f"{'='*60}")
        
        if failed > 0:
            print("\nFAILED TESTS (potential security issues):")
            for r in self.results:
                if not r.passed:
                    print(f"  - {r.name}")
        
        return passed, failed


def main():
    tester = VaultSecurityTester()
    passed, failed = tester.run_all_tests()
    
    # Ask about cleanup
    if tester.testuser.document_id:
        print(f"\nTest document created: {tester.testuser.document_id}")
        if "--cleanup" in sys.argv:
            tester.cleanup()
        else:
            print("Run with --cleanup to delete test data")
    
    # Exit with error code if any tests failed
    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
