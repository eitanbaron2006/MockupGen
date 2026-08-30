/** What the studio remembers about how you like to work.
 *
 * Everything here lives in this browser and nowhere else: the width of the
 * sidebar, whether it is pinned open, how the overlay is drawn, where the
 * rails were left. None of it is worth an error if it cannot be read or
 * written -- a browser in private mode, a full quota, storage switched off --
 * so every call answers with the fallback instead of throwing, and the studio
 * opens on its defaults.
 */
export const KEYS = {
  selectionStyle: "mockupStudio.selectionStyle",
  sidebarWidth: "mockupStudio.sidebarWidth",
  sidebarLocked: "mockupStudio.sidebarLocked",
  queueCompact: "mockupStudio.queueCompact",
  greenPanelCollapsed: "mockupStudio.greenPanelCollapsed",
  toolbarPositions: "mockupStudio.canvasToolbarPositions",
};

function read(key) {
  try {
    return localStorage.getItem(key);
  } catch (_error) {
    return null;
  }
}

function write(key, value) {
  try {
    localStorage.setItem(key, value);
    return true;
  } catch (_error) {
    return false;
  }
}

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
  const value = Number(read(key));
  return Number.isFinite(value) && read(key) !== null && read(key) !== "" ? value : fallback;
}

export function writeNumber(key, value) {
  return write(key, String(Math.round(value)));
}
