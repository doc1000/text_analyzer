// background.js

async function analyzeAndIngest(tab) {
  console.log("[TB] analyzeAndIngest for tab", tab.id);

  try {
    // 1) Ask content script for page text (and optionally title/url)
    const pageData = await browser.tabs.sendMessage(tab.id, {
      type: "collect-text"
    });

    const text = pageData && pageData.text ? pageData.text : "";
    const url = pageData && pageData.url ? pageData.url : tab.url;
    const title = pageData && pageData.title ? pageData.title : tab.title;

    console.log("[TB] collect-text response:", { textLength: text.length, url, title });

    if (!text || text.trim().length === 0) {
      await browser.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: "No text content found on this page."
      });
      return;
    }

    // 2) Call /analyzer with the text
    const analyzerRes = await fetch("http://localhost:8000/analyzer", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    console.log("[TB] /analyzer status:", analyzerRes.status);

    if (!analyzerRes.ok) {
      const errText = await analyzerRes.text();
      console.error("[TB] Analyzer error:", analyzerRes.status, errText);
      await browser.tabs.sendMessage(tab.id, {
        type: "show-error",
        error: `Analyzer error: ${analyzerRes.status} ${errText}`
      });
      return;
    }

    const analyzerResult = await analyzerRes.json();
    console.log("[TB] Analyzer result:", analyzerResult);

    // 3) Show the pretty overlay using analyzerResult
    await browser.tabs.sendMessage(tab.id, {
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
      captured_at: new Date().toISOString()
    };

    console.log("[TB] ingest payload:", ingestPayload);

    const ingestRes = await fetch("http://localhost:8000/ingest", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(ingestPayload)
    });

    console.log("[TB] /ingest status:", ingestRes.status);

    if (!ingestRes.ok) {
      console.error("[TB] ingest error:", ingestRes.status, await ingestRes.text());
    } else {
      const ingestResult = await ingestRes.json();
      console.log("[TB] ingest success:", ingestResult);
    }

  } catch (e) {
    console.error("[TB] analyzeAndIngest exception:", e);
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

// SINGLE click handler
browser.browserAction.onClicked.addListener(async (tab) => {
  console.log("[TB] Icon clicked on tab", tab.id);
  await analyzeAndIngest(tab);
});
