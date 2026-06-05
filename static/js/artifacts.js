// Artifacts: show file-like assistant output in a side editor panel.
//
// PROTOTYPE. When the model returns the contents of a file (a README, an HTML
// page, a script...), rendering it inline in the chat is awkward: Markdown can't
// reliably show fenced code that itself contains fences, and long files bury the
// conversation. Instead we detect such blocks, replace them in the chat with a
// small "card", and open the full content in an editable panel on the right.
// The user can edit, copy, or download it.
//
// Out of scope for this first cut (deliberately): asking the AI to update the
// artifact. That round-trip is the part small local models struggle with, so we
// prove the detection + panel first and add it later if it earns its keep.

import { escapeHtml } from "./dom.js";
import { fileSvg } from "./icons.js";

// Languages we treat as "a file worth opening in the panel". This (and
// MIN_LINES below) are the tuning knobs for how eagerly blocks become artifacts.
const FILE_LANGS = new Set([
  "markdown", "md", "html", "htm", "xml", "css", "scss", "sass", "json",
  "jsonc", "yaml", "yml", "toml", "ini", "conf", "csv", "tsv", "sql", "js",
  "javascript", "jsx", "ts", "typescript", "tsx", "py", "python", "sh",
  "bash", "zsh", "c", "cpp", "h", "hpp", "java", "go", "rust", "rs", "rb",
  "ruby", "php", "dockerfile", "makefile", "txt", "text", "env",
]);

// A block must be at least this many lines to become an artifact - short
// snippets stay inline in the chat where they read fine.
const MIN_LINES = 6;

// Map a language to a sensible file extension for downloads / default names.
const LANG_EXT = {
  markdown: "md", md: "md", html: "html", htm: "html", xml: "xml", css: "css",
  scss: "scss", sass: "sass", json: "json", jsonc: "json", yaml: "yml",
  yml: "yml", toml: "toml", ini: "ini", conf: "conf", csv: "csv", tsv: "tsv",
  sql: "sql", js: "js", javascript: "js", jsx: "jsx", ts: "ts",
  typescript: "ts", tsx: "tsx", py: "py", python: "py", sh: "sh", bash: "sh",
  zsh: "sh", c: "c", cpp: "cpp", h: "h", hpp: "hpp", java: "java", go: "go",
  rust: "rs", rs: "rs", rb: "rb", ruby: "rb", php: "php",
  dockerfile: "dockerfile", makefile: "makefile", txt: "txt", text: "txt",
  env: "env",
};

// Registry of artifacts by id, so a card click can find its content. It grows as
// messages render; fine for a prototype (entries are small text).
const registry = new Map();

// A monotonically increasing id source - guarantees uniqueness across renders.
let counter = 0;

