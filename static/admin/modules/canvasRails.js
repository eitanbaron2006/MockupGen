/** The rails that float over the canvas.
 *
 * Three of them -- the overlay style tools, the zoom controls, the mockup
 * actions -- plus the coordinate readout. Each is dragged by the grip at its
 * head, snaps to the wall it is dropped against, remembers where it was left
 * and which way up it was, and merges with another rail docked to the same
 * side wall so the two read as one bar.
 *
 * None of this knows anything about mockups: it is a small window manager over
 * a handful of elements, which is why it comes out of the editor whole.
 */

// Both canvas toolbars are dragged by the grip at their head and remember
// where they were left. Positions are stored per toolbar and clamped to the
// workspace, so a toolbar can never be parked out of reach -- including
// after the window is resized.
const TOOLBAR_POSITION_KEY = "mockupStudio.canvasToolbarPositions";

function readToolbarPositions() {
  try {
    const stored = JSON.parse(localStorage.getItem(TOOLBAR_POSITION_KEY) || "{}");
    return stored && typeof stored === "object" ? stored : {};
  } catch (_error) {
    return {};
  }
}

// Dragged within this many pixels of the workspace edge, a toolbar docks to
// it and stays there when the window is resized.
const TOOLBAR_DOCK_DISTANCE = 40;
// A docked rail sits on the wall of the workspace, not near it.
const TOOLBAR_DOCK_MARGIN = 0;

const canvasToolbars = [];
let aligningToolbars = false;

function toolbarDockSide(toolbar) {
  if (toolbar.classList.contains("canvas-toolbar-docked-left")) return "left";
  if (toolbar.classList.contains("canvas-toolbar-docked-right")) return "right";
  if (toolbar.classList.contains("canvas-toolbar-docked-top")) return "top";
  if (toolbar.classList.contains("canvas-toolbar-docked-bottom")) return "bottom";
  return null;
}

/** A rail standing on end docks to a side wall; one lying flat docks to the
 * top or bottom. Either way it is the long edge that meets the wall.
 */
function toolbarIsHorizontal(toolbar) {
  return toolbar.classList.contains("is-horizontal");
}

/** Toolbars docked to the same edge read as one bar, split by a single rule.
 *
 * They share a width and sit flush against each other, in the order they were
 * left in, with their borders overlapping so the seam is one line rather than
 * two -- the way a docked panel behaves in a drawing application.
 */
function alignDockedToolbars() {
  if (aligningToolbars) return;
  aligningToolbars = true;
  try {
    ["left", "right"].forEach((side) => {
      const members = canvasToolbars
        .map((entry) => entry.element)
        .filter((element) => !element.classList.contains("hidden")
          && !toolbarIsHorizontal(element)
          && toolbarDockSide(element) === side);
      members.forEach((element) => {
        element.classList.remove("canvas-toolbar-merged", "canvas-toolbar-merged-first", "canvas-toolbar-merged-last");
        element.style.width = "";
      });
      if (members.length < 2) return;
      const parent = members[0].offsetParent || members[0].parentElement;
      if (!parent) return;
      const bounds = parent.getBoundingClientRect();
      members.sort((a, b) => a.getBoundingClientRect().top - b.getBoundingClientRect().top);
      const width = Math.max(...members.map((element) => element.offsetWidth));
      members.forEach((element) => {
        element.style.width = `${width}px`;
      });
      const stackHeight = members.reduce((total, element) => total + element.offsetHeight, 0) - (members.length - 1);
      const left = side === "left"
        ? TOOLBAR_DOCK_MARGIN
        : Math.max(0, bounds.width - width - TOOLBAR_DOCK_MARGIN);
      let top = Math.min(
        Math.max(0, members[0].getBoundingClientRect().top - bounds.top),
        Math.max(0, bounds.height - stackHeight)
      );
      members.forEach((element, index) => {
        element.style.left = `${Math.round(left)}px`;
        element.style.top = `${Math.round(top)}px`;
        element.classList.add("canvas-toolbar-merged");
        if (index === 0) element.classList.add("canvas-toolbar-merged-first");
        if (index === members.length - 1) element.classList.add("canvas-toolbar-merged-last");
        // Overlap the 1px borders so the two toolbars meet on one line.
        top += element.offsetHeight - 1;
      });
    });
  } finally {
    aligningToolbars = false;
  }
}

