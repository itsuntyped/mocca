import { el } from "./dom.js";
import { api } from "./api.js";

// The settings modal's tabs. Kept separate from the Models modal's tab switcher
// (models.js) so the two don't toggle each other's panels - both reuse the same
// .tab/.tab-panel styles, so the switching must be scoped per modal.
const SETTINGS_TABS = ["general", "generation", "engine"];

export function switchSettingsTab(name) {
  const modal = el("settings-modal");
  for (const tab of modal.querySelectorAll(".tab")) {
    tab.classList.toggle("active", tab.dataset.tab === name);
  }
  // Toggle .active (CSS shows/hides via visibility); the panels are stacked in
  // one grid cell, so all of them keep reserving height and the modal stays put.
  for (const t of SETTINGS_TABS) {
    el(`set-tab-${t}`).classList.toggle("active", t === name);
  }
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
  el("set-idle-unload").value = s.unload_idle_minutes;
  el("set-msgs-per-page").value = s.messages_per_page;
  el("set-loglevel").value = s.log_level;
  // Web search is the only network toggle; everything local stays on.
  el("set-web-search").checked = s.enable_web_search;
  // Memory lives in its own modal (opened via the "Manage memory" button), so
  // its toggle and list are handled there, not on this form.
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
      unload_idle_minutes: parseInt(el("set-idle-unload").value, 10),
      messages_per_page: parseInt(el("set-msgs-per-page").value, 10),
      log_level: el("set-loglevel").value,
      enable_web_search: el("set-web-search").checked,
    }),
  });
  el("settings-modal").close();
}
