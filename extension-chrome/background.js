// background.js

const browserAPI = typeof browser !== "undefined" ? browser : chrome;
const API_BASE = "http://localhost:8000"; // adjust if needed

// ---------------- FULL PAGE ANALYZE + INGEST ---------------- //

async function analyzeAndIngest(tab) {
  console.log("[VB] analyzeAndIngest for tab", tab.id);

  try {
    // 1) Ask content script for page text (and optionally title/url)
    const pageData = await browserAPI.tabs.sendMessage(tab.id, {
      type: "collect-text"
    });

    const text = pageData && pageData.text ? pageData.text : "";
    const url = pageData && pageData.url ? pageData.url : tab.url;
    const title = pageData && pageData.title ? pageData.title : tab.title;

    console.log("[VB] collect-text response:", {
      textLength: text.length,
      url,
      title
    });

    if (!text || text.trim().length === 0) {
      await browserAPI.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: "No text content found on this page."
      });
      return;
    }

    // 2) Call /analyzer with the text
    const analyzerRes = await fetch(`${API_BASE}/analyzer`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    console.log("[VB] /analyzer status:", analyzerRes.status);

    if (!analyzerRes.ok) {
      const errText = await analyzerRes.text();
      console.error("[VB] Analyzer error:", analyzerRes.status, errText);
      await browserAPI.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: `Analyzer error: ${analyzerRes.status} ${errText}`
      });
      return;
    }

    const analyzerResult = await analyzerRes.json();
    console.log("[VB] Analyzer result:", analyzerResult);

    // 3) Show the pretty overlay using analyzerResult
    await browserAPI.tabs.sendMessage(tab.id, {
      type: "show-result",
      result: analyzerResult
    });

    // 4) Build ingest payload that INCLUDES the scores from analyzer
    const ingestPayload = {
      url,
      title,
      text,
      score_info: analyzerResult.infoScore,
      score_ai_slop: analyzerResult.aiScore,
      captured_at: new Date().toISOString(),
      mode: "page",
      tags: null
    };

    console.log("[VB] ingest payload:", ingestPayload);

    const ingestRes = await fetch(`${API_BASE}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ingestPayload)
    });

    console.log("[VB] /ingest status:", ingestRes.status);

    if (!ingestRes.ok) {
      console.error(
        "[VB] ingest error:",
        ingestRes.status,
        await ingestRes.text()
      );
    } else {
      const ingestResult = await ingestRes.json().catch(() => null);
      console.log("[VB] ingest success:", ingestResult);
    }
  } catch (e) {
    console.error("[VB] analyzeAndIngest exception:", e);
    try {
      await browserAPI.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: e.toString()
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
      score_info: null,
      score_ai_slop: null,
      captured_at: new Date().toISOString()
    };

    console.log("[VB] Quick-capture payload:", payload);

    const ingestRes = await fetch(`${API_BASE}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    console.log("[VB] /ingest (quick selection) status:", ingestRes.status);
    if (!ingestRes.ok) {
      console.error(
        "[VB] ingest error (quick selection):",
        ingestRes.status,
        await ingestRes.text()
      );
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
      score_info: null,
      score_ai_slop: null,
      captured_at: new Date().toISOString()
    };

    console.log("[VB] Popup snippet payload:", payload);

    fetch(`${API_BASE}/ingest`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    })
      .then((r) => r.json().catch(() => null))
      .then((data) => {
        console.log("[VB] /ingest response (popup capture):", data);
        sendResponse({ ok: true, data });
      })
      .catch((err) => {
        console.error("[VB] /ingest error (popup capture):", err);
        sendResponse({ ok: false, error: String(err) });
      });

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