function rememberToolbarPositions(parent) {
  if (!parent) return;
  const bounds = parent.getBoundingClientRect();
  const positions = readToolbarPositions();
  canvasToolbars.forEach((entry) => {
    if (entry.element.classList.contains("hidden")) return;
    const box = entry.element.getBoundingClientRect();
    positions[entry.key] = {
      x: box.left - bounds.left,
      y: box.top - bounds.top,
      dock: toolbarDockSide(entry.element),
      horizontal: toolbarIsHorizontal(entry.element),
    };
  });
  try {
    localStorage.setItem(TOOLBAR_POSITION_KEY, JSON.stringify(positions));
  } catch (_error) {
    // Remembering where a toolbar sits is a convenience, not a requirement.
  }
}

export function makeToolbarDraggable(toolbar, key) {
  if (!toolbar) return;
  const handle = toolbar.querySelector("[data-drag-handle]");
  if (!handle) return;

  let drag = null;

  const setDock = (side) => {
    toolbar.classList.toggle("canvas-toolbar-docked-left", side === "left");
    toolbar.classList.toggle("canvas-toolbar-docked-right", side === "right");
    toolbar.classList.toggle("canvas-toolbar-docked-top", side === "top");
    toolbar.classList.toggle("canvas-toolbar-docked-bottom", side === "bottom");
  };

  const place = (left, top, { snap = true } = {}) => {
    const parent = toolbar.offsetParent || toolbar.parentElement;
    if (!parent || !toolbar.offsetWidth) return null;
    const bounds = parent.getBoundingClientRect();
    const maxLeft = Math.max(0, bounds.width - toolbar.offsetWidth);
    const maxTop = Math.max(0, bounds.height - toolbar.offsetHeight);
    let x = Math.min(Math.max(0, left), maxLeft);
    let y = Math.min(Math.max(0, top), maxTop);
    let dock = null;
    if (snap && toolbarIsHorizontal(toolbar)) {
      if (y <= TOOLBAR_DOCK_DISTANCE) {
        dock = "top";
        y = Math.min(TOOLBAR_DOCK_MARGIN, maxTop);
      } else if (y >= maxTop - TOOLBAR_DOCK_DISTANCE) {
        dock = "bottom";
        y = Math.max(0, maxTop - TOOLBAR_DOCK_MARGIN);
      }
    } else if (snap && x <= TOOLBAR_DOCK_DISTANCE) {
      dock = "left";
      x = Math.min(TOOLBAR_DOCK_MARGIN, maxLeft);
    } else if (snap && x >= maxLeft - TOOLBAR_DOCK_DISTANCE) {
      dock = "right";
      x = Math.max(0, maxLeft - TOOLBAR_DOCK_MARGIN);
    }
    setDock(dock);
    toolbar.style.left = `${Math.round(x)}px`;
    toolbar.style.top = `${Math.round(y)}px`;
    toolbar.style.right = "auto";
    toolbar.style.bottom = "auto";
    return { x, y, dock };
  };

  const restore = () => {
    // Dragging toggles classes of its own, and the observer below watches for
    // exactly that; putting the toolbar back mid-drag would fight the hand.
    if (drag || aligningToolbars) return;
    const saved = readToolbarPositions()[key];
    if (!saved || toolbar.classList.contains("hidden")) return;
    // The way it was left lying is part of where it was left.
    toolbar.classList.toggle("is-horizontal", Boolean(saved.horizontal));
    // A docked toolbar is measured from the edge it is docked to, so it stays
    // on that edge whatever the workspace is resized to.
    if (saved.dock === "right" || saved.dock === "bottom") {
      const parent = toolbar.offsetParent || toolbar.parentElement;
      if (parent) {
        const bounds = parent.getBoundingClientRect();
        place(
          saved.dock === "right" ? bounds.width - toolbar.offsetWidth - TOOLBAR_DOCK_MARGIN : saved.x,
          saved.dock === "bottom" ? bounds.height - toolbar.offsetHeight - TOOLBAR_DOCK_MARGIN : saved.y
        );
        alignDockedToolbars();
        return;
      }
    }
    place(saved.x, saved.y);
    alignDockedToolbars();
  };

  handle.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    const parent = toolbar.offsetParent || toolbar.parentElement;
    if (!parent) return;
    event.preventDefault();
    const bounds = parent.getBoundingClientRect();
    const box = toolbar.getBoundingClientRect();
    // Rails merged into one bar are dragged as one *along the wall*: the
    // others keep their offset from this one for as long as the drag stays
    // docked. Pulling away from the wall is how one is taken out of the bar.
    const companions = toolbar.classList.contains("canvas-toolbar-merged")
      ? canvasToolbars
        .map((entry) => entry.element)
        .filter((element) => element !== toolbar
          && !element.classList.contains("hidden")
          && element.classList.contains("canvas-toolbar-merged"))
        .map((element) => {
          const other = element.getBoundingClientRect();
          return { element, dx: other.left - box.left, dy: other.top - box.top };
        })
      : [];
    drag = {
      pointerId: event.pointerId,
      offsetX: event.clientX - box.left,
      offsetY: event.clientY - box.top,
      parentLeft: bounds.left,
      parentTop: bounds.top,
      companions,
      dockedFrom: toolbarDockSide(toolbar),
    };
    handle.setPointerCapture(event.pointerId);
    toolbar.classList.add("canvas-toolbar-dragging");
  });

  /** Stand the rail on end, or lay it flat.
   *
   * On the head that already moves it -- double-click, or Enter on the
   * keyboard -- so a rail carries its own controls and the bar grows no
   * buttons that are not tools. Turning re-places it: a rail that was flush
   * against the left wall standing up belongs against the top wall lying
   * down, and it must be re-snapped rather than left hanging off the edge.
   */
  const turnToolbar = () => {
    if (drag) return;
    const wasDocked = toolbarDockSide(toolbar);
    toolbar.classList.toggle("is-horizontal");
    toolbar.classList.remove("canvas-toolbar-merged", "canvas-toolbar-merged-first", "canvas-toolbar-merged-last");
    toolbar.style.width = "";
    const parent = toolbar.offsetParent || toolbar.parentElement;
    if (!parent) return;
    const bounds = parent.getBoundingClientRect();
    const box = toolbar.getBoundingClientRect();
    // The corner it was in is the corner it stays in.
    let left = box.left - bounds.left;
    let top = box.top - bounds.top;
    if (toolbarIsHorizontal(toolbar) && (wasDocked === "left" || wasDocked === "right")) {
      top = top <= bounds.height / 2 ? 0 : bounds.height;
    } else if (!toolbarIsHorizontal(toolbar) && (wasDocked === "top" || wasDocked === "bottom")) {
      left = left <= bounds.width / 2 ? 0 : bounds.width;
    }
    place(left, top);
    clearOfOtherRails();
    alignDockedToolbars();
    rememberToolbarPositions(parent);
  };

  /** A rail that has just been turned is a different shape in the same
   * corner, and the corner may already be taken -- a flat style rail is as
   * wide as the coordinates beside it. Push it along its own length until it
   * is standing clear, or leave it be if there is nowhere to go.
   */
  const clearOfOtherRails = () => {
    const parent = toolbar.offsetParent || toolbar.parentElement;
    if (!parent) return;
    const bounds = parent.getBoundingClientRect();
    const horizontal = toolbarIsHorizontal(toolbar);
    const others = canvasToolbars
      .map((entry) => entry.element)
      .filter((element) => element !== toolbar && !element.classList.contains("hidden"))
      .map((element) => element.getBoundingClientRect());
    let box = toolbar.getBoundingClientRect();
    for (let guard = 0; guard < 8; guard += 1) {
      const hit = others.find((other) => box.left < other.right && other.left < box.right
        && box.top < other.bottom && other.top < box.bottom);
      if (!hit) return;
      if (horizontal) place(hit.right - bounds.left, box.top - bounds.top);
      else place(box.left - bounds.left, hit.bottom - bounds.top);
      const moved = toolbar.getBoundingClientRect();
      if (moved.left === box.left && moved.top === box.top) return;
      box = moved;
    }
  };

  handle.addEventListener("dblclick", (event) => {
    event.preventDefault();
    turnToolbar();
  });

  handle.addEventListener("keydown", (event) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    event.preventDefault();
    turnToolbar();
  });

  const leaveTheBar = () => {
    toolbar.classList.remove("canvas-toolbar-merged", "canvas-toolbar-merged-first", "canvas-toolbar-merged-last");
    toolbar.style.width = "";
  };

  handle.addEventListener("pointermove", (event) => {
    if (!drag || event.pointerId !== drag.pointerId) return;
    if (!drag.companions.length) leaveTheBar();
    const placed = place(
      event.clientX - drag.parentLeft - drag.offsetX,
      event.clientY - drag.parentTop - drag.offsetY
    );
    if (!placed) return;
    if (drag.companions.length && placed.dock !== drag.dockedFrom) {
      // Off the wall: this rail comes out of the bar and the rest stay put.
      drag.companions = [];
      leaveTheBar();
      alignDockedToolbars();
      return;
    }
    drag.companions.forEach(({ element, dx, dy }) => {
      element.style.left = `${Math.round(placed.x + dx)}px`;
      element.style.top = `${Math.round(placed.y + dy)}px`;
      element.style.right = "auto";
      element.style.bottom = "auto";
      element.classList.toggle("canvas-toolbar-docked-left", placed.dock === "left");
      element.classList.toggle("canvas-toolbar-docked-right", placed.dock === "right");
    });
  });

  const endDragging = (event) => {
    if (!drag || (event && event.pointerId !== drag.pointerId)) return;
    drag = null;
    toolbar.classList.remove("canvas-toolbar-dragging");
    const parent = toolbar.offsetParent || toolbar.parentElement;
    if (!parent) return;
    alignDockedToolbars();
    rememberToolbarPositions(parent);
  };
  handle.addEventListener("pointerup", endDragging);
  handle.addEventListener("pointercancel", endDragging);

  // A hidden toolbar has no size to clamp against, so wait until it is shown.
  // Only a change in visibility is worth reacting to. Watching every class
  // change would see the ones dragging and docking make, put the toolbar back
  // where it was stored, and chase its own tail.
  let wasHidden = toolbar.classList.contains("hidden");
  new MutationObserver(() => {
    const hidden = toolbar.classList.contains("hidden");
    if (hidden === wasHidden) return;
    wasHidden = hidden;
    if (!hidden) restore();
    else alignDockedToolbars();
  }).observe(toolbar, { attributes: true, attributeFilter: ["class"] });
  window.addEventListener("resize", restore);
  // The workspace also changes width on its own, with the window standing
  // still: the queue shrinking to thumbnails, the sidebar taking its column
  // back. A rail docked to an edge has to follow that edge, or the canvas
  // grows out from under it and leaves it standing in the middle.
  const dockParent = toolbar.offsetParent || toolbar.parentElement;
  if (dockParent && typeof ResizeObserver === "function") {
    let pendingResize = null;
    new ResizeObserver(() => {
      if (pendingResize) cancelAnimationFrame(pendingResize);
      pendingResize = requestAnimationFrame(() => {
        pendingResize = null;
        restore();
      });
    }).observe(dockParent);
  }
  canvasToolbars.push({ element: toolbar, key });
  restore();
}
