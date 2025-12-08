// background.js

async function analyzeCurrentTab(tab) {
  try {
    // Ask content script for the page text
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

    // Send text to local FastAPI analyzer
    const res = await fetch("http://localhost:8000/analyze", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text })
    });

    if (!res.ok) {
      throw new Error("Analyzer HTTP error: " + res.status);
    }

    const result = await res.json();

    // Send the result back to the content script to display
    await browser.tabs.sendMessage(tab.id, {
      type: "show-result",
      result
    });
  } catch (e) {
    console.error("Error analyzing page:", e);
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

browser.browserAction.onClicked.addListener(analyzeCurrentTab);
