/** Every slider on the page, given the same two affordances.
 *
 * A way back to the value it started at, and the wheel to nudge it. One pass
 * over the document does all of them, and repeats over whatever the page grows
 * later -- a second instance of an effect, a rebuilt inspector -- so nothing
 * has to be wired slider by slider.
 */
import { sliderDecimals, sliderStep } from "./helpers.js";

/** Every slider gets a way back to where it started, and answers the wheel.
 *
 * The default is the value the markup ships with -- what the panel shows
 * before anything is touched -- so the button is a way out of an experiment.
 * Both paths set the value and then dispatch input and change, so everything
 * already wired to the slider runs exactly as if it had been dragged: the
 * number beside it, the redraw, the save.
 *
 * Panels are cloned at runtime (a second instance of an effect), and a clone
 * brings a copy of the button with no handler behind it. The link from the
 * button back to its slider is a property rather than an attribute, so a
 * cloned button has none and is swept away on the next pass.
 */
const enhancedSliders = new WeakSet();



function setSliderValue(input, value) {
  if (!Number.isFinite(value)) return false;
  const step = sliderStep(input);
  const min = input.min === "" ? -Infinity : Number(input.min);
  const max = input.max === "" ? Infinity : Number(input.max);
  const base = Number.isFinite(min) ? min : 0;
  // Snapped to the slider's own grid: adding a step of 0.05 ten times
  // otherwise lands on 0.5000000000000001.
  let next = base + Math.round((value - base) / step) * step;
  next = Math.min(max, Math.max(min, next));
  next = Number(next.toFixed(sliderDecimals(step)));
  if (!Number.isFinite(next) || String(next) === String(input.value)) return false;
  input.value = String(next);
  input.dispatchEvent(new Event("input", { bubbles: true }));
  input.dispatchEvent(new Event("change", { bubbles: true }));
  return true;
}

function sliderDefault(input) {
  const attribute = input.getAttribute("value");
  if (attribute !== null && attribute !== "") return Number(attribute);
  const min = Number(input.min);
  return Number.isFinite(min) ? min : 0;
}

function enhanceSlider(input) {
  if (enhancedSliders.has(input)) return;
  enhancedSliders.add(input);

  const fallback = sliderDefault(input);
  const reset = document.createElement("button");
  reset.type = "button";
  reset.className = "slider-reset";
  reset.title = `Reset to ${fallback}`;
  reset.setAttribute("aria-label", `Reset to ${fallback}`);
  // A counter-clockwise arrow drawn on the same 24-box as the other icons in
  // the page: one circle centred in the box, with the tail turning back into
  // the corner it started from.
  reset.innerHTML = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"'
    + ' stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">'
    + '<path d="M3 12a9 9 0 1 0 9-9 9.75 9.75 0 0 0-6.74 2.74L3 8"></path>'
    + '<path d="M3 3v5h5"></path></svg>';
  reset.__sliderInput = input;
  reset.addEventListener("click", (event) => {
    event.preventDefault();
    if (input.disabled) return;
    setSliderValue(input, fallback);
  });

  // Most sliders sit in a row with the number they are showing; the few that
  // do not get a row of their own so the button lands beside them rather
  // than under them.
  const row = input.closest(".slider-container-row, .mask-detect-tolerance");
  if (row) {
    row.appendChild(reset);
  } else {
    const wrap = document.createElement("span");
    wrap.className = "slider-with-reset";
    input.replaceWith(wrap);
    wrap.appendChild(input);
    wrap.appendChild(reset);
  }

  input.addEventListener("wheel", (event) => {
    if (input.disabled) return;
    // The panel behind the pointer must not scroll away mid-adjustment.
    event.preventDefault();
    const delta = event.deltaY !== 0 ? -event.deltaY : event.deltaX;
    if (!delta) return;
    const step = sliderStep(input) * (event.shiftKey ? 10 : 1);
    setSliderValue(input, Number(input.value) + Math.sign(delta) * step);
  }, { passive: false });
}

function enhanceSliders() {
  document.querySelectorAll(".slider-reset").forEach((button) => {
    if (!button.__sliderInput) button.remove();
  });
  document.querySelectorAll('input[type="range"]').forEach(enhanceSlider);
}

/** Do the pass now, and keep doing it as the page grows.
 *
 * Panels arrive later -- a second instance of an effect, a rebuilt inspector --
 * so the sweep repeats on every batch of DOM changes, once per frame.
 */
export function watchSliders() {
  enhanceSliders();
  let sliderSweep = null;
  new MutationObserver(() => {
    if (sliderSweep) cancelAnimationFrame(sliderSweep);
    sliderSweep = requestAnimationFrame(() => {
      sliderSweep = null;
      enhanceSliders();
    });
  }).observe(document.body, { childList: true, subtree: true });
}
