// Documents: the text files a chat works with, shown as tabs in the side panel.
//
// A document is uploaded by the user (button or drag-drop) or authored by the AI
// (the chat route writes a file the model returned back to the database). They
// are persisted per session, so reopening a chat restores them. The model never
// gets their contents in the prompt - it reads them on demand via the
// read_document tool - so this module is purely about the UI and the CRUD calls.
//
// The panel reuses the existing #artifact-panel / #artifact-editor; we add a tab
// strip (#artifact-tabs) so several documents can share it. Editing the textarea
// syncs back to the server (debounced, and flushed before a message is sent so
// the read tool sees the latest text).

import { el } from "./dom.js";
import { api } from "./api.js";
import { state } from "./state.js";
import { createSession } from "./sidebar.js";

// Text file extensions we accept. Kept broad but text-only; binary types are
// rejected (later we may support more types - see CLAUDE.md). Mirrors the
// file-ish languages the panel knows how to show.
const ALLOWED_EXT = new Set([
  "md", "markdown", "txt", "text", "json", "jsonc", "yaml", "yml", "toml",
  "ini", "conf", "cfg", "csv", "tsv", "xml", "html", "htm", "css", "scss",
  "sass", "js", "jsx", "ts", "tsx", "py", "sh", "bash", "zsh", "sql", "c",
  "cpp", "h", "hpp", "java", "go", "rs", "rb", "php", "env", "log",
]);

// Upper bound on an uploaded file, matching the server's limit.
const MAX_BYTES = 1_000_000;

// Pending debounced save of hand edits, so we don't PATCH on every keystroke.
let saveTimer = null;

// Read a File as text via FileReader, resolving the string (or rejecting).
function readAsText(file) {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsText(file);
  });
}

// Cheap binary sniff: a real text file has no NUL bytes, and FileReader maps
// undecodable bytes to the replacement char, so a high density of those means
// it wasn't text.
function looksBinary(text) {
  if (text.includes("\u0000")) return true;
  const replacements = (text.match(/�/g) || []).length;
  return replacements > 8 && replacements / text.length > 0.02;
}

// Show / hide the side panel (same hooks the artifact panel used).
function showPanel() {
  el("artifact-panel").classList.remove("hidden");
  document.querySelector(".app").classList.add("panel-open");
}

export function closeDocumentPanel() {
  el("artifact-panel").classList.add("hidden");
  document.querySelector(".app").classList.remove("panel-open");
  updateDocsToggle();
}

// Reopen the panel after it was closed: show the active document (or the most
// recent one). This is what the composer's documents button calls, so a closed
// panel is never a dead end.
export function showDocumentPanel() {
  if (!state.documents.length) return;
  const active = state.documents.find((d) => d.id === state.activeDocumentId);
  openDocument((active || state.documents[state.documents.length - 1]).id);
}

// Show the composer's "documents" button only when there are documents AND the
// panel is closed, so it reads as a reopen affordance rather than a duplicate.
function updateDocsToggle() {
  const btn = el("docs-toggle");
  if (!btn) return;
  const panelHidden = el("artifact-panel").classList.contains("hidden");
  btn.hidden = state.documents.length === 0 || !panelHidden;
  const count = btn.querySelector(".docs-toggle-count");
  if (count) count.textContent = String(state.documents.length);
}

// Build the tab strip from state.documents, marking the active one.
function renderTabs() {
  updateDocsToggle();
  const strip = el("artifact-tabs");
  strip.innerHTML = "";
  for (const d of state.documents) {
    const tab = document.createElement("div");
    tab.className = "artifact-tab" + (d.id === state.activeDocumentId ? " active" : "");
    const name = document.createElement("span");
    name.className = "artifact-tab-name";
    name.textContent = d.filename;
    name.title = d.filename;
    name.onclick = () => openDocument(d.id);
    const del = document.createElement("button");
    del.type = "button";
    del.className = "artifact-tab-del";
    del.title = "Remove";
    del.innerHTML = "&times;";
    del.onclick = (e) => { e.stopPropagation(); deleteDocument(d.id); };
    tab.append(name, del);
    strip.appendChild(tab);
  }
}

// Open one document in the editor, fetching its content lazily (the list
// endpoint omits content to stay light).
export async function openDocument(id) {
  const doc = state.documents.find((d) => d.id === id);
  if (!doc) return;
  if (doc.content === undefined) {
    try {
      const full = await api(`/api/sessions/${state.currentSessionId}/documents/${id}`);
      doc.content = full.content;
    } catch {
      doc.content = "";
    }
  }
  state.activeDocumentId = id;
  el("artifact-title").textContent = doc.filename;
  const editor = el("artifact-editor");
  editor.value = doc.content;
  editor.oninput = onEditorInput;
  el("artifact-panel").dataset.current = id;
  showPanel();
  renderTabs();
}

// Open the document matching a name (used by an in-chat artifact card); falls
// back to the most recent document when the name is unknown.
export function openDocumentByName(name) {
  const lower = (name || "").toLowerCase();
  const doc = state.documents.find((d) => d.filename.toLowerCase() === lower)
    || state.documents[state.documents.length - 1];
  if (doc) openDocument(doc.id);
}

