/** Mockups drawn to order, rather than bought and imported.
 *
 * A mockup in this studio is a photograph of a room with a flat green
 * rectangle where the artwork goes -- so a generated one asks nothing of the
 * model that models are bad at. What it does ask is that the green be usable,
 * and that is not taken on trust: every picture drawn here is put through the
 * studio's own detector and reported on. One that fails is still shown, with
 * its numbers, because whether to keep it is the admin's call.
 */
import { api, csrfHeaders } from "./api.js";
import { $, toast } from "./dom.js";
import { escapeAttr, escapeHtml } from "./helpers.js";
import { showLightbox } from "./lightbox.js";

const studioState = {
  scenes: [],
  categories: [],
  prompt: "",
  defaultPrompt: "",
  reference: null,
  results: [],
  loaded: false,
};

async function loadEverything() {
  const [scenes, categories] = await Promise.all([
    api("/api/admin/mockups/scenes"),
    api("/api/admin/categories"),
  ]);
  studioState.scenes = scenes.scenes || [];
  studioState.prompt = scenes.prompt || "";
  studioState.defaultPrompt = scenes.default_prompt || "";
  studioState.categories = categories.categories || [];
  studioState.loaded = true;

  const category = $("genCategory");
  if (category) {
    category.innerHTML = studioState.categories
      .map((entry) => `<option value="${entry.id}">${escapeHtml(entry.name)}</option>`)
      .join("");
  }
  const prompt = $("genPrompt");
  if (prompt && !prompt.value) prompt.value = studioState.prompt;
  if (!scenes.enabled) {
    $("genHint").textContent =
      "Vertex AI is off on this server, so nothing can be drawn here yet.";
  }
  renderScenes();
  renderResults();
}

function renderScenes() {
  const row = $("genSceneRow");
  if (!row) return;
  row.innerHTML = studioState.scenes.map((scene) => `
    <div class="listing-style">
      <button class="listing-style-draw" type="button" data-scene="${escapeAttr(scene.key)}">
        <strong>${escapeHtml(scene.name)}</strong>
        <span>${escapeHtml(scene.note || "")}</span>
      </button>
      <button class="listing-style-edit" type="button" data-scene-edit="${escapeAttr(scene.key)}"
              title="Open the wording below to change it first">&#9998;</button>
    </div>
  `).join("");
  row.querySelectorAll("[data-scene]").forEach((button) => {
    button.onclick = () => generate(button, { scene: button.dataset.scene });
  });
  row.querySelectorAll("[data-scene-edit]").forEach((button) => {
    button.onclick = () => {
      $("genPrompt").value = studioState.prompt || studioState.defaultPrompt;
      $("genPrompt").focus();
      toast("The wording is below -- edit it, then Generate");
    };
  });
}

/** What the detector made of one picture, in a line the admin can act on. */
function verdictLine(report) {
  if (report.usable) {
    const frames = report.found_frames;
    const flattest = Math.max(...report.frames.map((frame) => frame.uniformity), 0);
    const notes = (report.warnings || [])
      .map((warning) => `<span class="gen-problem">${escapeHtml(warning)}</span>`)
      .join("");
    return `<span class="gen-verdict ok">Usable &middot; ${frames} ${frames === 1 ? "opening" : "openings"} &middot; flat to ${flattest}</span>${notes}`;
  }
  const problems = (report.problems || []).slice(0, 2)
    .map((problem) => `<span class="gen-problem">${escapeHtml(problem)}</span>`)
    .join("");
  return `<span class="gen-verdict bad">Not usable</span>${problems}`;
}

function renderResults() {
  const grid = $("genResults");
  if (!grid) return;
  if (!studioState.results.length) {
    grid.innerHTML = `<div class="gallery-empty">Nothing drawn yet &mdash; pick a room below</div>`;
    return;
  }
  grid.innerHTML = studioState.results.map((result, index) => `
    <div class="listing-guide-card">
      <img src="${escapeAttr(result.image)}" alt="${escapeAttr(result.name)}"
           title="${escapeAttr(result.name)}" data-open="${index}">
      <div class="gen-card-report">
        ${verdictLine(result.report)}
      </div>
      ${result.kept
        ? `<div class="listing-guide-meta"><strong>${escapeHtml(result.name)}</strong><span>saved as a draft</span></div>`
        : `<button class="btn compact" type="button" data-keep="${index}">Keep anyway</button>`}
    </div>
  `).join("");

  grid.querySelectorAll("[data-open]").forEach((image) => {
    image.onclick = () => {
      const gallery = studioState.results.map((result) => ({
        src: result.image,
        title: result.name,
      }));
      showLightbox(image.getAttribute("src"), image.getAttribute("title"), gallery);
    };
  });
  grid.querySelectorAll("[data-keep]").forEach((button) => {
    button.onclick = () => keepAnyway(Number(button.dataset.keep), button);
  });
}

