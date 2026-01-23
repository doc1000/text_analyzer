// settings.js - VaultBubble extension settings page logic

const browserAPI = typeof browser !== "undefined" ? browser : chrome;

const saveBtn = document.getElementById("saveBtn");
const statusEl = document.getElementById("status");
const radioOptions = document.querySelectorAll(".radio-option");
const radioInputs = document.querySelectorAll('input[name="endpoint"]');

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
  });
});

// Save button click
saveBtn.addEventListener("click", saveSettings);

// Load settings on page load
loadSettings();
