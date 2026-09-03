/** What the studio remembers about how you like to work.
 *
 * The stored copy lives in the database and is handed down with the page, so
 * the studio opens the same way in a new browser, after a cleared cache, or on
 * another machine. It is read synchronously all over the app while the screen
 * builds itself, which is why it arrives with the HTML rather than being
 * fetched afterwards: a round trip here would mean painting the wrong layout
 * and correcting it a moment later.
 *
 * The browser keeps a copy too, but only as a cache. It is what answers if the
 * page was served without preferences, and it is written first so a reload
 * feels instant -- the database is the copy that counts, and it wins whenever
 * both have something to say.
 *
 * None of this is worth an error: a browser in private mode, a full quota, a
 * server that will not answer. Every call falls back to the default and the
 * studio opens on it.
 */
export const KEYS = {
  selectionStyle: "mockupStudio.selectionStyle",
  sidebarWidth: "mockupStudio.sidebarWidth",
  sidebarLocked: "mockupStudio.sidebarLocked",
  queueCompact: "mockupStudio.queueCompact",
  greenPanelCollapsed: "mockupStudio.greenPanelCollapsed",
  toolbarPositions: "mockupStudio.canvasToolbarPositions",
  collapsedCategories: "mockupStudio.collapsedCategories",
};

const SAVE_DELAY = 700;

/** The stored preferences the page was served with. */
function served() {
  const node = document.getElementById("uiPreferences");
  if (!node) return {};
  try {
    const parsed = JSON.parse(node.textContent || "{}");
    return parsed && typeof parsed === "object" ? parsed : {};
  } catch (_error) {
    return {};
  }
}

/* Seeded from the database, then kept in step with every write. Consulted
   before the browser's copy, so the stored layout wins over a stale cache. */
const known = served();

function cached(key) {
  try {
    return localStorage.getItem(key);
  } catch (_error) {
    return null;
  }
}

function read(key) {
  const stored = known[key];
  if (stored !== undefined && stored !== null) return String(stored);
  return cached(key);
}

/* Changed keys waiting to go to the server, sent together so dragging a
   sidebar is one save rather than one per pixel. */
const pending = new Map();
let saveTimer = null;

function save() {
  saveTimer = null;
  if (!pending.size) return;
  const preferences = Object.fromEntries(pending);
  pending.clear();
  const token = document.querySelector('meta[name="csrf-token"]')?.content || "";
  fetch("/api/admin/preferences", {
    method: "PUT",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": token },
    body: JSON.stringify({ preferences }),
    keepalive: true,
  }).catch(() => {
    /* The browser's copy still holds it; the studio is no worse off than
       before any of this was stored at all. */
  });
}

function write(key, value) {
  known[key] = value;
  let cachedOk = true;
  try {
    localStorage.setItem(key, value);
  } catch (_error) {
    cachedOk = false;
  }
  pending.set(key, value);
  window.clearTimeout(saveTimer);
  saveTimer = window.setTimeout(save, SAVE_DELAY);
  return cachedOk;
}

/* A layout change made just before the tab closes is still worth keeping. */
window.addEventListener("pagehide", () => {
  if (pending.size) {
    window.clearTimeout(saveTimer);
    save();
  }
});

/** A remembered object, or the fallback if there is none worth reading. */
export function readJson(key, fallback = {}) {
  const raw = read(key);
  if (!raw) return fallback;
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === "object" ? parsed : fallback;
  } catch (_error) {
    return fallback;
  }
}

export function writeJson(key, value) {
  return write(key, JSON.stringify(value));
}

export function readBoolean(key, fallback = false) {
  const raw = read(key);
  return raw === null ? fallback : raw === "true";
}

export function writeBoolean(key, value) {
  return write(key, String(Boolean(value)));
}

export function readNumber(key, fallback) {
  const raw = Number.parseFloat(read(key));
  return Number.isFinite(raw) ? raw : fallback;
}

export function writeNumber(key, value) {
  return write(key, String(value));
}
