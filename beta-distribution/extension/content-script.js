// VaultBubble content script (Firefox + Chrome compatible)
const browserAPI = typeof browser !== "undefined" ? browser : chrome;

console.log("[VB] content script loaded", location.href);

function extractPageText() {
  let text = document.body ? (document.body.innerText || "") : "";
  
  // Detect PDF iframes/embeds and extract their URLs
  const pdfUrls = [];
  const currentUrl = window.location.href;
  
  // Check if current page is a direct PDF URL (common on arxiv.org, etc.)
  // Pattern: /pdf/ or ends with .pdf or content-type is application/pdf
  const isDirectPdf = currentUrl.match(/\/pdf\//) || 
                      currentUrl.endsWith('.pdf') || 
                      currentUrl.includes('/pdf?') ||
                      document.contentType === 'application/pdf';
  
  if (isDirectPdf) {
    // Current page IS a PDF - add it to PDF URLs
    pdfUrls.push(currentUrl);
    console.log("[VB CS] Detected direct PDF URL:", currentUrl);
  }
  
  // Special handling for arxiv.org abstract pages - extract PDF URL
  if (currentUrl.includes('arxiv.org/abs/')) {
    // Convert abstract URL to PDF URL
    // e.g., https://arxiv.org/abs/2512.13564 -> https://arxiv.org/pdf/2512.13564.pdf
    const paperId = currentUrl.match(/arxiv\.org\/abs\/([^\/?#]+)/);
    if (paperId && paperId[1]) {
      const pdfUrl = `https://arxiv.org/pdf/${paperId[1]}.pdf`;
      pdfUrls.push(pdfUrl);
      console.log("[VB CS] Extracted arxiv PDF URL:", pdfUrl);
    }
  }
  
  // Check for PDF iframes
  const pdfIframes = document.querySelectorAll('iframe[src*=".pdf"], iframe[src*="/pdf"], iframe[src*="application/pdf"]');
  pdfIframes.forEach(iframe => {
    const src = iframe.src || iframe.getAttribute('data-src') || iframe.getAttribute('data-url');
    if (src) {
      // Resolve relative URLs
      try {
        const absoluteUrl = new URL(src, window.location.href).href;
        if (absoluteUrl.includes('.pdf') || absoluteUrl.includes('/pdf') || absoluteUrl.includes('application/pdf')) {
          pdfUrls.push(absoluteUrl);
        }
      } catch (e) {
        // Invalid URL, skip
      }
    }
  });
  
  // Check for embed tags
  const pdfEmbeds = document.querySelectorAll('embed[type="application/pdf"], object[type="application/pdf"]');
  pdfEmbeds.forEach(embed => {
    const src = embed.src || embed.getAttribute('data') || embed.getAttribute('data-src');
    if (src) {
      try {
        const absoluteUrl = new URL(src, window.location.href).href;
        pdfUrls.push(absoluteUrl);
      } catch (e) {
        // Invalid URL, skip
      }
    }
  });
  
  // Check for links to PDFs (common on academic sites)
  const pdfLinks = document.querySelectorAll('a[href$=".pdf"], a[href*=".pdf?"], a[href*="/pdf"]');
  pdfLinks.forEach(link => {
    const href = link.href;
    if (href && !pdfUrls.includes(href)) {
      try {
        const absoluteUrl = new URL(href, window.location.href).href;
        if (absoluteUrl.includes('.pdf')) {
          pdfUrls.push(absoluteUrl);
        }
      } catch (e) {
        // Invalid URL, skip
      }
    }
  });
  
  // For arxiv abstract pages, also look for "Download PDF" links
  if (currentUrl.includes('arxiv.org/abs/')) {
    const downloadLinks = document.querySelectorAll('a[href*="/pdf/"], a[href*=".pdf"]');
    downloadLinks.forEach(link => {
      const href = link.href || link.getAttribute('href');
      if (href && href.includes('/pdf/') && !pdfUrls.includes(href)) {
        try {
          const absoluteUrl = new URL(href, window.location.href).href;
          pdfUrls.push(absoluteUrl);
        } catch (e) {
          // Invalid URL, skip
        }
      }
    });
  }
  
  // Remove duplicates
  const uniquePdfUrls = [...new Set(pdfUrls)];
  
  return {
    text: text,
    pdfUrls: uniquePdfUrls
  };
}

// Handle analyzer-related messages + overlay
browserAPI.runtime.onMessage.addListener((message, sender, sendResponse) => {
  console.log("[VB CS] message received:", message);
  
  if (message && message.type === "VB_GET_SELECTION_CONTEXT") {
      let selectionText = "";
      
      // Try to get selection from window.getSelection() (works for HTML pages)
      const selection = window.getSelection();
      if (selection && selection.toString().trim()) {
        selectionText = selection.toString();
      } else {
        // For PDF pages or when selection doesn't work, try alternative methods
        // Check if we're on a PDF page
        const isPdf = window.location.href.match(/\/pdf\//) || 
                     window.location.href.endsWith('.pdf') ||
                     document.contentType === 'application/pdf';
        
        if (isPdf) {
          // On PDF pages, selection might not work via getSelection()
          // Try to get selected text from clipboard or document
          // Note: This is limited by browser security, but we can try
          try {
            // For PDFs, we'll need to rely on the PDF URL being captured
            // Selection from PDF viewer is very limited due to browser security
            selectionText = "";
            console.log("[VB CS] PDF page detected - selection capture limited");
          } catch (e) {
            console.log("[VB CS] Could not get selection from PDF:", e);
          }
        }
      }

      sendResponse({
        selectionText,
        url: window.location.href,
        title: document.title || ""
      });

      // indicate we will respond synchronously
      return true;
  }
  if (message.type === "collect-text") {
    const result = extractPageText();
    const text = result.text || "";
    const pdfUrls = result.pdfUrls || [];
    console.log("[VB CS] collect-text returning", text.length, "chars", pdfUrls.length, "PDFs");

    sendResponse({
      text,
      pdfUrls: pdfUrls,
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




