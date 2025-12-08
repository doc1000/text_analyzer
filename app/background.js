// background.js

async function analyzeCurrentTab(tab) {
  try {
    const response = await browser.tabs.sendMessage(tab.id, {
      type: "collect-text"
    });

    const text = response && response.text ? response.text : "";

    if (!text || text.trim().length === 0) {
      await browser.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: "No text content found on this page."
      });
      return;
    }

    // Call your /analyzer endpoint (existing logic)
    const res = await fetch("http://localhost:8000/analyzer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    if (!res.ok) {
      const errText = await res.text();
      await browser.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: `Analyzer error: ${res.status} ${errText}`
      });
      return;
    }

    const result = await res.json();

    // Show overlay with result
    await browser.tabs.sendMessage(tab.id, {
      type: "show-result",
      result
    });
  } catch (e) {
    try {
      await browser.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: e.toString()
      });
    } catch (_) {
      // tab might not have content script loaded or can't receive messages
    }
  }
}

async function sendToBackend(payload) {
  try {
    const res = await fetch("http://localhost:8000/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!res.ok) {
      console.error("Backend ingest error:", res.status, await res.text());
    } else {
      console.log("Ingest success:", await res.json());
    }
  } catch (err) {
    console.error("Failed POST to backend:", err);
  }
}

// SINGLE click handler
browser.browserAction.onClicked.addListener(async (tab) => {
  console.log("Trust Badger clicked on tab", tab.id);

  // 1) Run analyzer flow
  await analyzeCurrentTab(tab);

  // 2) Get full capture payload for ingestion
  try {
    const response = await browser.tabs.sendMessage(tab.id, { type: "CAPTURE_PAGE" });
    console.log("Payload from content script:", response);
    await sendToBackend(response);
  } catch (err) {
    console.error("Error communicating with content script:", err);
  }
});
