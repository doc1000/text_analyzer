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

  const score = (result.score ?? 0).toFixed(1);
  const lexical = (result.lexical_density ?? 0).toFixed(3);
  const spec = (result.specificity ?? 0).toFixed(3);
  const ratio = (result.compression_ratio ?? 0).toFixed(2);

  
  overlay.textContent = `
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

function showScoreOverlay(overlay, { score, lexical, spec, ratio }) {
  overlay.replaceChildren(); // clear
  const header = makeOverlayHeader("Info Density");
  header.querySelector("strong").style.fontSize = "14px";

  const scoreRow = document.createElement("div");
  scoreRow.style.marginBottom = "6px";

  const scoreBig = document.createElement("span");
  scoreBig.style.fontSize = "24px";
  scoreBig.style.fontWeight = "bold";
  scoreBig.textContent = String(score);

  const scoreDenom = document.createElement("span");
  scoreDenom.style.fontSize = "11px";
  scoreDenom.style.opacity = "0.7";
  scoreDenom.textContent = "/100";

  scoreRow.append(scoreBig, document.createTextNode(" "), scoreDenom);

  const stats = document.createElement("div");
  stats.style.fontSize = "12px";
  stats.style.lineHeight = "1.4";

  const mkLine = (label, value) => {
    const line = document.createElement("div");
    const strong = document.createElement("strong");
    strong.textContent = label + ":";
    line.append(strong, document.createTextNode(" " + String(value)));
    return line;
  };

  stats.append(
    mkLine("Lexical density", lexical),
    mkLine("Specificity", spec),
    mkLine("Compression ratio", ratio)
  );

  const footer = document.createElement("div");
  footer.style.marginTop = "6px";
  footer.style.fontSize = "11px";
  footer.style.opacity = "0.8";
  footer.textContent = "Higher scores ≈ more specific, information-dense text.";

  overlay.append(header, scoreRow, stats, footer);
}



