# Getting Started with VaultBubbles

Welcome to VaultBubbles! This guide will help you set up and start using your personal knowledge vault.

## What is VaultBubbles?

VaultBubbles is a personal knowledge management system that:
- Captures web pages you read via a Chrome extension
- Automatically organizes content into topics using AI
- Lets you search and query your saved knowledge with natural language
- Visualizes your knowledge as an interactive bubble map

## Setup (5 minutes)

### Step 1: Get Your API Key

Your API key authenticates you with VaultBubbles. It looks like this:
```
vb_live_abc123xyz...
```

If you don't have one yet, contact your VaultBubbles administrator.

### Step 2: Install the Chrome Extension

1. Open Chrome and go to `chrome://extensions`
2. Enable "Developer mode" (toggle in top-right)
3. Click "Load unpacked" and select the `extension-chrome` folder
4. The VaultBubbles icon appears in your toolbar

### Step 3: Configure the Extension

1. Right-click the VaultBubbles icon
2. Click "Options"
3. Select "Cloud" to use the hosted service
4. Done! Settings save automatically

### Step 4: Configure the Website

1. Go to https://vaultbubbles.fly.dev
2. Click the ⚙️ Settings button (top-right)
3. Find "VaultBubble API Key"
4. Paste your `vb_live_...` key
5. Click "Save Key"
6. The page reloads - you're authenticated!

## Capturing Content

### From the Extension Popup
1. Navigate to any webpage
2. Click the VaultBubbles icon
3. Click "Capture Page"

### From the Context Menu
1. Right-click anywhere on a page
2. Select "Save to VaultBubbles"

### Saving Selected Text
1. Highlight text on any page
2. Right-click the selection
3. Choose "Save Selection to VaultBubbles"

## Using the Topics Map

The main page shows your knowledge as nested bubbles:

**Bubble Sizes:**
- 🔵 Large = Categories (broad topics)
- 🔵 Medium = Sub-topics (specific themes)
- 🔵 Small = Individual documents

**How to Navigate:**
- Click a large bubble to zoom into that topic
- Click a small bubble to open the document
- Click outside bubbles to zoom back out
- Use "Days" filter to show recent content only

## Querying Your Vault

### Scoped Questions
1. Click on any topic bubble to zoom in
2. The "Ask within this scope" panel appears at the bottom
3. Type a question like "What are the key points here?"
4. Press Ctrl+Enter or click "Ask"
5. AI searches only documents in that topic

### Question Examples
- "Summarize what I've saved about machine learning"
- "What were the main arguments in these articles?"
- "Find mentions of specific names or dates"
- "What patterns do you see across these documents?"

## Managing Documents

### Opening a Document
Click any small bubble (document) to open the viewer.

### Editing
- Change the title by editing the text at the top
- Modify content in the text area
- Click "Save" to keep changes

### Assigning Topics
- Click "Show Topics" to expand topic options
- Select from existing topics, or
- Click "Generate with AI" for a suggestion
- Click "Accept" or "Reject" the suggestion

### Deleting
Click "Delete" and confirm to permanently remove a document.

## Settings Reference

Access via ⚙️ button:

| Setting | What it does |
|---------|--------------|
| VaultBubble API Key | Your authentication (vb_live_...) |
| Chat Provider | AI for answering questions |
| Topic Provider | AI for generating topic names |
| Embedding Provider | How documents are compared |
| OpenAI API Key | Required for OpenAI features |

**Recommended defaults:**
- Chat: OpenAI
- Topics: OpenAI  
- Embeddings: HuggingFace

## Tips for Best Results

### Capture Quality Content
- Save articles, not homepages
- Longer content clusters better than short snippets
- Capture related content to build topic clusters

### Review Periodically
- Click "Reload" to re-cluster new documents
- Check for orphan documents (single-item topics)
- Merge similar topics by re-assigning documents

### Ask Good Questions
- Be specific: "What did X say about Y?" 
- Ask for summaries: "Summarize the main points"
- Compare: "What are the different views on X?"

## Troubleshooting

**"Unauthorized" error?**
→ Re-enter your API key in Settings

**Document not showing in topics?**
→ Click "Reload" to trigger re-clustering

**Extension not working?**
→ Check Options → make sure "Cloud" is selected

**Page looks empty?**
→ Check the "Days" filter (try 365 for everything)

---

Happy knowledge building! 🧠✨
