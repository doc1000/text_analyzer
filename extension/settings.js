// settings.js - VaultBubble extension settings page logic

const browserAPI = typeof browser !== "undefined" ? browser : chrome;

// Endpoint settings elements
const saveBtn = document.getElementById("saveBtn");
const statusEl = document.getElementById("status");
const radioOptions = document.querySelectorAll(".radio-option");
const radioInputs = document.querySelectorAll('input[name="endpoint"]');

// API key elements
const apiKeyInput = document.getElementById("apiKeyInput");
const toggleKeyBtn = document.getElementById("toggleKeyBtn");
const saveKeyBtn = document.getElementById("saveKeyBtn");
const clearKeyBtn = document.getElementById("clearKeyBtn");
const keyStatus = document.getElementById("keyStatus");
const keyStatusIcon = document.getElementById("keyStatusIcon");
const keyStatusText = document.getElementById("keyStatusText");
const webAppLink = document.getElementById("webAppLink");

// -------- API KEY MANAGEMENT --------

// Load API key status (don't show actual key, just hint)
async function loadApiKey() {
  try {
    const result = await browserAPI.storage.sync.get({ api_key: "" });
    const hasKey = result.api_key && result.api_key.length > 0;
    
    if (hasKey) {
      // Show masked hint (first 10 chars + last 4 chars)
      const key = result.api_key;
      const hint = key.length > 14 
        ? key.substring(0, 10) + "..." + key.substring(key.length - 4)
        : "********";
      
      keyStatus.className = "key-status set";
      keyStatusIcon.textContent = "✓";
      keyStatusText.textContent = `API key set: ${hint}`;
      apiKeyInput.placeholder = "Enter new key to replace...";
    } else {
      keyStatus.className = "key-status not-set";
      keyStatusIcon.textContent = "⚠️";
      keyStatusText.textContent = "No API key set";
      apiKeyInput.placeholder = "vb_live_...";
    }
    
    console.log("[VB Settings] API key loaded, hasKey:", hasKey);
  } catch (error) {
    console.error("[VB Settings] Error loading API key:", error);
  }
}

// Save API key
async function saveApiKey() {
  const key = apiKeyInput.value.trim();
  
  if (!key) {
    showStatus("Please enter an API key", "error");
    return;
  }
  
  if (!key.startsWith("vb_live_")) {
    showStatus("API key should start with 'vb_live_'", "error");
    return;
  }
  
  try {
    await browserAPI.storage.sync.set({ api_key: key });
    console.log("[VB Settings] API key saved");
    showStatus("✓ API key saved successfully!", "success");
    
    // Clear input and reload status
    apiKeyInput.value = "";
    apiKeyInput.type = "password";
    toggleKeyBtn.textContent = "Show";
    await loadApiKey();
    
    // Auto-hide success message
    setTimeout(() => {
      statusEl.style.display = "none";
    }, 2000);
  } catch (error) {
    console.error("[VB Settings] Error saving API key:", error);
    showStatus("Error saving API key. Please try again.", "error");
  }
}

// Clear API key
async function clearApiKey() {
  if (!confirm("Are you sure you want to clear your API key? You'll need to re-enter it to use VaultBubble.")) {
    return;
  }
  
  try {
    await browserAPI.storage.sync.remove("api_key");
    console.log("[VB Settings] API key cleared");
    showStatus("API key cleared", "success");
    
    apiKeyInput.value = "";
    await loadApiKey();
    
    setTimeout(() => {
      statusEl.style.display = "none";
    }, 2000);
  } catch (error) {
    console.error("[VB Settings] Error clearing API key:", error);
    showStatus("Error clearing API key.", "error");
  }
}

// Toggle show/hide API key input
function toggleKeyVisibility() {
  if (apiKeyInput.type === "password") {
    apiKeyInput.type = "text";
    toggleKeyBtn.textContent = "Hide";
  } else {
    apiKeyInput.type = "password";
    toggleKeyBtn.textContent = "Show";
  }
}

// Update web app link based on endpoint
function updateWebAppLink() {
  const selectedEndpoint = document.querySelector('input[name="endpoint"]:checked').value;
  if (selectedEndpoint === "local") {
    webAppLink.href = "http://localhost:8000/static/index.html";
  } else {
    webAppLink.href = "https://vaultbubbles.fly.dev/static/index.html";
  }
}

// -------- ENDPOINT SETTINGS --------

// Load current settings
async function loadSettings() {
  try {
    const result = await browserAPI.storage.sync.get({ endpoint: "cloud" });
    const selectedValue = result.endpoint;
    
    // Update radio selection
    radioInputs.forEach(input => {
      if (input.value === selectedValue) {
        input.checked = true;
        input.parentElement.classList.add("selected");
      } else {
        input.checked = false;
        input.parentElement.classList.remove("selected");
      }
    });
    
    // Update web app link
    updateWebAppLink();
    
    console.log("[VB Settings] Loaded:", result);
  } catch (error) {
    console.error("[VB Settings] Error loading settings:", error);
    showStatus("Error loading settings", "error");
  }
}

// Save settings
async function saveSettings() {
  const selectedEndpoint = document.querySelector('input[name="endpoint"]:checked').value;
  
  try {
    await browserAPI.storage.sync.set({ endpoint: selectedEndpoint });
    console.log("[VB Settings] Saved endpoint:", selectedEndpoint);
    showStatus("✓ Settings saved successfully!", "success");
    
    // Auto-hide success message after 2 seconds
    setTimeout(() => {
      statusEl.style.display = "none";
    }, 2000);
  } catch (error) {
    console.error("[VB Settings] Error saving settings:", error);
    showStatus("Error saving settings. Please try again.", "error");
  }
}

// Show status message
function showStatus(message, type) {
  statusEl.textContent = message;
  statusEl.className = `status ${type}`;
  statusEl.style.display = "block";
}

// Handle radio option clicks (for better UX)
radioOptions.forEach(option => {
  option.addEventListener("click", () => {
    const input = option.querySelector('input[type="radio"]');
    input.checked = true;
    
    // Update visual selection
    radioOptions.forEach(opt => opt.classList.remove("selected"));
    option.classList.add("selected");
    
    // Update web app link when endpoint changes
    updateWebAppLink();
  });
});

// Endpoint save button click
saveBtn.addEventListener("click", saveSettings);

// API key button clicks
saveKeyBtn.addEventListener("click", saveApiKey);
clearKeyBtn.addEventListener("click", clearApiKey);
toggleKeyBtn.addEventListener("click", toggleKeyVisibility);

// Allow Enter key to save API key
apiKeyInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    saveApiKey();
  }
});

// Load all settings on page load
loadSettings();
loadApiKey();
