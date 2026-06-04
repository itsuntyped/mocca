// A tiny, dependency-free Markdown renderer for assistant replies.
//
// Mocca is offline-first and build-free, so we cannot pull in a markdown
// library from a CDN or a bundler. This module is a small hand-rolled parser
// that covers the elements a chat model actually emits: headings, paragraphs,
// bold/italic/strikethrough, inline code, fenced code blocks, links, images,
// ordered/unordered (and nested) lists, blockquotes, horizontal rules, and
// GitHub-style tables. Everything is HTML-escaped before rendering so model
// output can never inject markup.
//
// The styling is deliberately restrained (see .bubble.md in style.css) - the
// goal is structure, not large flashy headings.

// Escape the four characters that matter for HTML so raw model text is inert.
function escapeHtml(s) {
  return s
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

// Render the inline span syntax inside a single block of text. The text is
// escaped first; code spans are pulled out and restored last so their contents
// are never treated as markup.
function inline(text) {
  text = escapeHtml(text);

  // Protect inline code spans with a placeholder so nothing inside them is
  // re-interpreted as bold/links/etc. The sentinel is deliberately ugly so it
  // never collides with real text.
  const codes = [];
  text = text.replace(/`([^`]+)`/g, (_, c) => {
    codes.push(c);
    return "\x01CODE" + (codes.length - 1) + "\x01";
  });

  // Images, then links (image first so its leading "!" isn't eaten by links).
  text = text.replace(
    /!\[([^\]]*)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
    (_, alt, url) => `<img alt="${alt}" src="${url}">`,
  );
  text = text.replace(
    /\[([^\]]+)\]\(([^)\s]+)(?:\s+"[^"]*")?\)/g,
    (_, label, url) => `<a href="${url}" target="_blank" rel="noopener noreferrer">${label}</a>`,
  );

  // Bold, then italic. Strikethrough last.
  text = text.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
  text = text.replace(/__([^_]+)__/g, "<strong>$1</strong>");
  text = text.replace(/\*([^*]+)\*/g, "<em>$1</em>");
  // Underscore italics only at word boundaries, so snake_case is left alone.
  text = text.replace(/(^|[^\w])_([^_]+)_(?!\w)/g, "$1<em>$2</em>");
  text = text.replace(/~~([^~]+)~~/g, "<del>$1</del>");

  // Restore the protected code spans (their contents are already escaped).
  text = text.replace(/\x01CODE(\d+)\x01/g, (_, i) => `<code>${codes[+i]}</code>`);
  return text;
}

// Is this line the start of some block-level construct? Used to know when a
// running paragraph ends.
function isBlockStart(line) {
  return (
    /^\s*$/.test(line) ||                         // blank
    /^(\s*)(`{3,}|~{3,})/.test(line) ||           // fenced code
    /^\s*#{1,6}\s+/.test(line) ||                 // heading
    /^\s*>/.test(line) ||                         // blockquote
    /^\s*([-*_])(\s*\1){2,}\s*$/.test(line) ||    // horizontal rule
    listMatch(line) !== null                       // list item
  );
}

// Recognise a list item; returns its indent, kind, and content (or null).
function listMatch(line) {
  let m = line.match(/^(\s*)([-*+])\s+(.*)$/);
  if (m) return { indent: m[1].length, ordered: false, content: m[3] };
  m = line.match(/^(\s*)(\d+)[.)]\s+(.*)$/);
  if (m) return { indent: m[1].length, ordered: true, start: +m[2], content: m[3] };
  return null;
}

// Matches a table separator row, e.g. "| --- | :--: |".
const TABLE_SEP = /^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$/;

// Split a table row into trimmed cells, dropping the optional outer pipes.
function splitRow(line) {
  let s = line.trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|")) s = s.slice(0, -1);
  return s.split("|").map((c) => c.trim());
}

// Turn a separator cell into a CSS text-align value (or "" for default).
function alignOf(cell) {
  const left = cell.startsWith(":");
  const right = cell.endsWith(":");
  if (left && right) return "center";
  if (right) return "right";
  if (left) return "left";
  return "";
}

// Parse a contiguous list beginning at lines[start]; returns { html, next }.
function parseList(lines, start) {
  const first = listMatch(lines[start]);
  const ordered = first.ordered;
  const base = first.indent;
  const items = []; // each item is an array of (dedented) content lines
  let current = null;
  let i = start;

  while (i < lines.length) {
    const line = lines[i];

    if (/^\s*$/.test(line)) {
      // A blank line stays in the list only if the list visibly continues
      // after it; otherwise it ends the list.
      let j = i + 1;
      while (j < lines.length && /^\s*$/.test(lines[j])) j++;
      if (j < lines.length) {
        const lm = listMatch(lines[j]);
        const indent = lines[j].match(/^(\s*)/)[1].length;
        if ((lm && lm.indent >= base) || indent > base) {
          if (current) current.push("");
          i++;
          continue;
        }
      }
      break;
    }

    const lm = listMatch(line);
    const indent = line.match(/^(\s*)/)[1].length;

    if (lm && lm.indent === base) {
      current = [lm.content];
      items.push(current);
    } else if (indent > base) {
      // Continuation text or a nested list - dedent by the base indent so the
      // recursive parse sees it at column zero (nested markers keep their gap).
      if (current) current.push(line.slice(base));
    } else {
      break;
    }
    i++;
  }

  const tag = ordered ? "ol" : "ul";
  const startAttr = ordered && first.start !== 1 ? ` start="${first.start}"` : "";
  let html = `<${tag}${startAttr}>`;
  for (const item of items) {
    let inner = parseBlocks(item).trim();
    // Tight list: unwrap a lone paragraph so items aren't over-spaced.
    const m = inner.match(/^<p>([\s\S]*)<\/p>$/);
    if (m && !m[1].includes("<p>")) inner = m[1];
    html += `<li>${inner}</li>`;
  }
  html += `</${tag}>`;
  return { html, next: i };
}

