// background.js

const browserAPI = typeof browser !== "undefined" ? browser : chrome;

console.log("[VB] Background script loaded");
console.log("[VB] Browser API:", typeof browserAPI !== "undefined" ? "available" : "missing");

// Get API endpoint from storage (defaults to cloud)
async function getApiBase() {
  try {
    const result = await browserAPI.storage.sync.get({ endpoint: "cloud" });
    if (result.endpoint === "local") {
      return "http://localhost:8000";
    } else {
      return "https://vaultbubbles.fly.dev";
    }
  } catch (error) {
    console.error("[VB] Error reading API endpoint setting:", error);
    return "https://vaultbubbles.fly.dev"; // Default to cloud on error
  }
}

// Get auth token from storage (extension token first, then legacy API key)
async function getApiKey() {
  try {
    // Check extension token first (new OAuth flow)
    const extResult = await browserAPI.storage.local.get({ vaultbubble_token: "" });
    if (extResult.vaultbubble_token) {
      console.log("[VB] Using extension token (OAuth)");
      return extResult.vaultbubble_token;
    }
    
    // Fall back to legacy API key
    const result = await browserAPI.storage.sync.get({ api_key: "" });
    if (result.api_key) {
      console.log("[VB] Using legacy API key");
    }
    return result.api_key || "";
  } catch (error) {
    console.error("[VB] Error reading auth token:", error);
    return "";
  }
}

// Check if extension is connected (has valid token)
async function isConnected() {
  try {
    const extResult = await browserAPI.storage.local.get({ vaultbubble_token: "" });
    if (extResult.vaultbubble_token) return true;
    
    const result = await browserAPI.storage.sync.get({ api_key: "" });
    return !!result.api_key;
  } catch (error) {
    return false;
  }
}

// Open the extension connect page
async function openConnectPage() {
  const API_BASE = await getApiBase();
  const connectUrl = `${API_BASE}/extension/connect`;
  console.log("[VB] Opening connect page:", connectUrl);
  browserAPI.tabs.create({ url: connectUrl });
}

// Disconnect (clear extension token)
async function disconnect() {
  await browserAPI.storage.local.remove("vaultbubble_token");
  console.log("[VB] Extension disconnected (token cleared)");
}

// ---------- OAuth Token Capture ----------
// Listen for successful extension connection and capture the token

browserAPI.tabs.onUpdated.addListener(async (tabId, changeInfo, tab) => {
  // Only check when URL changes and is complete
  if (changeInfo.status !== "complete" || !tab.url) return;
  
  // Get the API base to determine which success URL to look for
  const API_BASE = await getApiBase();
  const successUrlPattern = `${API_BASE}/extension/success`;
  
  if (tab.url.startsWith(successUrlPattern)) {
    console.log("[VB] Detected extension success page");
    
    try {
      const url = new URL(tab.url);
      const token = url.searchParams.get("token");
      
      if (token) {
        console.log("[VB] Captured extension token from success URL");
        
        // Store the token
        await browserAPI.storage.local.set({ vaultbubble_token: token });
        console.log("[VB] Extension token saved");
        
        // Close the tab after a brief delay to show success message
        setTimeout(() => {
          browserAPI.tabs.remove(tabId).catch(e => {
            console.log("[VB] Tab may have been closed manually:", e);
          });
        }, 1500);
      } else {
        console.warn("[VB] Success page loaded but no token in URL");
      }
    } catch (e) {
      console.error("[VB] Error capturing token:", e);
    }
  }
});

// Authenticated fetch wrapper - adds Authorization header and handles 401 errors
async function authenticatedFetch(url, options = {}) {
  const apiKey = await getApiKey();
  
  // Build headers with auth
  const headers = {
    ...options.headers,
    "Content-Type": "application/json"
  };
  
  if (apiKey) {
    headers["Authorization"] = `Bearer ${apiKey}`;
  }
  
  const response = await fetch(url, {
    ...options,
    headers
  });
  
  // Handle 401 Unauthorized
  if (response.status === 401) {
    const errorMsg = apiKey 
      ? "Authentication failed. Please check your API key in extension settings."
      : "No API key configured. Please set your API key in extension settings.";
    console.error("[VB] Authentication error:", errorMsg);
    throw new Error(errorMsg);
  }
  
  return response;
}

