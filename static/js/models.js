import { el, escapeHtml } from "./dom.js";
import { api, streamSSE } from "./api.js";
import { state } from "./state.js";

// Open the Models modal and refresh everything inside it.
export function openModels() {
  loadHardware();
  loadCatalog();
  loadModels();
  el("models-modal").showModal();
}

// Fetch detected hardware and render the summary line atop the modal.
async function loadHardware() {
  const box = el("hw-summary");
  try {
    const { available, summary } = await api("/api/hardware");
    if (available) {
      box.innerHTML = `<span class="label">Detected hardware:</span> ${escapeHtml(summary)}`;
    } else {
      box.innerHTML = `<span class="label">Hardware detection unavailable.</span>`;
    }
  } catch {
    box.textContent = "";
  }
}

// Render a fit badge from a {level, label} object, or "" if none.
function fitBadge(fit) {
  if (!fit) return "";
  return `<span class="fit-badge fit-${fit.level}">${escapeHtml(fit.label)}</span>`;
}

// Load the installed models and refresh the picker, installed list, and catalog.
export async function loadModels() {
  try {
    const { models } = await api("/api/models");
    state.models = models;
  } catch {
    state.models = [];
  }
  renderModelSelect();
  renderInstalledModels();
  renderCatalog();
}

// Load the recommended catalog (fit-annotated) and render it.
async function loadCatalog() {
  try {
    const { catalog } = await api("/api/catalog");
    state.catalog = catalog;
  } catch {
    state.catalog = [];
  }
  renderCatalog();
}

// Names of installed models, for marking catalog entries as "installed".
function installedNames() {
  return new Set(state.models.map((m) => m.name));
}

function renderModelSelect() {
  const sel = el("model-select");
  const current = sel.value;
  sel.innerHTML = state.models.length
    ? ""
    : `<option value="">No model - download one below</option>`;
  for (const m of state.models) {
    const opt = document.createElement("option");
    opt.value = m.name;
    opt.textContent = prettyModelName(m.name);
    sel.appendChild(opt);
  }
  if (current && state.models.some((m) => m.name === current)) sel.value = current;
}

function renderCatalog() {
  const box = el("catalog");
  if (!box) return;
  const installed = installedNames();
  box.innerHTML = "";
  for (const m of state.catalog) {
    const isInstalled = installed.has(m.filename);
    const item = document.createElement("div");
    item.className = "catalog-item" + (isInstalled ? " installed" : "");
    item.innerHTML = `
      <div class="info">
        <div class="name">${escapeHtml(m.name)} ${fitBadge(m.fit)}</div>
        <div class="desc">${escapeHtml(m.description)}</div>
      </div>
      <span class="size">~${m.size_gb} GB</span>
      <button class="get-btn">${isInstalled ? "Installed" : "Download"}</button>`;
    const btn = item.querySelector(".get-btn");
    if (!isInstalled) btn.onclick = () => downloadModel(m.repo, m.filename);
    box.appendChild(item);
  }
}

function renderInstalledModels() {
  const list = el("installed-models");
  if (!list) return;
  list.innerHTML = "";
  if (!state.models.length) {
    list.innerHTML = `<li class="meta">No models installed yet.</li>`;
    return;
  }
  for (const m of state.models) {
    const li = document.createElement("li");
    const gb = (m.size / 1e9).toFixed(1);
    li.innerHTML = `<span><strong>${escapeHtml(prettyModelName(m.name))}</strong>
                    <span class="meta"> · ${gb} GB</span> ${fitBadge(m.fit)}</span>
                    <button class="del-model">Delete</button>`;
    li.querySelector(".del-model").onclick = () => deleteModel(m.name);
    list.appendChild(li);
  }
}

// Download a model from Hugging Face, streaming progress into the bar.
async function downloadModel(repo, filename) {
  const progress = el("pull-progress");
  const fill = el("pull-bar-fill");
  const status = el("pull-status");
  progress.classList.remove("hidden");
  fill.style.width = "0%";
  status.textContent = "Starting…";
  el("pull-btn").disabled = true;

  try {
    await streamSSE("/api/models/download", { repo, filename }, (evt) => {
      if (evt.error) { status.textContent = "Error: " + evt.error; return; }
      if (evt.status) status.textContent = evt.status;
      if (evt.total) {
        const pct = Math.round((evt.completed || 0) / evt.total * 100);
        fill.style.width = pct + "%";
        status.textContent = `${evt.status} - ${pct}%`;
      }
      if (evt.done && !evt.error) {
        status.textContent = "Done";
        fill.style.width = "100%";
      }
    });
  } catch (err) {
    status.textContent = "Error: " + err.message;
  } finally {
    el("pull-btn").disabled = false;
    await loadModels();
    checkHealth();
  }
}

// Download the model named in the manual (Advanced) tab inputs.
export async function pullManual() {
  const repo = el("pull-repo").value.trim();
  const filename = el("pull-file").value.trim();
  if (!repo || !filename) return;
  await downloadModel(repo, filename);
}

async function deleteModel(name) {
  if (!confirm(`Delete model "${name}"?`)) return;
  await api(`/api/models/${name}`, { method: "DELETE" });
  await loadModels();
  checkHealth();
}

// Trim the ".gguf" extension for friendlier display.
function prettyModelName(name) {
  return name.replace(/\.gguf$/i, "");
}

// Switch the active tab in the Models modal.
export function switchTab(name) {
  for (const tab of document.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.tab === name);
  }
  for (const panel of ["browse", "manual", "installed"]) {
    el(`tab-${panel}`).classList.toggle("hidden", panel !== name);
  }
}

// Show/hide the "engine not installed" banner based on backend health.
export async function checkHealth() {
  try {
    const { engine_available } = await api("/api/health");
    el("engine-banner").classList.toggle("hidden", engine_available);
  } catch {
    el("engine-banner").classList.remove("hidden");
  }
}
