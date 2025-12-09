async function analyzeCurrentTab(tab) {
  console.log("[TB] analyzeCurrentTab called for tab", tab.id);

  try {
    // 1) Ask content script for text
    const response = await browser.tabs.sendMessage(tab.id, {
      type: "collect-text"
    });

    console.log("[TB] collect-text response:", response);

    const text = response && response.text ? response.text : "";

    if (!text || text.trim().length === 0) {
      console.warn("[TB] No text returned from collect-text");
      await browser.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: "No text content found on this page."
      });
      return;
    }

    // 2) POST to /analyzer – NOTE: endpoint name and payload shape
    const res = await fetch("http://localhost:8000/analyzer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    console.log("[TB] /analyzer status:", res.status);

    if (!res.ok) {
      const errText = await res.text();
      console.error("[TB] Analyzer error:", res.status, errText);
      await browser.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: `Analyzer error: ${res.status} ${errText}`
      });
      return;
    }

    const result = await res.json();
    console.log("[TB] Analyzer result:", result);

    await browser.tabs.sendMessage(tab.id, {
      type: "show-result",
      result
    });
  } catch (e) {
    console.error("[TB] analyzeCurrentTab exception:", e);
    try {
      await browser.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: e.toString()
      });
    } catch (inner) {
      console.error("[TB] Failed to send error to tab:", inner);
    }
  }
}

async function sendToBackend(payload) {
  console.log("[TB] sendToBackend payload:", payload);
  try {
    const res = await fetch("http://localhost:8000/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    console.log("[TB] /ingest status:", res.status);

    if (!res.ok) {
      console.error("[TB] Backend ingest error:", res.status, await res.text());
    } else {
      console.log("[TB] Ingest success:", await res.json());
    }
  } catch (err) {
    console.error("[TB] Failed POST to backend:", err);
  }
}

// Single click handler: analyze + ingest
browser.browserAction.onClicked.addListener(async (tab) => {
  console.log("[TB] Icon clicked on tab", tab.id);

  // 1) Analyzer flow
  await analyzeCurrentTab(tab);

  // 2) Ingest flow
  try {
    const response = await browser.tabs.sendMessage(tab.id, { type: "CAPTURE_PAGE" });
    console.log("[TB] CAPTURE_PAGE response:", response);
    await sendToBackend(response);
  } catch (err) {
    console.error("[TB] Error during CAPTURE_PAGE:", err);
  }
});
