import { el } from "./dom.js";
import { api } from "./api.js";

// Populate the Tools section of the settings modal with one checkbox per tool
// category. Network categories are flagged so the user knows they reach out.
export async function loadToolSettings() {
  const data = await api("/api/tools");
  const box = el("tool-categories");
  box.innerHTML = "";

  const enabled = new Set(data.enabled);
  // Group the tool names under their category for a helpful sublabel.
  const byCategory = {};
  for (const t of data.tools) (byCategory[t.category] ||= []).push(t);

  for (const category of data.categories) {
    const tools = byCategory[category] || [];
    const isNetwork = tools.some((t) => !t.is_local);

    const row = document.createElement("label");
    row.className = "tool-cat";
    row.innerHTML = `<input type="checkbox" ${enabled.has(category) ? "checked" : ""} />
      <span class="tool-cat-text">
        <span class="tool-cat-name"></span>
        <span class="tool-cat-tools"></span>
      </span>`;
    // dataset carries the category name back to save without parsing the label.
    row.querySelector("input").dataset.category = category;

    const nameEl = row.querySelector(".tool-cat-name");
    nameEl.textContent = category;
    if (isNetwork) {
      const badge = document.createElement("span");
      badge.className = "tool-net";
      badge.textContent = "network";
      nameEl.appendChild(badge);
    }
    row.querySelector(".tool-cat-tools").textContent = tools.map((t) => t.name).join(", ");
    box.appendChild(row);
  }
}

// Persist the enabled tool categories from the checkboxes.
export async function saveToolSettings() {
  const checks = el("tool-categories").querySelectorAll("input[type=checkbox]");
  const enabled_categories = [];
  for (const c of checks) if (c.checked) enabled_categories.push(c.dataset.category);
  await api("/api/tools", {
    method: "PUT",
    body: JSON.stringify({ enabled_categories }),
  });
}
