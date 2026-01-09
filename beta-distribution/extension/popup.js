// popup.js

const browserAPI = typeof browser !== "undefined" ? browser : chrome;

const headerInput = document.getElementById("vb-header");
const bodyInput = document.getElementById("vb-body");
const tagsInput = document.getElementById("vb-tags");
const tieUrlCheckbox = document.getElementById("vb-tie-url");
const btnSave = document.getElementById("btn-save");
const btnFullPage = document.getElementById("btn-fullpage");
const statusEl = document.getElementById("status");

let currentPageUrl = "";
let currentPageTitle = "";

// On load, grab selection from active tab (but DO NOT prefill header)
document.addEventListener("DOMContentLoaded", () => {
  browserAPI.tabs.query({ active: true, currentWindow: true }, async (tabs) => {
    if (!tabs || !tabs[0]) return;

    const tabId = tabs[0].id;

    // Ask background to inject content script and get selection
    browserAPI.runtime.sendMessage(
      { type: "VB_POPUP_GET_SELECTION", tabId },
      (response) => {
        if (browserAPI.runtime.lastError) {
          console.warn("[VB popup] Error getting selection:", browserAPI.runtime.lastError);
          return;
        }
        if (!response) return;

        const selectionText = response.selectionText || "";
        currentPageUrl = response.url || "";
        currentPageTitle = response.title || "";

        if (selectionText.trim()) {
          bodyInput.value = selectionText.trim();
        }
        // headerInput intentionally left blank by default
      }
    );
  });
});

function parseTags(raw) {
  if (!raw) return [];
  return raw
    .split(",")
    .map((t) => t.trim())
    .filter(Boolean);
}

function setStatus(msg, isError = false) {
  statusEl.textContent = msg;
  statusEl.style.color = isError ? "#f97373" : "#9ca3af";
}

function sendNoteCapture() {
  const headerText = headerInput.value || "";
  const selectionText = bodyInput.value || "";
  const tags = parseTags(tagsInput.value);
  const tieToUrl = !!tieUrlCheckbox.checked;

  if (!headerText.trim() && !selectionText.trim()) {
    setStatus("Nothing to save. Add a header or some text.", true);
    return;
  }

  browserAPI.runtime.sendMessage(
    {
      type: "VB_CAPTURE_SNIPPET",
      payload: {
        mode: "note",
        url: tieToUrl ? currentPageUrl : "",
        pageTitle: currentPageTitle,
        headerText,
        selectionText,
        tags,
        tieToUrl
      }
    },
    (response) => {
      if (browserAPI.runtime.lastError) {
        console.warn("[VB popup] Capture error:", browserAPI.runtime.lastError);
        setStatus("Error sending capture.", true);
        return;
      }
      if (!response || !response.ok) {
        setStatus("Capture failed.", true);
      } else {
        setStatus("Saved to vault ✔");
      }
    }
  );
}

function sendFullPageCapture() {
  setStatus("Capturing full page…");
  browserAPI.runtime.sendMessage(
    { type: "VB_CAPTURE_FULL_PAGE" },
    (response) => {
      if (browserAPI.runtime.lastError) {
        console.warn("[VB popup] Full-page capture error:", browserAPI.runtime.lastError);
        setStatus("Error capturing page.", true);
        return;
      }
      setStatus("Full page capture triggered ✔");
    }
  );
}

btnSave.addEventListener("click", () => {
  sendNoteCapture();
});

btnFullPage.addEventListener("click", () => {
  sendFullPageCapture();
});