// Parse an array of lines into block-level HTML. Recurses for blockquotes and
// list items.
function parseBlocks(lines) {
  let html = "";
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    // Blank line: nothing to emit.
    if (/^\s*$/.test(line)) { i++; continue; }

    // Fenced code block (``` or ~~~). Contents are escaped, never parsed.
    const fence = line.match(/^(\s*)(`{3,}|~{3,})(.*)$/);
    if (fence) {
      const marker = fence[2][0];
      const len = fence[2].length;
      // Any later line that is itself a fence of the same marker, at least as
      // long as the opener. Group 2 is its info string (the language, if any).
      const fenceLine = new RegExp(`^\\s*(${marker}{${len},})\\s*(.*)$`);
      i++;
      const buf = [];
      // Models often show a code block that itself contains fenced code (e.g. a
      // README example). We can't tell an inner closing fence from the outer
      // one by length alone, so we track nesting: a fence carrying an info
      // string opens a nested block, a bare fence closes one. Only a bare fence
      // at the outer level (depth 0) ends this block.
      let depth = 0;
      while (i < lines.length) {
        const fm = lines[i].match(fenceLine);
        if (fm) {
          if (fm[2].trim()) {
            depth++;            // ```sh ... opens a nested block (kept as text)
          } else if (depth > 0) {
            depth--;            // bare ``` closes a nested block (kept as text)
          } else {
            i++;                // bare ``` at the outer level: real close
            break;
          }
        }
        buf.push(lines[i]);
        i++;
      }
      html += `<pre><code>${escapeHtml(buf.join("\n"))}</code></pre>`;
      continue;
    }

    // Heading.
    const heading = line.match(/^\s*(#{1,6})\s+(.*?)\s*#*\s*$/);
    if (heading) {
      const level = heading[1].length;
      html += `<h${level}>${inline(heading[2])}</h${level}>`;
      i++;
      continue;
    }

    // Horizontal rule.
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) {
      html += "<hr>";
      i++;
      continue;
    }

    // Blockquote: gather consecutive ">" lines, strip one level, recurse.
    if (/^\s*>/.test(line)) {
      const buf = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) {
        buf.push(lines[i].replace(/^\s*>\s?/, ""));
        i++;
      }
      html += `<blockquote>${parseBlocks(buf)}</blockquote>`;
      continue;
    }

    // Table: a row with pipes immediately followed by a separator row.
    if (line.includes("|") && i + 1 < lines.length && TABLE_SEP.test(lines[i + 1])) {
      const headers = splitRow(line);
      const aligns = splitRow(lines[i + 1]).map(alignOf);
      i += 2;
      const rows = [];
      while (i < lines.length && lines[i].includes("|") && !/^\s*$/.test(lines[i])) {
        rows.push(splitRow(lines[i]));
        i++;
      }
      const styleFor = (idx) => (aligns[idx] ? ` style="text-align:${aligns[idx]}"` : "");
      let t = "<table><thead><tr>";
      headers.forEach((h, idx) => { t += `<th${styleFor(idx)}>${inline(h)}</th>`; });
      t += "</tr></thead><tbody>";
      for (const row of rows) {
        t += "<tr>";
        headers.forEach((_, idx) => { t += `<td${styleFor(idx)}>${inline(row[idx] || "")}</td>`; });
        t += "</tr>";
      }
      t += "</tbody></table>";
      html += t;
      continue;
    }

    // List (ordered or unordered, possibly nested).
    if (listMatch(line)) {
      const { html: listHtml, next } = parseList(lines, i);
      html += listHtml;
      i = next;
      continue;
    }

    // Paragraph: gather until the next block start; single newlines become <br>
    // so the model's line breaks survive (GitHub-comment style).
    const buf = [line];
    i++;
    while (i < lines.length && !isBlockStart(lines[i])) {
      buf.push(lines[i]);
      i++;
    }
    html += `<p>${inline(buf.join("\n")).replace(/\n/g, "<br>")}</p>`;
  }

  return html;
}

// Render a Markdown string to an HTML string.
export function renderMarkdown(src) {
  if (!src) return "";
  const lines = src.replace(/\r\n?/g, "\n").split("\n");
  return parseBlocks(lines).trim();
}
