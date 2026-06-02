import { el } from "./dom.js";
import { api, streamSSE } from "./api.js";
import { state } from "./state.js";
import { createSession, loadSidebar } from "./sidebar.js";
import { openModels } from "./models.js";

// Show the welcome/empty state in the message area.
export function showEmptyState() {
  el("messages").innerHTML = `<div class="empty-state" id="empty-state">
    <img class="brand-logo" src="/static/images/mocca.png" alt="Mocca" />
    <p>Pick a model and start chatting. Everything runs locally.</p></div>`;
}

// Render a full conversation into the message area.
export function renderMessages(messages) {
  const box = el("messages");
  box.innerHTML = "";
  if (!messages.length) { showEmptyState(); return; }
  for (const m of messages) addMessageBubble(m.role, m.content);
  box.scrollTop = box.scrollHeight;
}

// Append one message bubble; returns its .bubble element for live streaming.
export function addMessageBubble(role, content) {
  const box = el("messages");
  el("empty-state")?.remove();

  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `<div class="bubble"></div>`;
  wrap.querySelector(".bubble").textContent = content;
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
  return wrap.querySelector(".bubble");
}

// Send the user's message and stream the assistant reply into a new bubble.
export async function sendMessage(text) {
  if (state.sending) return;
  const model = el("model-select").value;
  if (!model) { openModels(); return; }  // No model picked yet - open the picker.

  // Auto-create a session on the first message.
  if (!state.currentSessionId) await createSession();

  state.sending = true;
  el("send").disabled = true;

  addMessageBubble("user", text);
  const bubble = addMessageBubble("assistant", "");
  bubble.classList.add("streaming");

  let reply = "";
  try {
    await streamSSE("/api/chat",
      { session_id: state.currentSessionId, model, message: text },
      (evt) => {
        if (evt.chunk) {
          reply += evt.chunk;
          bubble.textContent = reply;
          el("messages").scrollTop = el("messages").scrollHeight;
        } else if (evt.error) {
          bubble.textContent = "Error: " + evt.error;
        }
      });
  } catch (err) {
    bubble.textContent = "Error: " + err.message;
  } finally {
    bubble.classList.remove("streaming");
    state.sending = false;
    el("send").disabled = false;
    refreshSessionTitleMaybe(text);
  }
}

// Title a brand-new chat after its first user message, for easy scanning.
async function refreshSessionTitleMaybe(firstText) {
  const session = state.sessions.find((s) => s.id === state.currentSessionId);
  if (session && session.title === "New chat") {
    const title = firstText.slice(0, 40);
    await api(`/api/sessions/${session.id}`, {
      method: "PATCH",
      body: JSON.stringify({ title }),
    });
    await loadSidebar();
  }
}
