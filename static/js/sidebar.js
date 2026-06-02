import { el, escapeHtml } from "./dom.js";
import { api } from "./api.js";
import { state } from "./state.js";
import { starSvg, chevronSvg, pencilSvg, trashSvg } from "./icons.js";
import { renderMessages, showEmptyState } from "./chat.js";
import { enableChatDrag, enableFolderDrop, enableTouchMove } from "./dragdrop.js";

// Open the off-canvas sidebar (only visible as a drawer on small screens).
export function openSidebar() {
  el("sidebar").classList.add("open");
  el("sidebar-backdrop").classList.remove("hidden");
}

// Close the sidebar drawer. No-op visually on desktop (the sidebar is static).
export function closeSidebar() {
  el("sidebar").classList.remove("open");
  el("sidebar-backdrop").classList.add("hidden");
}

// Fetch folders and sessions together, then re-render the sidebar.
export async function loadSidebar() {
  const [folders, sessions] = await Promise.all([
    api("/api/folders"),
    api("/api/sessions"),
  ]);
  state.folders = folders.folders;
  state.sessions = sessions.sessions;
  renderSidebar();
}

// Order chats within a container: favorites first, then most-recent first.
function sortChats(chats) {
  return [...chats].sort((a, b) => {
    const fav = (b.favorite ? 1 : 0) - (a.favorite ? 1 : 0);
    if (fav !== 0) return fav;
    return (b.updated_at || "").localeCompare(a.updated_at || "");
  });
}

// Render the whole tree: folders (alphabetical) first, then root chats.
export function renderSidebar() {
  const list = el("session-list");
  list.innerHTML = "";
  for (const f of state.folders) list.appendChild(renderFolder(f));
  const rootChats = sortChats(state.sessions.filter((s) => !s.folder_id));
  for (const s of rootChats) list.appendChild(renderChatItem(s));
}

// Build one folder block (header + its chats), with drop support.
function renderFolder(f) {
  const collapsed = state.collapsed.has(f.id);
  const wrap = document.createElement("div");
  wrap.className = "folder";
  wrap.dataset.folderId = f.id;

  const header = document.createElement("div");
  header.className = "folder-header";
  header.innerHTML = `
    <button class="folder-toggle" aria-label="Toggle folder">${chevronSvg(collapsed)}</button>
    <span class="folder-name">${escapeHtml(f.name)}</span>
    <button class="folder-del" title="Delete folder" aria-label="Delete folder">&times;</button>`;
  header.querySelector(".folder-toggle").onclick = () => toggleFolder(f.id);
  header.querySelector(".folder-name").onclick = () => toggleFolder(f.id);
  header.querySelector(".folder-name").ondblclick = () => renameFolder(f.id, f.name);
  header.querySelector(".folder-del").onclick = (e) => { e.stopPropagation(); deleteFolder(f.id, f.name); };
  wrap.appendChild(header);

  const body = document.createElement("div");
  body.className = "folder-body" + (collapsed ? " hidden" : "");
  const chats = sortChats(state.sessions.filter((s) => s.folder_id === f.id));
  for (const s of chats) body.appendChild(renderChatItem(s));
  if (!chats.length) {
    const empty = document.createElement("div");
    empty.className = "folder-empty";
    empty.textContent = "Drop chats here";
    body.appendChild(empty);
  }
  wrap.appendChild(body);

  enableFolderDrop(wrap, f.id);
  return wrap;
}

// Build one chat row: favorite star, title, rename pencil, delete, drag support.
function renderChatItem(s) {
  const item = document.createElement("div");
  item.className = "session-item" + (s.id === state.currentSessionId ? " active" : "");
  item.dataset.id = s.id;
  item.draggable = true;
  item.innerHTML = `
    <button class="fav ${s.favorite ? "on" : ""}" title="Favorite" aria-label="Favorite">${starSvg()}</button>
    <span class="title" title="Double-click to rename">${escapeHtml(s.title)}</span>
    <button class="rename" title="Rename" aria-label="Rename">${pencilSvg()}</button>
    <button class="del" title="Delete" aria-label="Delete"><span class="del-x">&times;</span><span class="del-trash">${trashSvg()}</span></button>`;
  const titleEl = item.querySelector(".title");
  titleEl.onclick = () => selectSession(s.id);
  titleEl.ondblclick = (e) => { e.stopPropagation(); startRenameChat(s, item, titleEl); };
  item.querySelector(".fav").onclick = (e) => { e.stopPropagation(); toggleFavorite(s); };
  item.querySelector(".rename").onclick = (e) => { e.stopPropagation(); startRenameChat(s, item, titleEl); };
  item.querySelector(".del").onclick = (e) => { e.stopPropagation(); deleteSession(s.id); };
  enableChatDrag(item, s.id);    // Desktop: native HTML5 drag.
  enableTouchMove(item, s.id);   // Mobile: long-press to move.
  return item;
}

