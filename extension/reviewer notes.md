# Notes for Mozilla Add-on Reviewers

## Testing Instructions

**Prerequisites:** The extension requires a connection to a vault server. Two options:

1. **Cloud (recommended for review):** Sign up or log in at [https://vaultbubbles.fly.dev](https://vaultbubbles.fly.dev). No additional download required. Use the extension Settings (gear icon) to set the server to `https://vaultbubbles.fly.dev` and enter your API key.
2. **Local:** Requires downloading and running the beta vault server software. Use only if you prefer local testing.

**Quick test flow:**
1. Install the extension and open the popup
2. Click **Connect** (or open Settings) and configure server URL + API key
3. Visit any webpage, select some text, right-click and choose the VaultBubble option—or open the popup and click **Save as a note**
4. Verify the capture appears in your vault at the configured server

---

## Permissions

| Permission | Purpose |
|------------|---------|
| `activeTab` | Access current tab URL/content only when user explicitly captures |
| `tabs` | Access tab URL/title for the "Tie to page URL" capture option |
| `contextMenus` | Right-click "Save to VaultBubble" menu |
| `scripting` | Inject content script to extract visible text for capture |
| `storage` | Store extension settings (server URL, API key) locally |
| `host_permissions: vaultbubbles.fly.dev` | Communicate with cloud vault service |
| `host_permissions: localhost:8000` | Communicate with local vault server (optional) |

---

## Data Collection & Use

**What we collect:** Only content the user explicitly captures—selected text, full page content (if requested), page URL, notes, and tags.

**Where it goes:** Data is sent only to the user's configured vault—either our cloud (vaultbubbles.fly.dev) or their local server (localhost:8000). Nothing is sent elsewhere.

**How it's used:** Data is stored under the user's account for organizing content into topics, semantic search, and AI-assisted querying within the vault. We do not sell, share, or use data for advertising or marketing. Data is not used for AI training beyond the processing required to provide the vault features.

---

## Third-Party AI Services

The vault backend (separate from the extension) uses third-party AI services (e.g., HuggingFace, OpenAI) for embeddings, topic labeling, and chat. These providers are asked not to use submitted data for training or other secondary purposes. The extension itself does not call AI APIs directly; it only sends captures to the user's configured vault server.

---

## Data Collection Permissions (Manifest)

The manifest declares `data_collection_permissions` as required: `websiteContent`, `browsingActivity` (for capturing page content and URLs when the user saves) and optional: `technicalAndInteraction` (for basic interaction logging). This aligns with the permissions we request.

---

## Test Account

**Login:** reviewer@mozilla.com / 123456  
Vault interface: [https://vaultbubbles.fly.dev](https://vaultbubbles.fly.dev)
