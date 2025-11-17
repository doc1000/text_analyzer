// content-script.js

// Listen for messages from the background script
browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "collect-text") {
    // Get the visible text of the page
    const text = document.body ? document.body.innerText || "" : "";
    sendResponse({ text });
    // Returning true is only needed for async sendResponse, here it's sync
    return true;
  }

  if (message.type === "show-result") {
    showResultOverlay(message.result);
  }

  if (message.type === "show-error") {
    showErrorOverlay(message.error);
  }

  // No async response
  return false;
});

// Create / update an overlay to show the results
function showResultOverlay(result) {
  const existing = document.getElementById("info-density-overlay");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "info-density-overlay";
  overlay.style.position = "fixed";
  overlay.style.top = "10px";
  overlay.style.right = "10px";
  overlay.style.zIndex = "999999";
  overlay.style.background = "rgba(0, 0, 0, 0.85)";
  overlay.style.color = "#fff";
  overlay.style.padding = "12px 16px";
  overlay.style.borderRadius = "8px";
  overlay.style.fontFamily = "system-ui, sans-serif";
  overlay.style.fontSize = "13px";
  overlay.style.maxWidth = "260px";
  overlay.style.boxShadow = "0 4px 10px rgba(0,0,0,0.4)";

  const score = (result.score ?? 0).toFixed(1);
  const lexical = (result.lexical_density ?? 0).toFixed(3);
  const spec = (result.specificity ?? 0).toFixed(3);
  const ratio = (result.compression_ratio ?? 0).toFixed(2);

  overlay.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
      <strong style="font-size:14px;">Info Density</strong>
      <button id="info-density-close"
              style="background:none;border:none;color:#fff;font-size:14px;cursor:pointer;">✕</button>
    </div>
    <div style="margin-bottom:6px;">
      <span style="font-size:24px;font-weight:bold;">${score}</span>
      <span style="font-size:11px;opacity:0.7;">/100</span>
    </div>
    <div style="font-size:12px;line-height:1.4;">
      <div><strong>Lexical density:</strong> ${lexical}</div>
      <div><strong>Specificity:</strong> ${spec}</div>
      <div><strong>Compression ratio:</strong> ${ratio}</div>
    </div>
    <div style="margin-top:6px;font-size:11px;opacity:0.8;">
      Higher scores ≈ more specific, information-dense text.
    </div>
  `;

  document.body.appendChild(overlay);

  const btn = document.getElementById("info-density-close");
  if (btn) {
    btn.addEventListener("click", () => overlay.remove());
  }
}

function showErrorOverlay(errorText) {
  const existing = document.getElementById("info-density-overlay");
  if (existing) existing.remove();

  const overlay = document.createElement("div");
  overlay.id = "info-density-overlay";
  overlay.style.position = "fixed";
  overlay.style.top = "10px";
  overlay.style.right = "10px";
  overlay.style.zIndex = "999999";
  overlay.style.background = "rgba(128, 0, 0, 0.9)";
  overlay.style.color = "#fff";
  overlay.style.padding = "10px 14px";
  overlay.style.borderRadius = "8px";
  overlay.style.fontFamily = "system-ui, sans-serif";
  overlay.style.fontSize = "12px";
  overlay.style.maxWidth = "260px";
  overlay.style.boxShadow = "0 4px 10px rgba(0,0,0,0.4)";

  overlay.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px;">
      <strong>Error</strong>
      <button id="info-density-close"
              style="background:none;border:none;color:#fff;font-size:14px;cursor:pointer;">✕</button>
    </div>
    <div>${errorText}</div>
  `;

  document.body.appendChild(overlay);

  const btn = document.getElementById("info-density-close");
  if (btn) {
    btn.addEventListener("click", () => overlay.remove());
  }
}
