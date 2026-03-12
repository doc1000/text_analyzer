"""
Shared pytest configuration and fixtures for VaultBubbles tests.

The patch below prevents an eager DB connection during test collection.
When app.main is imported, the line:
    from .db import get_db, EMBED_TABLE, SENTENCE_TABLE, TOPIC_TABLE, DOCUMENT_TABLE
triggers app/db.py's __getattr__, which calls initialize_embedding_tables(),
which opens a real DB session. Patching that function with a mock tuple lets
tests that do not need a database (e.g. test_health, test_ingestion_client)
run without a running Postgres instance.

Tests that require a real database (e.g. test_vault_security.py) must be run
against a running Docker environment; they are unaffected by this patch.

Add project-wide fixtures here as new test phases are implemented.
"""
from unittest.mock import MagicMock, patch

# (embed_dim, embed_model, EMBED_TABLE, SENTENCE_TABLE, TOPIC_TABLE, DOCUMENT_TABLE)
_mock_embed_tuple = (384, "test-model", MagicMock(), MagicMock(), MagicMock(), MagicMock())

# Start the patch at module level so it is active before any test file is
# imported (pytest loads conftest.py before collecting tests).
_db_patcher = patch("app.db.initialize_embedding_tables", return_value=_mock_embed_tuple)
_db_patcher.start()
