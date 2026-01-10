function extractPageText() {
  return document.body ? (document.body.innerText || "") : "";
}

// Handle analyzer-related messages + overlay
browser.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log("[VB CS] message received:", message);
  
  if (message && message.type === "VB_GET_SELECTION_CONTEXT") {
      const selection = window.getSelection();
      const selectionText = selection ? selection.toString() : "";

      sendResponse({
        selectionText,
        url: window.location.href,
        title: document.title || ""
      });

      // indicate we will respond synchronously
      return true;
  }
  if (message.type === "collect-text") {
    const text = extractPageText();
    console.log("[VB CS] collect-text returning", text.length, "chars");

    sendResponse({
      text,
      url: window.location.href,
      title: document.title
    });
    return true;
  }

  if (message.type === "show-result") {
    console.log("[VB CS] show-result:", message.result);
    //showResultOverlay(message.result);  //this was hanging, not as useful with popup console
    // alert("Analyzer result:\n\n" + JSON.stringify(message.result, null, 2));
    return false;
  }

  if (message.type === "show-error") {
    console.log("[VB CS] show-error:", message.error);
    showErrorOverlay(message.error);
    // alert("Analyzer error:\n\n" + message.error);
  }

  if (message.type === "show-loading") {
    console.log("[VB CS] show-loading:", message.message);
    // TODO: your overlay code here
  }

  // allow other listeners to run
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

  overlay.innerHTML = `
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">
      <strong style="font-size:14px;">VaultBubble</strong>
      <button id="info-density-close"
              style="background:none;border:none;color:#fff;font-size:14px;cursor:pointer;">✕</button>
    </div>
    <div style="margin-bottom:6px;">
      <span style="font-size:18px;font-weight:bold;">✓ Captured</span>
    </div>
    <div style="font-size:12px;opacity:0.8;">
      Page content saved to VaultBubble.
    </div>
  `;

  document.body.appendChild(overlay);

  const btn = document.getElementById("info-density-close");
  if (btn) {
    btn.addEventListener("click", () => overlay.remove());
  }
  
  // Auto-hide after 3 seconds
  setTimeout(() => {
    if (overlay.parentNode) overlay.remove();
  }, 3000);
}

function makeOverlayHeader(titleText) {
  const header = document.createElement("div");
  header.style.display = "flex";
  header.style.justifyContent = "space-between";
  header.style.alignItems = "center";
  header.style.marginBottom = "4px";

  const title = document.createElement("strong");
  title.textContent = titleText;
  header.appendChild(title);

  const btn = document.createElement("button");
  btn.id = "info-density-close";
  btn.type = "button";
  btn.textContent = "✕";
  btn.style.background = "none";
  btn.style.border = "none";
  btn.style.color = "#fff";
  btn.style.fontSize = "14px";
  btn.style.cursor = "pointer";
  header.appendChild(btn);

  return header;
}

function showErrorOverlay(overlay, errorText) {
  overlay.replaceChildren(); // clear
  const header = makeOverlayHeader("Error");
  header.style.marginBottom = "2px";

  const body = document.createElement("div");
  body.textContent = String(errorText ?? "");

  overlay.append(header, body);
}




