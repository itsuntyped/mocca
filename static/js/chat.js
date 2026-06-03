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
  for (const m of messages) {
    if (m.role === "tool") {
      // Tool rows are stored as JSON {name, arguments, result}. They're only
      // shown when the user has enabled tool-call visibility; otherwise skip
      // them (they remain stored, so toggling the setting reveals them later).
      if (!state.showToolCalls) continue;
      try {
        const t = JSON.parse(m.content);
        addToolBlock(t.name, t.arguments, t.result);
      } catch {
        addMessageBubble("assistant", m.content);  // Fall back to plain text.
      }
    } else {
      addMessageBubble(m.role, m.content);
    }
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
  wrap.querySelector(".bubble").textContent = content;
  box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
  return wrap.querySelector(".bubble");
}

// Pretty-print a tool's arguments object for display.
function prettyArgs(argsObj) {
  if (!argsObj || Object.keys(argsObj).length === 0) return "(none)";
  return JSON.stringify(argsObj, null, 2);
}

// Append a collapsible tool-call block (a <details>) to the message area.
// Pass result === null for a still-running call, then call the returned
// setResult(text) once it finishes. Pass beforeEl to insert above an element
// (so a running call shows above the in-progress answer bubble).
// Returns { setResult }.
export function addToolBlock(name, argsObj, result = null, beforeEl = null) {
  const box = el("messages");
  el("empty-state")?.remove();

  const wrap = document.createElement("div");
  wrap.className = "msg assistant";
  wrap.innerHTML = `<details class="tool-block">
      <summary class="tool-head">
        <span class="tool-name"></span>
        <span class="tool-state"></span>
      </summary>
      <div class="tool-body">
        <div class="tool-sec"><span class="tool-label">Arguments</span><pre class="tool-args"></pre></div>
        <div class="tool-sec tool-result-sec"><span class="tool-label">Result</span><pre class="tool-result"></pre></div>
      </div>
    </details>`;
  wrap.querySelector(".tool-name").textContent = `Used tool: ${name}`;
  wrap.querySelector(".tool-args").textContent = prettyArgs(argsObj);

  const stateEl = wrap.querySelector(".tool-state");
  const resultEl = wrap.querySelector(".tool-result");
  const resultSec = wrap.querySelector(".tool-result-sec");
  const setResult = (text) => {
    stateEl.textContent = "done";
    resultEl.textContent = text;
    resultSec.classList.remove("hidden");
  };
  if (result === null) {
    stateEl.textContent = "running…";
    resultSec.classList.add("hidden");
  } else {
    setResult(result);
  }

  if (beforeEl) box.insertBefore(wrap, beforeEl);
  else box.appendChild(wrap);
  box.scrollTop = box.scrollHeight;
  return { setResult };
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
  // The answer bubble's row, so tool blocks can be inserted above the answer.
  const assistantWrap = bubble.closest(".msg");
  // Running tool blocks keyed by call id, so a tool_result can finish its block.
  const toolBlocks = new Map();

  let reply = "";
  try {
    await streamSSE("/api/chat",
      { session_id: state.currentSessionId, model, message: text },
      (evt) => {
        if (evt.chunk) {
          reply += evt.chunk;
          bubble.textContent = reply;
          el("messages").scrollTop = el("messages").scrollHeight;
        } else if (evt.tool_call) {
          // Only render tool activity when the display setting is on.
          if (state.showToolCalls) {
            const handle = addToolBlock(
              evt.tool_call.name, evt.tool_call.arguments, null, assistantWrap);
            toolBlocks.set(evt.tool_call.id, handle);
          }
        } else if (evt.tool_result) {
          toolBlocks.get(evt.tool_result.id)?.setResult(evt.tool_result.result);
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
