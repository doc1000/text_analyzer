VaultBubbles Responsibilities
- persistence
- ingest queue
- async processing
- embeddings
- topic modeling
- retrieval
- permissions

vbub-doc-ingestion Responsibilities
- file parsing
- document normalization
- text extraction
- format detection
- CanonicalDocument generation

Communication
VaultBubbles → vbub-doc-ingestion via HTTP API

Return Contract
CanonicalDocument JSON only