// Replace a chat's title with an inline input. Enter saves, Escape cancels.
function startRenameChat(s, item, titleEl) {
  const input = document.createElement("input");
  input.className = "rename-input";
  input.value = s.title;
  item.draggable = false;  // Allow text selection inside the input while editing.

  titleEl.replaceWith(input);
  input.focus();
  input.select();

  let settled = false;
  const commit = async () => {
    if (settled) return;
    settled = true;
    const name = input.value.trim();
    if (name && name !== s.title) {
      await api(`/api/sessions/${s.id}`, {
        method: "PATCH",
        body: JSON.stringify({ title: name }),
      });
    }
    await loadSidebar();
  };
  const cancel = () => { if (!settled) { settled = true; renderSidebar(); } };

  input.onclick = (e) => e.stopPropagation();
  input.onkeydown = (e) => {
    e.stopPropagation();  // Don't let Enter/Escape bubble to other handlers.
    if (e.key === "Enter") { e.preventDefault(); commit(); }
    else if (e.key === "Escape") { e.preventDefault(); cancel(); }
  };
  input.onblur = commit;
}

// Update which chat row is highlighted without rebuilding the sidebar.
function updateActiveHighlight() {
  for (const item of document.querySelectorAll(".session-item")) {
    item.classList.toggle("active", item.dataset.id === state.currentSessionId);
  }
}

// Collapse or expand a folder.
function toggleFolder(id) {
  if (state.collapsed.has(id)) state.collapsed.delete(id);
  else state.collapsed.add(id);
  renderSidebar();
}

// Create a new (root-level) folder.
export async function createFolder() {
  const name = (prompt("Folder name:", "New folder") || "").trim();
  if (!name) return;
  await api("/api/folders", { method: "POST", body: JSON.stringify({ name }) });
  await loadSidebar();
}

// Rename a folder.
async function renameFolder(id, current) {
  const name = (prompt("Rename folder:", current) || "").trim();
  if (!name || name === current) return;
  await api(`/api/folders/${id}`, { method: "PATCH", body: JSON.stringify({ name }) });
  await loadSidebar();
}

// Delete a folder (its chats move back to the top level).
async function deleteFolder(id, name) {
  if (!confirm(`Delete folder "${name}"? Its chats move back to the top level.`)) return;
  await api(`/api/folders/${id}`, { method: "DELETE" });
  await loadSidebar();
}

// Flag/unflag a chat as a favorite.
async function toggleFavorite(s) {
  await api(`/api/sessions/${s.id}/favorite`, {
    method: "PUT",
    body: JSON.stringify({ favorite: !s.favorite }),
  });
  await loadSidebar();
}

// Open a chat: load it, highlight it, render its messages, close the drawer.
async function selectSession(id) {
  state.currentSessionId = id;
  const session = await api(`/api/sessions/${id}`);
  if (session.model) el("model-select").value = session.model;
  updateActiveHighlight();
  renderMessages(session.messages);
  closeSidebar();
}

// Create a new chat (using the active model) and open it.
export async function createSession() {
  const model = el("model-select").value;
  const session = await api("/api/sessions", {
    method: "POST",
    body: JSON.stringify({ title: "New chat", model }),
  });
  await loadSidebar();
  await selectSession(session.id);
  closeSidebar();
  el("input").focus();
}

// Delete a chat; if it was open, fall back to the empty state.
async function deleteSession(id) {
  await api(`/api/sessions/${id}`, { method: "DELETE" });
  if (state.currentSessionId === id) {
    state.currentSessionId = null;
    showEmptyState();
  }
  await loadSidebar();
}
