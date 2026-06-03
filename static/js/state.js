// Shared, mutable application state - the single source of truth that the other
// modules import and update. A plain object so changes are visible everywhere.
export const state = {
  sessions: [],          // Each: {id, title, folder_id, favorite, updated_at, ...}.
  folders: [],           // Each: {id, name, ...}, alphabetical from the API.
  collapsed: new Set(),  // Folder ids the user has collapsed (expanded by default).
  currentSessionId: null,
  models: [],
  catalog: [],
  sending: false,
  draggingId: null,      // Id of the chat currently being dragged, or null.
  showToolCalls: false,  // Whether to render tool-call blocks (a display setting).
};
