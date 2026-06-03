import { el } from "./dom.js";
import { api } from "./api.js";

// The Memory modal: the on/off switch plus the list of facts the AI has learned
// about the user, each deletable. Lives in its own modal (opened from Settings)
// so the list has room to grow and scroll. The toggle saves on its own - there's
// no Save button here - so changes take effect immediately.

// Open the modal: load the current toggle state and the stored memories.
export async function openMemory() {
  const s = await api("/api/settings");
  el("set-memory").checked = s.enable_memory;
  await renderMemories();
  el("memory-modal").showModal();
}

// Persist the memory on/off switch the moment it's flipped.
export async function toggleMemory() {
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({ enable_memory: el("set-memory").checked }),
  });
}

// Forget everything, then re-render. Wired to the footer's "Clear all" button.
export async function clearMemories() {
  await api("/api/memories", { method: "DELETE" });
  renderMemories();
}

// Render the list of learned facts, each with a delete button. The "Clear all"
// control lives in the modal footer (always reachable, even with a long list);
// we just show/hide it here based on whether there's anything to clear. Memory
// is meant to be transparent and user-controlled, so everything is shown.
async function renderMemories() {
  const box = el("memory-list");
  const { memories } = await api("/api/memories");
  box.replaceChildren();
  el("clear-memory").style.display = memories.length ? "" : "none";

  if (!memories.length) {
    const empty = document.createElement("p");
    empty.className = "hint";
    empty.style.margin = "0";
    empty.textContent = "Nothing remembered yet.";
    box.append(empty);
    return;
  }

  for (const m of memories) {
    const row = document.createElement("div");
    row.className = "memory-item";

    const text = document.createElement("span");
    text.textContent = m.content;

    const del = document.createElement("button");
    del.type = "button";
    del.className = "del";
    del.title = "Forget this";
    del.innerHTML = "&times;";
    del.onclick = async () => {
      await api(`/api/memories/${m.id}`, { method: "DELETE" });
      renderMemories();
    };

    row.append(text, del);
    box.append(row);
  }
}
