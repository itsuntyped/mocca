import { el } from "./dom.js";
import { api } from "./api.js";
import { state } from "./state.js";
import { loadToolSettings, saveToolSettings } from "./tools.js";

// Load just the display preferences the rest of the app needs into shared state,
// at startup, without opening the settings modal.
export async function loadDisplayPrefs() {
  const s = await api("/api/settings");
  state.showToolCalls = s.show_tool_calls;
}

// Populate the settings form from the backend.
export async function loadSettings() {
  const s = await api("/api/settings");
  el("set-system").value = s.system_prompt;
  el("set-temp").value = s.temperature;
  el("set-topp").value = s.top_p;
  el("set-maxtokens").value = s.max_tokens;
  el("set-nctx").value = s.n_ctx;
  el("set-ngpu").value = s.n_gpu_layers;
  el("set-nthreads").value = s.n_threads;
  el("set-loglevel").value = s.log_level;
  el("set-show-tools").checked = s.show_tool_calls;
  state.showToolCalls = s.show_tool_calls;
  // Tool categories live on a separate endpoint; load them alongside.
  await loadToolSettings();
}

// Persist the settings form, then close the modal.
export async function saveSettings(e) {
  e.preventDefault();
  await api("/api/settings", {
    method: "PUT",
    body: JSON.stringify({
      system_prompt: el("set-system").value,
      temperature: parseFloat(el("set-temp").value),
      top_p: parseFloat(el("set-topp").value),
      max_tokens: parseInt(el("set-maxtokens").value, 10),
      n_ctx: parseInt(el("set-nctx").value, 10),
      n_gpu_layers: parseInt(el("set-ngpu").value, 10),
      n_threads: parseInt(el("set-nthreads").value, 10),
      log_level: el("set-loglevel").value,
      show_tool_calls: el("set-show-tools").checked,
    }),
  });
  // Keep shared state in sync so already-open chats honour the new preference.
  state.showToolCalls = el("set-show-tools").checked;
  // Save tool category toggles (separate endpoint), then close.
  await saveToolSettings();
  el("settings-modal").close();
}
