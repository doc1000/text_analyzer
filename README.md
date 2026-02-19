# VaultBubbles

A personal knowledge vault that captures, organizes, and queries your web content using AI-powered topic clustering.

## What is VaultBubbles?

VaultBubbles helps you build a searchable, organized knowledge base from web pages you read. It:

- **Captures** content from any webpage via the Chrome extension
- **Clusters** documents into topics automatically using embeddings
- **Visualizes** your knowledge as an interactive bubble map
- **Queries** your vault with natural language questions

---

## Quick Start (Cloud)

### 1. Install the Chrome Extension

1. Download the extension from the Chrome Web Store (or load unpacked from `extension-chrome/`)
2. Click the VaultBubbles icon in your browser toolbar
3. The extension is ready to capture pages

### 2. Get Your API Key

1. Contact the VaultBubbles administrator to receive your API key
2. Your key starts with `vb_live_...`

### 3. Configure the Extension

1. Right-click the VaultBubbles extension icon → **Options**
2. Select **Cloud** as your API endpoint
3. Your settings are saved automatically

### 4. Configure the Website

1. Go to [https://vaultbubbles.fly.dev](https://vaultbubbles.fly.dev)
2. Click the **⚙️ Settings** button
3. Under "VaultBubble API Key", paste your `vb_live_...` key
4. Click **Save Key** - the page will reload

### 5. Start Capturing

1. Navigate to any webpage you want to save
2. Click the VaultBubbles extension icon
3. Click **Capture Page** (or use the context menu: right-click → "Save to VaultBubbles")
4. The content is sent to your vault

---

## Using the Website

### Topics Map

The main view shows your documents organized as an interactive bubble visualization:

- **Large bubbles** = Topic categories (Level 2)
- **Medium bubbles** = Sub-topics (Level 1)  
- **Small bubbles** = Individual documents (Level 0)

**Navigation:**
- Click a topic bubble to zoom in
- Click a document bubble to open it in the document viewer
- Use the **Days** input to filter by time range
- Click **Reload** to refresh and re-cluster uncategorized documents

### Document Viewer

When you click a document bubble, you're taken to the document detail page:

- **Edit** the title and content
- **Assign topics** manually or let AI generate one
- **Delete** documents you no longer need
- **Export** to clipboard or file

### Settings

Access settings via the **⚙️** button:

| Setting | Description |
|---------|-------------|
| **VaultBubble API Key** | Your authentication key (starts with `vb_live_...`) |
| **Chat Provider** | AI for answering questions (OpenAI recommended) |
| **Topic Provider** | AI for generating topic titles (OpenAI recommended) |
| **Embedding Provider** | Vector embeddings (HuggingFace default) |
| **OpenAI API Key** | Required if using OpenAI for chat/topics |

---

## Querying Your Vault

### Scoped Queries

1. Click on any topic or sub-topic bubble to zoom in
2. The **"Ask within this scope"** panel appears
3. Type your question and press **Ctrl+Enter** or click **Ask**
4. The AI searches only documents within that topic

### Example Queries

- "What are the main points about machine learning?"
- "Summarize the articles about climate change"
- "What did I read about Python async programming?"

---

## Chrome Extension Features

### Capture Methods

1. **Popup**: Click extension icon → **Capture Page**
2. **Context Menu**: Right-click anywhere → **Save to VaultBubbles**
3. **Selection**: Select text → Right-click → **Save Selection to VaultBubbles**

### Extension Settings

Right-click the extension icon → **Options**:

- **Cloud**: Use `https://vaultbubbles.fly.dev` (default)
- **Local**: Use `http://localhost:8000` (for self-hosted)

---

## Local Development Setup

### Prerequisites

- Docker and Docker Compose
- (Optional) Ollama for local AI models

### Running Locally

```bash
# Clone the repository
git clone https://github.com/your-repo/text_analyzer.git
cd text_analyzer

# Copy and configure environment
cp .env.example .env
# Edit .env with your API keys

# Start services
docker compose up -d

# Access the site
open http://localhost:8000
```

### Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OPENAI_API_KEY` | OpenAI API key for chat/topics | - |
| `HUGGINGFACE_API_TOKEN` | HuggingFace token for embeddings | - |
| `HUGGINGFACE_EMBED_URL` | Dedicated HF endpoint URL | - |
| `EMBEDDING_PROVIDER` | `ollama`, `huggingface`, or `openai` | `huggingface` |
| `CHAT_PROVIDER` | `ollama` or `openai` | `openai` |
| `TOPIC_PROVIDER` | `ollama` or `openai` | `openai` |
| `VB_REQUIRE_AUTH` | Enable API key authentication | `true` |
| `VB_BOOTSTRAP_TOKEN` | Secret for creating API keys | - |
| `VB_API_KEY_PEPPER` | Salt for hashing API keys | - |

### Creating API Keys (Admin)

```bash
curl -X POST "http://localhost:8000/auth/bootstrap/create-key" \
  -H "Content-Type: application/json" \
  -H "X-Bootstrap-Token: YOUR_BOOTSTRAP_TOKEN" \
  -d '{"email": "user@example.com", "name": "User Name"}'
```

Response:
```json
{
  "api_key": "vb_live_xxx...",
  "user_id": "uuid",
  "email": "user@example.com"
}
```

---

## API Reference

### Authentication

All API requests (except `/health` and `/static/*`) require a Bearer token:

```
Authorization: Bearer vb_live_your_key_here
```

### Key Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | Health check (no auth required) |
| `POST` | `/ingest` | Ingest a new document |
| `GET` | `/documents` | List documents |
| `GET` | `/documents/{id}` | Get document details |
| `PUT` | `/documents/{id}` | Update document |
| `DELETE` | `/documents/{id}` | Delete document |
| `POST` | `/query` | Query your vault |
| `GET` | `/topics/hierarchy` | Get topic tree for visualization |
| `POST` | `/topics/recluster` | Re-cluster uncategorized documents |
| `GET` | `/auth/whoami` | Get current user info |
| `GET` | `/auth/api-keys` | List your API keys |

### Swagger Documentation

Full API docs available at:
- Cloud: `https://vaultbubbles.fly.dev/docs`
- Local: `http://localhost:8000/docs`

---

## Architecture

```
┌─────────────────┐     ┌─────────────────┐
│ Chrome Extension│────▶│   FastAPI       │
└─────────────────┘     │   Backend       │
                        │                 │
┌─────────────────┐     │  - Ingest       │     ┌─────────────────┐
│  Web Interface  │────▶│  - Query        │────▶│   PostgreSQL    │
│  (D3.js)        │     │  - Topics       │     │   + pgvector    │
└─────────────────┘     │  - Auth         │     └─────────────────┘
                        └────────┬────────┘
                                 │
                    ┌────────────┴────────────┐
                    ▼                         ▼
            ┌─────────────┐           ┌─────────────┐
            │ HuggingFace │           │   OpenAI    │
            │ (Embeddings)│           │ (Chat/Topics)│
            └─────────────┘           └─────────────┘
```

---

## E2E Test Script (Pre-Merge Validation)

Run the end-to-end test script to validate core behavior before merges:

```bash
export API_BASE=http://localhost:8000
export DATABASE_URL=postgresql://badger:badgerpass@localhost:5433/badgerdb
python scripts/run_e2e_tests.py
```

For remote (e.g. Fly.io), set `API_BASE` and `DATABASE_URL` to your deployment. The script:

- Uses extension login flow (DB-inserted verification code)
- Tests auth, vaults, ingest, document CRUD, topics, query
- Takes pre/post DB snapshots and validates no existing user data is modified or deleted

Requires: `psycopg2-binary` (in `app/requirements.txt`).

---

## Troubleshooting

### "Unauthorized" errors

1. Make sure you've entered your API key in Settings
2. Check that the key starts with `vb_live_`
3. Try clearing your key and re-entering it

### Documents not appearing in topics

1. Click **Reload** to trigger re-clustering
2. New documents need embeddings (may take a few seconds)
3. Check the **Days** filter isn't excluding your documents

### Extension not connecting

1. Right-click extension icon → Options
2. Verify the correct endpoint is selected (Cloud vs Local)
3. Check browser console for errors

---

## Support

For issues or questions, contact the VaultBubbles administrator or open an issue on GitHub.

---

*Built with FastAPI, PostgreSQL, pgvector, D3.js, and lots of ☕*
