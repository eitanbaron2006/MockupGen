/** What the studio is holding at any moment.
 *
 * One object per thing that is going on: the workspace itself (categories,
 * templates, what is selected, how the canvas is zoomed and panned), the
 * guided detection wizard while it is running, and the test-mockups modal
 * while it is open. Every module that needs them shares these same objects --
 * they are read and written in place, never replaced.
 *
 * The overlay style sits here too, because it is the one piece of this that
 * outlives the visit: it is read from the browser's storage as the studio
 * opens and written back whenever it changes.
 */
import { clampStyleNumber } from "./helpers.js";
import { KEYS, readJson, writeJson } from "./preferences.js";

export const DEFAULT_SELECTION_STYLE = {
  polygonColor: "#ed6f5c",
  crossColor: "#ed6f5c",
  polygonOpacity: 15,
  crossOpacity: 100,
  polygonWidth: 2,
  crossWidth: 1.5,
  overlayMode: "polygon",
  overlayImage: "",
  overlayImageName: "",
  overlayImageWidth: 0,
  overlayImageHeight: 0
};

export function loadSelectionStylePreference() {
  try {
    const saved = readJson(KEYS.selectionStyle);
    const loaded = {
      polygonColor: typeof saved.polygonColor === "string" ? saved.polygonColor : DEFAULT_SELECTION_STYLE.polygonColor,
      crossColor: typeof saved.crossColor === "string" ? saved.crossColor : DEFAULT_SELECTION_STYLE.crossColor,
      polygonOpacity: clampStyleNumber(saved.polygonOpacity, 0, 100, DEFAULT_SELECTION_STYLE.polygonOpacity),
      crossOpacity: clampStyleNumber(saved.crossOpacity, 0, 100, DEFAULT_SELECTION_STYLE.crossOpacity),
      polygonWidth: clampStyleNumber(saved.polygonWidth, 1, 8, DEFAULT_SELECTION_STYLE.polygonWidth),
      crossWidth: clampStyleNumber(saved.crossWidth, 0.5, 8, DEFAULT_SELECTION_STYLE.crossWidth),
      overlayMode: saved.overlayMode === "image" ? "image" : DEFAULT_SELECTION_STYLE.overlayMode,
      overlayImage: typeof saved.overlayImage === "string" ? saved.overlayImage : "",
      overlayImageName: typeof saved.overlayImageName === "string" ? saved.overlayImageName : "",
      overlayImageWidth: typeof saved.overlayImageWidth === "number" ? saved.overlayImageWidth : 0,
      overlayImageHeight: typeof saved.overlayImageHeight === "number" ? saved.overlayImageHeight : 0
    };

    if (loaded.overlayImage && (!loaded.overlayImageWidth || !loaded.overlayImageHeight)) {
      const img = new Image();
      img.onload = () => {
        state.selectionStyle.overlayImageWidth = img.naturalWidth;
        state.selectionStyle.overlayImageHeight = img.naturalHeight;
        saveSelectionStylePreference();
        drawSelection();
      };
      img.src = loaded.overlayImage;
    }
    return loaded;
  } catch (_error) {
    return { ...DEFAULT_SELECTION_STYLE };
  }
}

export const state = {
  categories: [],
  templates: [],
  selectedCategory: null,
  selected: null,
  settings: {},
  busy: false,
  drag: null,
  pendingDelete: null,
  queueFilter: "all",
  selectedForBatch: new Set(),
  switchingProvider: false,
  selectionStyle: loadSelectionStylePreference(),
  zoom: 1,
  pan: { x: 0, y: 0 },
  isPanning: false,
  canvasLocked: false,
  lockBeforePlacement: false,
  polygonLocked: false,
  polygonLockBeforePlacement: false,
  panStart: { x: 0, y: 0 },
  spacePressed: false,
  lastSelectedTemplateId: null,
  isPreviewingMockup: false,
  wasPreviewingMockup: false,
  globalOverlayPlacementActive: false,
  globalOverlayDrag: null,
  greenFramePlacementActive: false,
  greenFrameDrag: null
};

export const wizardState = {
  active: false,
  step: 1,
  layers: [],
  layerIndex: 0,
  proposedCorners: null,
  clickListener: null
};

export const testState = {
  files: [],
  activeIndex: -1,
  templates: [],
  selectedTemplates: new Set(),
  // Completed renders keyed by artwork+template+engine settings, so
  // re-generating only renders what was not produced yet.
  renderCache: new Map()
};

export function saveSelectionStylePreference() {
  writeJson(KEYS.selectionStyle, state.selectionStyle);
}
