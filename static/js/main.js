import { el, autoGrow } from "./dom.js";
import { loadSidebar, createSession, createFolder, openSidebar, closeSidebar } from "./sidebar.js";
import { enableRootDrop, enableComposerDrop } from "./dragdrop.js";
import { sendMessage, loadOlderMessages } from "./chat.js";
import { openModels, pullManual, switchTab, checkHealth, loadModels, cancelDownload, refreshCatalog } from "./models.js";
import { loadSettings, saveSettings, switchSettingsTab } from "./settings.js";
import { openMemory, toggleMemory, clearMemories } from "./memory.js";
import { closeDocumentPanel, copyActiveDocument, downloadActiveDocument, uploadFiles, showDocumentPanel } from "./documents.js";
import { copyIcon, downloadIcon, closeIcon, attachIcon, fileSvg } from "./icons.js";

function boot() {
  // Sidebar actions.
  el("new-chat").onclick = createSession;
  el("new-folder").onclick = createFolder;
  el("open-models").onclick = openModels;
  el("open-settings").onclick = () => {
    loadSettings();
    switchSettingsTab("general");  // Always open on the first tab.
    el("settings-modal").showModal();
  };

  // Dropping a chat onto the list background (not a folder) moves it to root.
  enableRootDrop();

  // Mobile sidebar drawer: hamburger opens it, backdrop tap closes it.
  el("menu-toggle").onclick = openSidebar;
  el("sidebar-backdrop").onclick = closeSidebar;

  // Composer: Enter sends, Shift+Enter inserts a newline.
  const input = el("input");
  input.addEventListener("input", () => autoGrow(input));
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      el("composer").requestSubmit();
    }
  });
  el("composer").addEventListener("submit", (e) => {
    e.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    input.value = "";
    autoGrow(input);
    sendMessage(text);
  });

  // Attach files: the button opens the hidden file input; choosing files (or
  // dropping them on the composer) uploads them as documents.
  el("attach").innerHTML = attachIcon();
  el("attach").onclick = () => el("file-input").click();
  el("file-input").onchange = (e) => {
    uploadFiles(e.target.files);
    e.target.value = "";  // Allow re-selecting the same file later.
  };
  enableComposerDrop();

  // Reopen the documents panel after it has been closed.
  el("docs-toggle").innerHTML = fileSvg() + '<span class="docs-toggle-count"></span>';
  el("docs-toggle").onclick = showDocumentPanel;

  // Chat scrolling: scrolling near the top lazily loads the previous page of
  // messages (infinite scroll up); a floating button appears once the view is
  // away from the bottom and jumps back to the most recent message.
  const messages = el("messages");
  const scrollDown = el("scroll-down");
  // Show the "jump to latest" button only when scrolled meaningfully up.
  const refreshScrollDown = () => {
    const distanceFromBottom =
      messages.scrollHeight - messages.scrollTop - messages.clientHeight;
    scrollDown.classList.toggle("visible", distanceFromBottom > 120);
  };
  messages.addEventListener("scroll", () => {
    if (messages.scrollTop < 80) loadOlderMessages();
    refreshScrollDown();
  });
  scrollDown.onclick = () => {
    messages.scrollTop = messages.scrollHeight;
  };

  // Settings modal.
  el("save-settings").onclick = saveSettings;

  // Memory modal (opened from Settings). The toggle saves on change; there's no
  // Save button, so flipping it takes effect immediately.
  el("open-memory").onclick = openMemory;
  el("close-memory").onclick = () => el("memory-modal").close();
  el("clear-memory").onclick = clearMemories;
  el("set-memory").onchange = toggleMemory;

  // Models modal.
  el("pull-btn").onclick = pullManual;
  el("pull-cancel").onclick = cancelDownload;
  el("refresh-catalog").onclick = refreshCatalog;
  el("close-models").onclick = () => el("models-modal").close();
  // Scope each modal's tabs to itself: both reuse the .tab class, so an unscoped
  // selector would wire the settings tabs to the Models switcher and vice versa.
  for (const tab of document.querySelectorAll("#models-modal .tab")) {
    tab.onclick = () => switchTab(tab.dataset.tab);
  }
  for (const tab of document.querySelectorAll("#settings-modal .tab")) {
    tab.onclick = () => switchSettingsTab(tab.dataset.tab);
  }

  // Document panel: icons live in icons.js, so paint them in here, then wire
  // the copy / download / close actions to the active document.
  el("artifact-copy").innerHTML = copyIcon();
  el("artifact-download").innerHTML = downloadIcon();
  el("artifact-close").innerHTML = closeIcon();
  el("artifact-copy").onclick = copyActiveDocument;
  el("artifact-download").onclick = downloadActiveDocument;
  el("artifact-close").onclick = closeDocumentPanel;

  // Engine banner retry.
  el("banner-retry").onclick = checkHealth;

  // Initial load.
  checkHealth();
  loadModels();
  loadSidebar();
}

document.addEventListener("DOMContentLoaded", boot);
