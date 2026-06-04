import { el } from "./dom.js";
import { api, streamSSE } from "./api.js";
import { state } from "./state.js";
import { createSession, loadSidebar } from "./sidebar.js";
import { openModels } from "./models.js";
import { renderMarkdown } from "./markdown.js";
import { extractArtifacts, mountArtifactCards, openArtifact, pendingArtifact, generatingIndicator, currentOpenArtifact } from "./artifacts.js";

// Render an assistant reply: pull out file-like blocks as artifact cards, render
// the rest as Markdown, then wire the cards. Returns the artifacts found so the
// caller can decide whether to open the panel.
function renderAssistant(bubble, content) {
  bubble.classList.add("md");
  const { text, artifacts } = extractArtifacts(content);
  bubble.innerHTML = renderMarkdown(text);
  mountArtifactCards(bubble);
  return artifacts;
}

// Render a streaming reply. Once the model starts emitting a file, we stop
// pouring its contents into the chat and show prose-so-far plus an animated
// "Generating..." indicator, until the stream completes and we can finalize.
function renderStreaming(bubble, reply) {
  bubble.classList.add("md");
  const pending = pendingArtifact(reply);
  if (pending.active) {
    bubble.innerHTML = renderMarkdown(pending.before) + generatingIndicator(pending.name);
  } else {
    bubble.innerHTML = renderMarkdown(reply);
  }
}

// Set a bubble's content. Assistant replies are rendered as Markdown (with
// artifacts); user messages stay verbatim plain text (we never reinterpret what
// the user typed).
function setBubbleContent(bubble, role, content) {
  if (role === "assistant") {
    renderAssistant(bubble, content);
  } else {
    bubble.textContent = content;
  }
}

// Mocca never renders the assistant's tool calls in the chat; they run behind
// the scenes. Stored "tool" rows and tool_call/tool_result SSE events are
// therefore ignored by the UI (see renderMessages and sendMessage).

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
  for (const m of messages) {
    // Tool rows are stored only for the record; we never display them.
    if (m.role === "tool") continue;
    addMessageBubble(m.role, m.content);
  }
  box.scrollTop = box.scrollHeight;
}

// Append one message bubble; returns its .bubble element for live streaming.
export function addMessageBubble(role, content) {
  const box = el("messages");
  el("empty-state")?.remove();

  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  wrap.innerHTML = `<div class="bubble"></div>`;
  setBubbleContent(wrap.querySelector(".bubble"), role, content);
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

  // If a file is open in the panel, send its current (possibly edited) state so
  // the model can apply follow-up changes to what the user is actually looking at.
  const openFile = currentOpenArtifact();
  const body = { session_id: state.currentSessionId, model, message: text };
  if (openFile) body.open_file = openFile;

  let reply = "";
  try {
    await streamSSE("/api/chat", body,
      (evt) => {
        if (evt.chunk) {
          reply += evt.chunk;
          renderStreaming(bubble, reply);
          el("messages").scrollTop = el("messages").scrollHeight;
        } else if (evt.error) {
          bubble.textContent = "Error: " + evt.error;
        }
        // tool_call / tool_result events are intentionally ignored: tools run
        // behind the scenes and are never shown in the chat.
      });
  } catch (err) {
    bubble.textContent = "Error: " + err.message;
  } finally {
    bubble.classList.remove("streaming");
    // Re-render once the full reply is in: only now can we reliably detect
    // artifacts (during streaming the close fence isn't known yet). Auto-open
    // the last artifact so it's front-and-centre, like a freshly made document.
    if (reply) {
      const artifacts = renderAssistant(bubble, reply);
      if (artifacts.length) openArtifact(artifacts[artifacts.length - 1].id);
    }
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
