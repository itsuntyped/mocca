import { el } from "./dom.js";
import { api, streamSSE } from "./api.js";
import { state } from "./state.js";
import { createSession, loadSidebar } from "./sidebar.js";
import { openModels } from "./models.js";
import { renderMarkdown } from "./markdown.js";
import { extractArtifacts, mountArtifactCards, pendingArtifact, generatingIndicator } from "./artifacts.js";
import { openDocumentByName, flushPendingDocumentEdits, refreshDocumentsAfterReply } from "./documents.js";

// Render an assistant reply: pull out file-like blocks as artifact cards, render
// the rest as Markdown, then wire the cards to open their persisted document.
// Returns the artifacts found.
function renderAssistant(bubble, content) {
  bubble.classList.add("md");
  const { text, artifacts } = extractArtifacts(content);
  bubble.innerHTML = renderMarkdown(text);
  mountArtifactCards(bubble, openDocumentByName);
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

// Build (but don't attach) one message wrapper. `seq` is the server's stable
// row cursor, stashed on the element so the infinite scroller knows which
// message is currently the oldest on screen. Freshly streamed bubbles have no
// seq yet (they aren't persisted until the turn ends) - that's fine, they're
// always the newest rows and never the scroll anchor.
function buildMessage(role, content, seq) {
  const wrap = document.createElement("div");
  wrap.className = `msg ${role}`;
  if (seq != null) wrap.dataset.seq = seq;
  wrap.innerHTML = `<div class="bubble"></div>`;
  setBubbleContent(wrap.querySelector(".bubble"), role, content);
  return wrap;
}

// Jump the message list to the newest message. Instant (never smoothed) and
// re-asserted on the next frame, so late layout - markdown/fonts settling, or
// the documents panel opening and reflowing the column narrower right after we
// scroll - can't leave us stranded above the bottom.
export function scrollMessagesToBottom() {
  const box = el("messages");
  box.scrollTop = box.scrollHeight;
  requestAnimationFrame(() => { box.scrollTop = box.scrollHeight; });
}

// Render a freshly fetched page (the latest, or any earlier one) into the box,
// replacing whatever was there. Resets the scroll cursor to this page and jumps
// to the bottom (newest message).
function renderPage(page) {
  const box = el("messages");
  box.innerHTML = "";
  if (!page.messages.length) {
    showEmptyState();
    state.oldestSeq = null;
    state.hasMoreOlder = false;
    state.expandedHistory = false;
    return;
  }
  for (const m of page.messages) box.appendChild(buildMessage(m.role, m.content, m.seq));
  state.oldestSeq = page.messages[0].seq;
  state.hasMoreOlder = page.has_more;
  state.expandedHistory = false;
  scrollMessagesToBottom();
}

// Fetch and render the most recent page of a session's conversation. This is
// the entry point used when opening a chat (see sidebar.selectSession).
export async function loadConversation(sessionId) {
  state.loadingOlder = false;
  const page = await api(`/api/sessions/${sessionId}/messages`);
  renderPage(page);
}

// Prepend the next older page when the user scrolls to the top. Anchors the
// viewport over the same content after the box grows, so scrolling up feels
// continuous rather than jumping. No-op while a fetch is in flight, when no
// older messages remain, or when no chat is open.
export async function loadOlderMessages() {
  if (state.loadingOlder || !state.hasMoreOlder) return;
  if (!state.currentSessionId || state.oldestSeq == null) return;
  state.loadingOlder = true;
  const box = el("messages");
  const prevHeight = box.scrollHeight;
  const prevTop = box.scrollTop;
  try {
    const page = await api(
      `/api/sessions/${state.currentSessionId}/messages?before=${state.oldestSeq}`,
    );
    if (!page.messages.length) {
      state.hasMoreOlder = false;
      return;
    }
    const frag = document.createDocumentFragment();
    for (const m of page.messages) frag.appendChild(buildMessage(m.role, m.content, m.seq));
    box.insertBefore(frag, box.firstChild);
    state.oldestSeq = page.messages[0].seq;
    state.hasMoreOlder = page.has_more;
    state.expandedHistory = true;  // The next new message collapses back to the latest page.
    box.scrollTop = prevTop + (box.scrollHeight - prevHeight);
  } finally {
    state.loadingOlder = false;
  }
}

// Append one message bubble; returns its .bubble element for live streaming.
export function addMessageBubble(role, content) {
  const box = el("messages");
  el("empty-state")?.remove();

  const wrap = buildMessage(role, content);
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

  // If the user had scrolled up and loaded older history, a new interaction
  // collapses the view back to just the latest page before the new turn is
  // added - they can scroll up again from there.
  if (state.expandedHistory) {
    const page = await api(`/api/sessions/${state.currentSessionId}/messages`);
    renderPage(page);
  }

  state.sending = true;
  el("send").disabled = true;

  addMessageBubble("user", text);
  const bubble = addMessageBubble("assistant", "");
  bubble.classList.add("streaming");

  // Flush any unsaved hand edits to attached documents first, so the model reads
  // the user's latest text (via the read_document tool) rather than a stale copy.
  await flushPendingDocumentEdits();
  const body = { session_id: state.currentSessionId, model, message: text };

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
    // artifacts (during streaming the close fence isn't known yet) and show them
    // as cards. The server has written any returned file back to a document, so
    // refresh the panel's tabs and surface the edited/new file.
    if (reply) {
      renderAssistant(bubble, reply);
      await refreshDocumentsAfterReply();
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
