import { el } from "./dom.js";
import { api } from "./api.js";
import { state } from "./state.js";
import { loadSidebar } from "./sidebar.js";

// Make a chat row draggable with the mouse.
export function enableChatDrag(item, id) {
  item.addEventListener("dragstart", (e) => {
    state.draggingId = id;
    e.dataTransfer.effectAllowed = "move";
    e.dataTransfer.setData("text/plain", id);  // Required for Firefox to start a drag.
    item.classList.add("dragging");
  });
  item.addEventListener("dragend", () => {
    state.draggingId = null;
    item.classList.remove("dragging");
    document.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
  });
}

// Make a folder a drop target that moves the dragged chat into it.
export function enableFolderDrop(folderEl, folderId) {
  folderEl.addEventListener("dragover", (e) => {
    e.preventDefault();
    e.stopPropagation();  // Keep the root-drop handler from also firing.
    folderEl.classList.add("drop-target");
  });
  folderEl.addEventListener("dragleave", (e) => {
    if (!folderEl.contains(e.relatedTarget)) folderEl.classList.remove("drop-target");
  });
  folderEl.addEventListener("drop", (e) => {
    e.preventDefault();
    e.stopPropagation();
    folderEl.classList.remove("drop-target");
    const id = state.draggingId || e.dataTransfer.getData("text/plain");
    if (id) moveSession(id, folderId);
  });
}

// Attach the root drop zone (the list itself); dropping here means top level.
export function enableRootDrop() {
  const list = el("session-list");
  list.addEventListener("dragover", (e) => { e.preventDefault(); list.classList.add("drop-root"); });
  list.addEventListener("dragleave", (e) => {
    if (!list.contains(e.relatedTarget)) list.classList.remove("drop-root");
  });
  list.addEventListener("drop", (e) => {
    e.preventDefault();
    list.classList.remove("drop-root");
    const id = state.draggingId || e.dataTransfer.getData("text/plain");
    if (id) moveSession(id, null);
  });
}

// Move a chat into a folder, or to the root when folderId is null.
export async function moveSession(id, folderId) {
  const s = state.sessions.find((x) => x.id === id);
  if (s && (s.folder_id || null) === (folderId || null)) return;  // No-op.
  await api(`/api/sessions/${id}/folder`, {
    method: "PUT",
    body: JSON.stringify({ folder_id: folderId }),
  });
  await loadSidebar();
}

// Touch move: a long-press (~420ms) picks a chat up; dragging a finger over a
// folder (or the list background) and lifting drops it there. A floating
// "ghost" follows the finger and the target is highlighted.
export function enableTouchMove(item, id) {
  let timer = null, dragging = false, ghost = null;
  let startX = 0, startY = 0, lastX = 0, lastY = 0;

  const clearTimer = () => { if (timer) { clearTimeout(timer); timer = null; } };

  const positionGhost = (x, y) => {
    if (ghost) { ghost.style.left = x + "px"; ghost.style.top = y + "px"; }
  };

  const clearTargets = () => {
    document.querySelectorAll(".drop-target").forEach((n) => n.classList.remove("drop-target"));
    el("session-list").classList.remove("drop-root");
  };

  const highlight = (x, y) => {
    clearTargets();
    const under = document.elementFromPoint(x, y);
    const folder = under && under.closest(".folder");
    if (folder) folder.classList.add("drop-target");
    else if (under && under.closest("#session-list")) el("session-list").classList.add("drop-root");
  };

  const begin = () => {
    dragging = true;
    state.draggingId = id;
    if (navigator.vibrate) navigator.vibrate(15);  // Subtle "picked up" feedback.
    ghost = item.cloneNode(true);
    ghost.classList.add("drag-ghost");
    ghost.style.width = item.offsetWidth + "px";
    document.body.appendChild(ghost);
    positionGhost(lastX, lastY);
    highlight(lastX, lastY);
    item.classList.add("dragging");
  };

  const cleanup = () => {
    if (ghost) { ghost.remove(); ghost = null; }
    item.classList.remove("dragging");
    clearTargets();
    dragging = false;
    state.draggingId = null;
  };

  item.addEventListener("touchstart", (e) => {
    if (e.touches.length !== 1 || e.target.closest("button")) return;  // Ignore action buttons.
    const t = e.touches[0];
    startX = lastX = t.clientX;
    startY = lastY = t.clientY;
    clearTimer();
    timer = setTimeout(begin, 420);
  }, { passive: true });

  item.addEventListener("touchmove", (e) => {
    const t = e.touches[0];
    lastX = t.clientX;
    lastY = t.clientY;
    if (!dragging) {
      // Movement before the long-press fires means the user is scrolling.
      if (Math.abs(t.clientX - startX) > 12 || Math.abs(t.clientY - startY) > 12) clearTimer();
      return;
    }
    e.preventDefault();  // Hold scrolling while we drag.
    positionGhost(lastX, lastY);
    highlight(lastX, lastY);
  }, { passive: false });

  const onEnd = () => {
    clearTimer();
    if (!dragging) return;
    const under = document.elementFromPoint(lastX, lastY);
    const folder = under && under.closest(".folder");
    const onList = under && under.closest("#session-list");
    cleanup();
    if (folder) moveSession(id, folder.dataset.folderId);
    else if (onList) moveSession(id, null);
    // Dropped outside the sidebar: leave the chat where it was.
  };

  item.addEventListener("touchend", onEnd);
  item.addEventListener("touchcancel", () => { clearTimer(); if (dragging) cleanup(); });

  // A long-press also triggers the browser's native context menu; suppress it
  // while a press is pending or a drag is active so it doesn't pop up mid-move.
  item.addEventListener("contextmenu", (e) => {
    if (timer || dragging) e.preventDefault();
  });
}
