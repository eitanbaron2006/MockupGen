/** The full-screen preview, shared by every screen that shows results.
 *
 * It holds a gallery rather than a single picture, so whatever opened it can
 * hand over everything on screen and the user can step between them with the
 * arrows or the keyboard without closing it first.
 */
import { $ } from "./dom.js";

const lightboxState = { items: [], index: 0 };

function renderLightboxItem() {
  const img = $("lightboxImage");
  const caption = $("lightboxCaption");
  const counter = $("lightboxCounter");
  const item = lightboxState.items[lightboxState.index];
  if (!img || !item) return;
  img.src = item.src;
  if (caption) caption.textContent = item.title || "Preview";
  const hasGallery = lightboxState.items.length > 1;
  if (counter) {
    counter.classList.toggle("hidden", !hasGallery);
    counter.textContent = hasGallery ? `${lightboxState.index + 1} / ${lightboxState.items.length}` : "";
  }
  if ($("lightboxPrevBtn")) $("lightboxPrevBtn").classList.toggle("hidden", !hasGallery);
  if ($("lightboxNextBtn")) $("lightboxNextBtn").classList.toggle("hidden", !hasGallery);
}

function stepLightbox(delta) {
  const count = lightboxState.items.length;
  if (count < 2) return;
  lightboxState.index = (lightboxState.index + delta + count) % count;
  renderLightboxItem();
}

export function showLightbox(src, title, items) {
  const overlay = $("lightboxOverlay");
  if (!overlay || !$("lightboxImage")) return;
  lightboxState.items = (items && items.length ? items : [{ src, title }]);
  const startIndex = lightboxState.items.findIndex((item) => item.src === src);
  lightboxState.index = startIndex === -1 ? 0 : startIndex;
  renderLightboxItem();
  overlay.classList.remove("hidden");
}

export function hideLightbox() {
  const overlay = $("lightboxOverlay");
  if (overlay) overlay.classList.add("hidden");
}

if ($("closeLightboxBtn")) {
  $("closeLightboxBtn").onclick = hideLightbox;
}
if ($("lightboxPrevBtn")) {
  $("lightboxPrevBtn").onclick = (event) => {
    event.stopPropagation();
    stepLightbox(-1);
  };
}
if ($("lightboxNextBtn")) {
  $("lightboxNextBtn").onclick = (event) => {
    event.stopPropagation();
    stepLightbox(1);
  };
}
if ($("lightboxOverlay")) {
  $("lightboxOverlay").onclick = (event) => {
    if (event.target === $("lightboxOverlay") || event.target === $("closeLightboxBtn")) {
      hideLightbox();
    }
  };
}
document.addEventListener("keydown", (event) => {
  const overlay = $("lightboxOverlay");
  if (!overlay || overlay.classList.contains("hidden")) return;
  if (event.key === "ArrowLeft") {
    event.preventDefault();
    stepLightbox(-1);
  } else if (event.key === "ArrowRight") {
    event.preventDefault();
    stepLightbox(1);
  } else if (event.key === "Escape") {
    event.preventDefault();
    hideLightbox();
  }
});
