// background.js

const browserAPI = typeof browser !== "undefined" ? browser : chrome;

console.log("[VB] Background script loaded");

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

  try {
    let pageData = null;
    let pdfUrls = [];
    let text = "";
    
    // Check if current tab is a direct PDF URL (content script might not work on PDFs)
    const isDirectPdf = tab.url.match(/\/pdf\//) || 
                       tab.url.endsWith('.pdf') || 
                       tab.url.includes('/pdf?');
    
    // Special handling for arxiv.org
    if (tab.url.includes('arxiv.org/abs/')) {
      // Extract PDF URL from abstract page
      const paperId = tab.url.match(/arxiv\.org\/abs\/([^\/?#]+)/);
      if (paperId && paperId[1]) {
        pdfUrls.push(`https://arxiv.org/pdf/${paperId[1]}.pdf`);
        console.log("[VB] Extracted arxiv PDF URL from abstract page:", pdfUrls[0]);
      }
    } else if (isDirectPdf) {
      // Direct PDF page - add current URL as PDF to parse
      pdfUrls.push(tab.url);
      console.log("[VB] Detected direct PDF URL:", tab.url);
    }
    
    // 1) Inject content script if needed (activeTab permission)
    try {
      await browserAPI.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content-script.js"]
      });
    } catch (e) {
      // Content script may already be injected, or tab may not be accessible (e.g., PDF pages)
      console.log("[VB] Content script injection note:", e.message);
    }

    // 2) Try to get data from content script (may fail on PDF pages)
    try {
      pageData = await browserAPI.tabs.sendMessage(tab.id, {
        type: "collect-text"
      });
      
      text = pageData && pageData.text ? pageData.text : "";
      // Merge PDF URLs from content script with those detected from URL
      if (pageData && pageData.pdfUrls && pageData.pdfUrls.length > 0) {
        pdfUrls = [...new Set([...pdfUrls, ...pageData.pdfUrls])];
      }
    } catch (e) {
      // Content script might not be available (e.g., on PDF pages)
      console.log("[VB] Content script not available (may be PDF page):", e.message);
      // Use tab URL and title as fallback
      text = "";
    }

    const url = pageData && pageData.url ? pageData.url : tab.url;
    const title = pageData && pageData.title ? pageData.title : tab.title;

    console.log("[VB] collect-text response:", {
      textLength: text.length,
      pdfUrlsCount: pdfUrls.length,
      url,
      title,
      isDirectPdf: isDirectPdf
    });

    // Allow ingestion if there's text OR PDFs to parse
    if ((!text || text.trim().length === 0) && pdfUrls.length === 0) {
      try {
        await browserAPI.tabs.sendMessage(tab.id, {
          type: "show-error",
          error: "No text content or PDFs found on this page."
        });
      } catch (e) {
        // Ignore if we can't send message (e.g., PDF page)
      }
      return;
    }

    // Show success overlay
    await browserAPI.tabs.sendMessage(tab.id, {
      type: "show-result",
      result: { captured: true }
    });

    // Build ingest payload
    const ingestPayload = {
      url,
      title,
      text,
      captured_at: new Date().toISOString(),
      mode: "page",
      tags: null,
      pdf_urls: pdfUrls.length > 0 ? pdfUrls : null
    };

    console.log("[VB] ingest payload:", ingestPayload);

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

actionAPI.onClicked.addListener(async (tab) => {
  console.log("[VB] Icon clicked on tab", tab.id);
  await analyzeAndIngest(tab);
});


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
    // Inject content script if needed (activeTab permission)
    try {
      await browserAPI.scripting.executeScript({
        target: { tabId: tab.id },
        files: ["content-script.js"]
      });
    } catch (e) {
      console.log("[VB] Content script injection note:", e.message);
    }

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
  if (!message || !message.type) return false;

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

  // Popup requesting selection from active tab
  if (message.type === "VB_POPUP_GET_SELECTION") {
    const tabId = message.tabId;
    
    (async () => {
      try {
        // Inject content script if needed (activeTab permission)
        try {
          await browserAPI.scripting.executeScript({
            target: { tabId },
            files: ["content-script.js"]
          });
          // Small delay to ensure content script is ready
          await new Promise(resolve => setTimeout(resolve, 100));
        } catch (e) {
          console.log("[VB] Content script injection note:", e.message);
        }

        // Get selection from content script
        const response = await browserAPI.tabs.sendMessage(tabId, {
          type: "VB_GET_SELECTION_CONTEXT"
        });
        
        sendResponse(response || {});
      } catch (e) {
        console.error("[VB] Error getting selection for popup:", e);
        sendResponse({});
      }
    })();
    
    return true; // async response
  }

  // Popup-triggered note capture
  if (message.type === "VB_CAPTURE_SNIPPET") {
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
    browserAPI.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
      if (tabs && tabs[0]) {
        console.log("[VB] Popup requested full-page capture for tab", tabs[0].id);
        await analyzeAndIngest(tabs[0]);
      }
      sendResponse({ ok: true });
    });

    return true; // async
  }

  return false;
});