async function generate(button, { scene = "" } = {}) {
  const label = button.innerHTML;
  button.disabled = true;
  button.classList.add("is-busy");
  try {
    const form = new FormData();
    form.append("category_id", $("genCategory").value);
    form.append("frames", $("genFrames").value);
    form.append("orientation", $("genOrientation").value);
    form.append("ratio", $("genRatio").value);
    // A room is sent as itself; the box below is sent only when it holds a
    // rewrite, so trying a room never overwrites the admin's own wording.
    if (scene) form.append("scene", scene);
    else form.append("prompt", $("genPrompt")?.value || "");
    if (studioState.reference) form.append("reference", studioState.reference);

    const response = await fetch("/api/admin/mockups/generate", {
      method: "POST",
      headers: csrfHeaders(),
      body: form,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "The mockup could not be drawn");
    const name = payload.template?.name
      || studioState.scenes.find((entry) => entry.key === scene)?.name
      || "Generated mockup";
    studioState.results.unshift({
      image: payload.image,
      report: payload.report,
      kept: Boolean(payload.kept),
      name,
      request: {
        category_id: $("genCategory").value,
        frames: $("genFrames").value,
        orientation: $("genOrientation").value,
        ratio: $("genRatio").value,
        scene,
      },
    });
    if (!scene) studioState.prompt = $("genPrompt")?.value || studioState.prompt;
    renderResults();
    toast(payload.kept
      ? "Mockup saved as a draft"
      : "Drawn, but its green cannot be read -- see the report");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.classList.remove("is-busy");
    button.innerHTML = label;
  }
}

/** Draw it again and file it whatever the detector said, if the admin insists. */
async function keepAnyway(index, button) {
  const result = studioState.results[index];
  if (!result) return;
  const label = button.textContent;
  button.disabled = true;
  button.textContent = "Saving...";
  try {
    const form = new FormData();
    Object.entries(result.request).forEach(([key, value]) => {
      if (value) form.append(key, value);
    });
    form.append("force", "1");
    form.append("prompt", $("genPrompt")?.value || "");
    const response = await fetch("/api/admin/mockups/generate", {
      method: "POST",
      headers: csrfHeaders(),
      body: form,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "It could not be saved");
    studioState.results[index] = {
      ...result,
      image: payload.image,
      report: payload.report,
      kept: Boolean(payload.kept),
    };
    renderResults();
    toast("Saved as a draft");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = label;
  }
}

if ($("openMockupStudio")) {
  $("openMockupStudio").onclick = async () => {
    $("mockupStudioModal").classList.add("open");
    if (!studioState.loaded) {
      try {
        await loadEverything();
      } catch (error) {
        toast(error.message);
      }
    }
  };
}
if ($("closeMockupStudio")) {
  $("closeMockupStudio").onclick = () => $("mockupStudioModal").classList.remove("open");
}
if ($("mockupStudioModal")) {
  $("mockupStudioModal").onclick = (event) => {
    if (event.target === $("mockupStudioModal")) $("mockupStudioModal").classList.remove("open");
  };
}
if ($("genGenerate")) {
  $("genGenerate").onclick = () => generate($("genGenerate"));
}
if ($("genPromptReset")) {
  $("genPromptReset").onclick = () => {
    $("genPrompt").value = studioState.defaultPrompt;
  };
}
if ($("genReferenceButton")) {
  $("genReferenceButton").onclick = () => {
    if (studioState.reference) {
      studioState.reference = null;
      $("genReferenceFile").value = "";
      $("genReferenceName").textContent = "";
      return;
    }
    $("genReferenceFile").click();
  };
}
if ($("genReferenceFile")) {
  $("genReferenceFile").onchange = () => {
    const file = $("genReferenceFile").files?.[0] || null;
    studioState.reference = file;
    $("genReferenceName").textContent = file ? `example: ${file.name} (click to clear)` : "";
  };
}
