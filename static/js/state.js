// Shared, mutable application state - the single source of truth that the other
// modules import and update. A plain object so changes are visible everywhere.
export const state = {
  sessions: [],          // Each: {id, title, folder_id, favorite, updated_at, ...}.
  folders: [],           // Each: {id, name, ...}, alphabetical from the API.
  collapsed: new Set(),  // Folder ids the user has collapsed (expanded by default).
  currentSessionId: null,
  models: [],
  catalog: [],          // Downloadable models from /api/catalog (HF-sourced).
  catalogSource: "huggingface",  // "fallback" when HF was unreachable.
  sending: false,
  draggingId: null,      // Id of the chat currently being dragged, or null.
  documents: [],         // Documents attached to the current chat: {id, filename, source, content?}.
  activeDocumentId: null, // Id of the document shown in the side panel, or null.

  // --- Chat pagination (infinite scroll up) ----------------------------------
  oldestSeq: null,       // `seq` of the oldest message currently rendered (scroll cursor).
  hasMoreOlder: false,   // Whether still-older messages exist on the server.
  loadingOlder: false,   // Guard so a single scroll-up doesn't fire many fetches.
  expandedHistory: false, // True once the user scrolled up and loaded older pages;
                          // the next new message collapses back to the latest page.
};