// Load a session's documents into the panel (called when a chat is opened).
export async function loadDocuments(sessionId) {
  state.documents = [];
  state.activeDocumentId = null;
  if (!sessionId) { renderTabs(); closeDocumentPanel(); return; }
  try {
    const resp = await api(`/api/sessions/${sessionId}/documents`);
    state.documents = resp.documents || [];
  } catch {
    state.documents = [];
  }
  renderTabs();
  if (state.documents.length) openDocument(state.documents[0].id);
  else closeDocumentPanel();
}

// Upload one or more files: validate text-only, POST each, show as tabs.
export async function uploadFiles(fileList) {
  const files = Array.from(fileList || []);
  if (!files.length) return;
  // Uploading implies a chat to attach to; create one if needed.
  if (!state.currentSessionId) await createSession();

  let lastId = null;
  for (const file of files) {
    const ext = file.name.includes(".") ? file.name.split(".").pop().toLowerCase() : "";
    if (!ALLOWED_EXT.has(ext)) {
      alert(`Can't attach ${file.name}: only text files are supported for now.`);
      continue;
    }
    if (file.size > MAX_BYTES) {
      alert(`Can't attach ${file.name}: the file is too large.`);
      continue;
    }
    let text;
    try {
      text = await readAsText(file);
    } catch {
      alert(`Couldn't read ${file.name}.`);
      continue;
    }
    if (looksBinary(text)) {
      alert(`Can't attach ${file.name}: it doesn't look like a text file.`);
      continue;
    }
    try {
      const doc = await api(`/api/sessions/${state.currentSessionId}/documents`, {
        method: "POST",
        body: JSON.stringify({ filename: file.name, content: text }),
      });
      state.documents.push(doc);
      lastId = doc.id;
    } catch (err) {
      alert(`Couldn't attach ${file.name}: ${err.message}`);
    }
  }
  renderTabs();
  if (lastId) openDocument(lastId);
}

// Remove a document from the chat.
export async function deleteDocument(id) {
  try {
    await api(`/api/documents/${id}`, { method: "DELETE" });
  } catch {
    // Already gone, or a transient error - drop it from the UI regardless.
  }
  state.documents = state.documents.filter((d) => d.id !== id);
  if (state.activeDocumentId === id) {
    state.activeDocumentId = null;
    if (state.documents.length) openDocument(state.documents[state.documents.length - 1].id);
    else closeDocumentPanel();
  }
  renderTabs();
}

// Editor changed: cache the text and schedule a save.
function onEditorInput() {
  const doc = state.documents.find((d) => d.id === state.activeDocumentId);
  if (!doc) return;
  doc.content = el("artifact-editor").value;
  doc.dirty = true;
  if (saveTimer) clearTimeout(saveTimer);
  saveTimer = setTimeout(savePending, 600);
}

// PATCH every document with unsaved hand edits.
async function savePending() {
  if (saveTimer) { clearTimeout(saveTimer); saveTimer = null; }
  for (const doc of state.documents.filter((d) => d.dirty)) {
    try {
      await api(`/api/documents/${doc.id}`, {
        method: "PATCH",
        body: JSON.stringify({ content: doc.content }),
      });
      doc.dirty = false;
    } catch {
      // Leave it dirty; the next edit or send will retry.
    }
  }
}

// Flush unsaved edits before sending a message, so the read_document tool sees
// the user's latest text rather than a stale server copy.
export async function flushPendingDocumentEdits() {
  await savePending();
}

// After a reply, re-fetch the documents (the chat route may have edited one or
// created a new file) and surface the change in the panel.
export async function refreshDocumentsAfterReply() {
  if (!state.currentSessionId) return;
  const previous = new Map(state.documents.map((d) => [d.id, d]));
  let docs = [];
  try {
    const resp = await api(`/api/sessions/${state.currentSessionId}/documents`);
    docs = resp.documents || [];
  } catch {
    return;
  }
  const changed = [];
  state.documents = docs.map((d) => {
    const prev = previous.get(d.id);
    if (prev && prev.updated_at === d.updated_at) {
      // Unchanged: keep any content we already fetched.
      return { ...d, content: prev.content };
    }
    changed.push(d);  // New or edited - content fetched lazily on open.
    return d;
  });
  renderTabs();
  if (changed.length) {
    openDocument(changed[changed.length - 1].id);
  } else if (!state.documents.length) {
    closeDocumentPanel();
  }
}

// Copy the active document's text to the clipboard.
export async function copyActiveDocument() {
  try {
    await navigator.clipboard.writeText(el("artifact-editor").value);
  } catch {
    el("artifact-editor").select();
  }
}

// Download the active document under its filename.
export function downloadActiveDocument() {
  const doc = state.documents.find((d) => d.id === state.activeDocumentId);
  const blob = new Blob([el("artifact-editor").value], { type: "text/plain" });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = doc ? doc.filename : "document.txt";
  link.click();
  URL.revokeObjectURL(url);
}
