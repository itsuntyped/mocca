// Inline SVG icons used in the sidebar, returned as strings for innerHTML.
export function starSvg() {
  return `<svg viewBox="0 0 24 24" width="15" height="15"><path d="M12 2.6l2.9 5.9 6.5.95-4.7 4.58 1.1 6.47L12 17.9l-5.8 3.06 1.1-6.47-4.7-4.58 6.5-.95z"/></svg>`;
}

export function chevronSvg(collapsed) {
  return `<svg viewBox="0 0 24 24" width="14" height="14" class="chevron ${collapsed ? "collapsed" : ""}"
    fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="6 9 12 15 18 9" /></svg>`;
}

export function pencilSvg() {
  return `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <path d="M12 20h9" /><path d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4z" /></svg>`;
}

export function trashSvg() {
  return `<svg viewBox="0 0 24 24" width="15" height="15" fill="none" stroke="currentColor"
    stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
    <polyline points="3 6 5 6 21 6" /><path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    <line x1="10" y1="11" x2="10" y2="17" /><line x1="14" y1="11" x2="14" y2="17" />
    <path d="M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2" /></svg>`;
}
