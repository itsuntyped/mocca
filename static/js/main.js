import { el, autoGrow } from "./dom.js";
import { loadSidebar, createSession, createFolder, openSidebar, closeSidebar } from "./sidebar.js";
import { enableRootDrop } from "./dragdrop.js";
import { sendMessage } from "./chat.js";
import { openModels, pullManual, switchTab, checkHealth, loadModels, cancelDownload, refreshCatalog } from "./models.js";
import { loadSettings, saveSettings } from "./settings.js";
import { openMemory, toggleMemory, clearMemories } from "./memory.js";
import { closeArtifact, copyArtifact, downloadArtifact } from "./artifacts.js";
import { copyIcon, downloadIcon, closeIcon } from "./icons.js";

function boot() {
  // Sidebar actions.
  el("new-chat").onclick = createSession;
  el("new-folder").onclick = createFolder;
  el("open-models").onclick = openModels;
  el("open-settings").onclick = () => { loadSettings(); el("settings-modal").showModal(); };

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
  for (const tab of document.querySelectorAll(".tab")) {
    tab.onclick = () => switchTab(tab.dataset.tab);
  }

  // Artifact panel: icons live in icons.js, so paint them in here, then wire
  // the copy / download / close actions.
  el("artifact-copy").innerHTML = copyIcon();
  el("artifact-download").innerHTML = downloadIcon();
  el("artifact-close").innerHTML = closeIcon();
  el("artifact-copy").onclick = copyArtifact;
  el("artifact-download").onclick = downloadArtifact;
  el("artifact-close").onclick = closeArtifact;

  // Engine banner retry.
  el("banner-retry").onclick = checkHealth;

  // Initial load.
  checkHealth();
  loadModels();
  loadSidebar();
}

document.addEventListener("DOMContentLoaded", boot);