// ---------------- FULL PAGE ANALYZE + INGEST ---------------- //

async function analyzeAndIngest(tab) {
  console.log("[VB] analyzeAndIngest for tab", tab.id);
  console.log("[VB] Tab URL:", tab.url);
  console.log("[VB] Tab title:", tab.title);

  try {
    let pageData = null;
    let text = "";
    let pdfToParseUrl = null;
    let linkedPdfUrls = [];
    
    // Ensure we have a valid tab URL
    if (!tab.url) {
      console.error("[VB] No tab URL available!");
      return;
    }
    
    // Check if current tab is a direct PDF URL (content script might not work on PDFs)
    const isDirectPdf = tab.url.match(/\/pdf\//) || 
                       tab.url.endsWith('.pdf') || 
                       tab.url.includes('/pdf?');
    
    console.log("[VB] Is direct PDF?", isDirectPdf);
    
    // Determine if we should parse a PDF (URL is a PDF or arXiv abstract)
    if (isDirectPdf) {
      pdfToParseUrl = tab.url;
      console.log("[VB] Direct PDF - will parse:", pdfToParseUrl);
    } else if (tab.url.includes('arxiv.org/abs/')) {
      // arXiv abstract page - the PDF IS the content
      const paperId = tab.url.match(/arxiv\.org\/abs\/([^\/?#]+)/);
      if (paperId && paperId[1]) {
        pdfToParseUrl = `https://arxiv.org/pdf/${paperId[1]}.pdf`;
        console.log("[VB] arXiv abstract - will parse PDF:", pdfToParseUrl);
      }
    }
    
    // Try to get data from content script (may fail on PDF pages)
    try {
      pageData = await browserAPI.tabs.sendMessage(tab.id, {
        type: "collect-text"
      });
      
      text = pageData && pageData.text ? pageData.text : "";
      
      // Use content script's PDF detection if available
      if (pageData && pageData.pdfToParseUrl && !pdfToParseUrl) {
        pdfToParseUrl = pageData.pdfToParseUrl;
      }
      
      // Get linked PDFs (references only, not parsed)
      if (pageData && pageData.linkedPdfUrls) {
        linkedPdfUrls = pageData.linkedPdfUrls;
      }
    } catch (e) {
      // Content script might not be available (e.g., on PDF pages)
      console.log("[VB] Content script not available (may be PDF page):", e.message);
      text = "";
    }

    const url = pageData && pageData.url ? pageData.url : tab.url;
    const title = pageData && pageData.title ? pageData.title : tab.title;

    console.log("[VB] collect-text response:", {
      textLength: text.length,
      pdfToParseUrl,
      linkedPdfUrlsCount: linkedPdfUrls.length,
      url,
      title
    });

    // Allow ingestion if there's text OR a PDF to parse
    if ((!text || text.trim().length === 0) && !pdfToParseUrl) {
      try {
        await browserAPI.tabs.sendMessage(tab.id, {
          type: "show-error",
          error: "No text content found on this page."
        });
      } catch (e) {
        // Ignore if we can't send message (e.g., PDF page)
      }
      return;
    }

    // Show success overlay (may fail on PDF pages, that's OK)
    try {
      await browserAPI.tabs.sendMessage(tab.id, {
        type: "show-result",
        result: { captured: true }
      });
    } catch (e) {
      // Ignore if we can't send message (e.g., PDF page) - this is expected
      console.log("[VB] Could not show result overlay (expected on PDF pages):", e.message);
    }

    // Build ingest payload with separated PDF fields
    const ingestPayload = {
      url,
      title,
      text,
      captured_at: new Date().toISOString(),
      mode: "page",
      tags: null,
      pdf_to_parse: pdfToParseUrl,                               // Single PDF to parse (or null)
      linked_pdf_urls: linkedPdfUrls.length > 0 ? linkedPdfUrls : null  // References only
    };

    console.log("[VB] ingest payload:", ingestPayload);
    if (pdfToParseUrl) {
      console.log("[VB] PDF to parse:", pdfToParseUrl);
    }
    if (linkedPdfUrls.length > 0) {
      console.log("[VB] Linked PDFs (references):", linkedPdfUrls.length);
    }

    const API_BASE = await getApiBase();
    const ingestRes = await authenticatedFetch(`${API_BASE}/ingest`, {
      method: "POST",
      body: JSON.stringify(ingestPayload)
    });

    console.log("[VB] /ingest status:", ingestRes.status);

    if (!ingestRes.ok) {
      const errorText = await ingestRes.text();
      console.error("[VB] ingest error:", ingestRes.status, errorText);
      throw new Error(`Server error: ${ingestRes.status}`);
    } else {
      const ingestResult = await ingestRes.json().catch(() => null);
      console.log("[VB] ingest success:", ingestResult);
      console.log("[VB] PDFs parsed:", ingestResult?.pdfs_parsed || 0);
    }
  } catch (e) {
    console.error("[VB] analyzeAndIngest exception:", e);
    try {
      await browserAPI.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: e.message || e.toString()
      });
    } catch (inner) {
      console.error("[VB] Failed to send error to tab:", inner);
    }
  }
}

// Toolbar icon click = full-page capture
const actionAPI = browserAPI.action || browserAPI.browserAction;

if (actionAPI && actionAPI.onClicked) {
  actionAPI.onClicked.addListener(async (tab) => {
    console.log("[VB] Icon clicked on tab", tab.id);
    console.log("[VB] Tab URL:", tab.url);
    await analyzeAndIngest(tab);
  });
  console.log("[VB] Icon click listener registered");
} else {
  console.log("[VB] Icon click listener not available (popup may be defined)");
}


// ---------------- CONTEXT MENU: QUICK “SAVE SELECTION” ---------------- //

browserAPI.runtime.onInstalled.addListener(() => {
  try {
    browserAPI.contextMenus.create({
      id: "vb-save-selection",
      title: "Save selection to VaultBubble",
      contexts: ["selection"]
    });
  } catch (e) {
    console.warn("[VB] contextMenus.create failed (may already exist):", e);
  }
});

browserAPI.contextMenus.onClicked.addListener(async (info, tab) => {
  if (info.menuItemId !== "vb-save-selection" || !tab || !tab.id) return;

  try {
    // Ask content script in that tab for selection + page info
    const response = await browserAPI.tabs.sendMessage(tab.id, {
      type: "VB_GET_SELECTION_CONTEXT"
    });

    if (!response || !response.selectionText) {
      console.warn("[VB] No selection text to save from context menu");
      return;
    }

    const selectionText = response.selectionText.trim();
    const pageUrl = response.url || tab.url || "";
    const pageTitle = response.title || tab.title || "";

    // Build header + captured text
    const headerSnippet =
      selectionText.length > 80
        ? selectionText.slice(0, 80) + "…"
        : selectionText;

    const headerLine = `[Header] ${headerSnippet}`;
    const body = `${headerLine}\n\n[captured]\n${selectionText}`;

    const payload = {
      url: pageUrl,
      title: pageTitle,
      text: body,
      mode: "selection",
      tags: null,
      captured_at: new Date().toISOString()
    };

    console.log("[VB] Quick-capture payload:", payload);

    const API_BASE = await getApiBase();
    const ingestRes = await authenticatedFetch(`${API_BASE}/ingest`, {
      method: "POST",
      body: JSON.stringify(payload)
    });

    console.log("[VB] /ingest (quick selection) status:", ingestRes.status);
    if (!ingestRes.ok) {
      const errorText = await ingestRes.text();
      console.error("[VB] ingest error (quick selection):", ingestRes.status, errorText);
    } else {
      const ingestResult = await ingestRes.json().catch(() => null);
      console.log("[VB] ingest success (quick selection):", ingestResult);
    }
  } catch (e) {
    console.error("[VB] context menu quick-capture exception:", e);
  }
});


// ---------------- POPUP-DRIVEN NOTE + FULL PAGE CAPTURE ---------------- //

browserAPI.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log("[VB] Message received:", message?.type);
  
  if (!message || !message.type) {
    console.log("[VB] Invalid message, returning false");
    return false;
  }

  // Check connection status
  if (message.type === "VB_CHECK_CONNECTION") {
    (async () => {
      try {
        const connected = await isConnected();
        const extResult = await browserAPI.storage.local.get({ vaultbubble_token: "" });
        const hasExtToken = !!extResult.vaultbubble_token;
        const apiKeyResult = await browserAPI.storage.sync.get({ api_key: "" });
        const hasApiKey = !!apiKeyResult.api_key;
        
        sendResponse({ 
          connected, 
          hasExtToken,
          hasApiKey,
          authMethod: hasExtToken ? "oauth" : (hasApiKey ? "api_key" : "none")
        });
      } catch (err) {
        sendResponse({ connected: false, error: err.message });
      }
    })();
    return true; // async response
  }

  // Open connect page
  if (message.type === "VB_OPEN_CONNECT") {
    openConnectPage();
    sendResponse({ ok: true });
    return true;
  }

  // Disconnect (clear token)
  if (message.type === "VB_DISCONNECT") {
    (async () => {
      await disconnect();
      sendResponse({ ok: true });
    })();
    return true; // async response
  }

  // Popup-triggered note capture
  if (message.type === "VB_CAPTURE_SNIPPET") {
    console.log("[VB] Handling VB_CAPTURE_SNIPPET");
    const {
      mode,          // "note"
      url,
      pageTitle,
      headerText,
      selectionText,
      tags,
      tieToUrl
    } = message.payload || {};

    const trimmedHeader = (headerText || "").trim();
    const trimmedSelection = (selectionText || "").trim();

    let bodyParts = [];

    if (trimmedHeader) {
      bodyParts.push(`[Header] ${trimmedHeader}`);
    }

    if (trimmedSelection) {
      bodyParts.push(`[captured]\n${trimmedSelection}`);
    }

    if (bodyParts.length === 0) {
      sendResponse({ ok: false, error: "Nothing to save." });
      return true;
    }

    const combinedText = bodyParts.join("\n\n");

    // Title: header text wins, truncated to 40 chars.
    const baseTitle =
      trimmedHeader ||
      pageTitle ||
      "(untitled)";

    const truncatedTitle =
      baseTitle.length > 40
        ? baseTitle.slice(0, 40)
        : baseTitle;

    // URL: either tie to real page or synthetic note://[truncated title]
    let finalUrl;
    if (tieToUrl && url) {
      finalUrl = url;
    } else {
      finalUrl = `note://${truncatedTitle}`;
    }

    const payload = {
      url: finalUrl,
      title: truncatedTitle,
      text: combinedText,
      mode: mode || "note",
      tags: tags && tags.length ? tags : null,
      captured_at: new Date().toISOString()
    };

    console.log("[VB] Popup snippet payload:", payload);

    // Get API endpoint and send request
    (async () => {
      try {
        const API_BASE = await getApiBase();
        const response = await authenticatedFetch(`${API_BASE}/ingest`, {
          method: "POST",
          body: JSON.stringify(payload)
        });
        
        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`Server error: ${response.status} - ${errorText}`);
        }
        
        const data = await response.json().catch(() => null);
        console.log("[VB] /ingest response (popup capture):", data);
        sendResponse({ ok: true, data });
      } catch (err) {
        console.error("[VB] /ingest error (popup capture):", err);
        sendResponse({ ok: false, error: err.message || String(err) });
      }
    })();

    return true; // async response
  }

  // Popup-triggered full-page capture
  if (message.type === "VB_CAPTURE_FULL_PAGE") {
    console.log("[VB] Handling VB_CAPTURE_FULL_PAGE");
    try {
      browserAPI.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
        try {
          if (tabs && tabs[0]) {
            console.log("[VB] Popup requested full-page capture for tab", tabs[0].id);
            console.log("[VB] Tab URL:", tabs[0].url);
            await analyzeAndIngest(tabs[0]);
          } else {
            console.error("[VB] No active tab found");
          }
          sendResponse({ ok: true });
        } catch (e) {
          console.error("[VB] Error in full-page capture:", e);
          sendResponse({ ok: false, error: String(e) });
        }
      });
    } catch (e) {
      console.error("[VB] Error querying tabs:", e);
      sendResponse({ ok: false, error: String(e) });
    }

    return true; // async
  }

  console.log("[VB] Unknown message type:", message.type);
  return false;
});

console.log("[VB] Message listener registered");
