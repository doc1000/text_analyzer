// Simple function to extract text; you may already have a better version.
function extractPageText() {
  // You might be using Readability or some cleaner HTML extraction already.
  return document.body.innerText || "";
}

// Listen for message from background asking for data
browser.runtime.onMessage.addListener((message, sender) => {
  if (message.type === "CAPTURE_PAGE") {

    const text = extractPageText();

    // TODO: replace with your scoring logic
    const infoScore = window.infoScore ?? null;
    const aiScore = window.aiScore ?? null;

    return Promise.resolve({
      url: window.location.href,
      title: document.title,
      text,
      score_info: infoScore,
      score_ai_slop: aiScore,
      captured_at: new Date().toISOString()
    });
  }
});