// A fenced-code opener: indent, the backticks, then the info string.
const OPEN = /^(\s*)(`{3,})(.*)$/;
// A bare closing fence (backticks only, no info string).
const BARE_CLOSE = /^\s*`{3,}\s*$/;

// If an info string starts with something that looks like a filename
// ("README.md", "src/app.py"), return it; otherwise null.
function filenameIn(info) {
  const first = info.trim().split(/\s+/)[0] || "";
  return /^[\w.\-/]+\.[A-Za-z0-9]+$/.test(first) ? first : null;
}

// Scan raw assistant text for artifact-worthy code blocks. Returns the text with
// each such block replaced by a placeholder token, plus the artifacts found.
// Non-artifact code blocks are left untouched for the Markdown renderer.
export function extractArtifacts(src) {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  const out = [];
  const found = [];
  let i = 0;

  while (i < lines.length) {
    const m = lines[i].match(OPEN);
    if (!m) { out.push(lines[i]); i++; continue; }

    const fenceLen = m[2].length;
    const info = m[3].trim();
    const lang = (info.split(/\s+/)[0] || "").toLowerCase();
    const filename = filenameIn(info);

    // Find the closing fence. A Markdown document legitimately contains its own
    // fences, so for md/markdown we take the LAST bare fence as the close (the
    // greedy rule we discussed); for any other language, the first matching one.
    let close;
    if (lang === "markdown" || lang === "md") {
      close = -1;
      for (let k = i + 1; k < lines.length; k++) {
        if (BARE_CLOSE.test(lines[k])) close = k;
      }
    } else {
      const re = new RegExp("^\\s*`{" + fenceLen + ",}\\s*$");
      close = i + 1;
      while (close < lines.length && !re.test(lines[close])) close++;
    }
    const end = close > i ? close : lines.length; // unclosed -> runs to the end
    const content = lines.slice(i + 1, end).join("\n");
    const nLines = content ? content.split("\n").length : 0;

    if ((FILE_LANGS.has(lang) || filename) && nLines >= MIN_LINES) {
      const id = "art-" + counter++;
      const artifact = {
        id, content, nLines,
        lang: lang || "text",
        ext: LANG_EXT[lang] || "txt",
        // We only know the name when the model gives one (filename in the fence
        // or, later, an uploaded file). When unknown we leave it blank rather
        // than invent one.
        filename: filename || "",
      };
      registry.set(id, artifact);
      found.push(artifact);
      // Placeholder on its own line so the renderer makes it a lone paragraph.
      out.push("", "@@ARTIFACT:" + id + "@@", "");
    } else {
      // Not an artifact: keep the block verbatim for normal inline rendering.
      for (let k = i; k <= end && k < lines.length; k++) out.push(lines[k]);
    }
    i = end < lines.length ? end + 1 : lines.length;
  }

  return { text: out.join("\n"), artifacts: found };
}

// During streaming we can't finalize an artifact - the closing fence may not
// have arrived - but we CAN tell when the model has STARTED emitting one. This
// scans the partial reply for the first artifact-worthy opening fence; if found,
// the caller renders the prose before it and a "Generating" indicator, and stops
// streaming the file's contents into the chat until the reply completes.
export function pendingArtifact(src) {
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  let i = 0;
  while (i < lines.length) {
    const m = lines[i].match(OPEN);
    if (!m) { i++; continue; }

    const info = m[3].trim();
    const lang = (info.split(/\s+/)[0] || "").toLowerCase();
    const filename = filenameIn(info);
    if (FILE_LANGS.has(lang) || filename) {
      return { active: true, before: lines.slice(0, i).join("\n"), name: filename || "" };
    }

    // A non-artifact fence (e.g. a short snippet): skip past its close so its
    // contents can't be mistaken for an artifact opener. If it isn't closed yet
    // we're still inside ordinary streamed code - nothing pending.
    const re = new RegExp("^\\s*`{" + m[2].length + ",}\\s*$");
    let k = i + 1;
    while (k < lines.length && !re.test(lines[k])) k++;
    if (k >= lines.length) return { active: false, before: src };
    i = k + 1;
  }
  return { active: false, before: src };
}

// The animated "Generating..." placeholder shown in the chat while a file is
// still streaming (swapped for the real card once the reply completes). Names the
// file when known, otherwise stays generic.
export function generatingIndicator(name) {
  const label = name ? `Generating ${escapeHtml(name)}…` : "Generating file…";
  return (
    `<div class="artifact-generating">` +
      `<span class="artifact-generating-spinner"></span>` +
      `<span class="artifact-generating-label">${label}</span>` +
    `</div>`
  );
}

// The inline card markup that stands in for an artifact in the chat. When the
// file has no known name, the language line carries its identity instead.
function cardHtml(a) {
  const unit = a.nLines === 1 ? "line" : "lines";
  const title = a.filename
    ? `<span class="artifact-card-title">${escapeHtml(a.filename)}</span>`
    : "";
  return (
    `<button class="artifact-card" data-artifact-id="${a.id}">` +
      `<span class="artifact-card-icon">${fileSvg()}</span>` +
      `<span class="artifact-card-text">` +
        title +
        `<span class="artifact-card-sub">${escapeHtml(a.lang)} &middot; ${a.nLines} ${unit}</span>` +
      `</span>` +
      `<span class="artifact-card-open">Open</span>` +
    `</button>`
  );
}

// Replace placeholder paragraphs in a rendered bubble with real artifact cards.
// Each card, when clicked, calls onOpen(filename) so the caller can open the
// corresponding persisted document in the side panel (see documents.js). Call
// right after setting the bubble's innerHTML.
export function mountArtifactCards(bubble, onOpen) {
  if (!bubble.innerHTML.includes("@@ARTIFACT:")) return;
  bubble.innerHTML = bubble.innerHTML.replace(
    /<p>@@ARTIFACT:(art-\d+)@@<\/p>/g,
    (_, id) => {
      const a = registry.get(id);
      return a ? cardHtml(a) : "";
    },
  );
  for (const card of bubble.querySelectorAll(".artifact-card")) {
    const a = registry.get(card.dataset.artifactId);
    card.onclick = () => onOpen && onOpen(a ? a.filename : "");
  }
}
