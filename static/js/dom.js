// Shorthand for document.getElementById.
export const el = (id) => document.getElementById(id);

// Escape a string for safe insertion as text inside HTML.
export function escapeHtml(str) {
  const div = document.createElement("div");
  div.textContent = str;
  return div.innerHTML;
}

// Grow a textarea to fit its content, showing a scrollbar only past the max.
// Keeps overflow hidden (no phantom scrollbar) until the content exceeds the
// max-height. The 220 here must match `.input { max-height }` in the CSS.
export function autoGrow(ta) {
  const MAX = 220;
  ta.style.height = "auto";
  if (ta.scrollHeight > MAX) {
    ta.style.height = MAX + "px";
    ta.style.overflowY = "auto";
  } else {
    ta.style.height = ta.scrollHeight + "px";
    ta.style.overflowY = "hidden";
  }
}
