/** The Listing sets screen: what a shop listing is made of, decided in advance.
 *
 * Automatic selection scores how well a frame fits the artwork and nothing
 * else -- it cannot know that the MAIN categories hold the picture Etsy shows
 * in search, so left alone it will spend one on a filler slot. Here the admin
 * says once, per product type, which mockups a listing gets.
 *
 * A set is three things, and their order is not one of them: the main image
 * (one MAIN mockup), the mockups (up to 18), and the size guide. Which slot is
 * active decides what the picker offers -- MAIN mockups for the main image,
 * everything else for the rest -- so the rule holds by construction rather
 * than by refusing clicks after the fact.
 */
import { api, csrfHeaders } from "./api.js";
import { $, systemConfirm, toast } from "./dom.js";
import { escapeAttr, escapeHtml } from "./helpers.js";
import { showLightbox } from "./lightbox.js";

const MAX_MOCKUPS = 18;

const listingState = {
  sets: [],
  templates: [],
  categories: [],
  guides: [],
  ratios: [],
  prompt: "",
  defaultPrompt: "",
  presets: [],
  reference: null,
  productTypes: [],
  draft: null,
  slot: "hero",
  loaded: false,
};

function isMainCategoryName(name) {
  return String(name || "").trim().toLowerCase().startsWith("main");
}

function templateById(templateId) {
  return listingState.templates.find((entry) => entry.template_id === templateId) || null;
}

function previewUrl(template) {
  return template.preview_url || `/api/admin/templates/${template.template_id}/asset/preview.png`;
}

function guideById(guideId) {
  return listingState.guides.find((guide) => guide.id === guideId) || null;
}

// ---------------------------------------------------------------- loading

async function loadEverything() {
  const [sets, templates, categories, guides] = await Promise.all([
    api("/api/admin/listing-sets"),
    api("/api/admin/templates?status=active"),
    api("/api/admin/categories"),
    api("/api/admin/size-guides"),
  ]);
  listingState.sets = sets.sets || [];
  listingState.productTypes = sets.product_types || [];
  listingState.templates = templates.templates || [];
  listingState.categories = categories.categories || [];
  listingState.guides = guides.guides || [];
  listingState.ratios = guides.ratios || [];
  listingState.prompt = guides.prompt || "";
  listingState.defaultPrompt = guides.default_prompt || "";
  listingState.presets = guides.presets || [];
  listingState.loaded = true;
  renderChoices();
  renderSetList();
  renderEditor();
  renderGuides();
  renderStyles();
}

function renderChoices() {
  const product = $("listingSetProduct");
  if (product) {
    product.innerHTML = ['<option value="">All product types</option>']
      .concat(listingState.productTypes.map((name) =>
        `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`))
      .join("");
    product.value = listingState.draft?.product_type || "";
  }
  const picker = $("listingPickerCategory");
  if (picker) {
    const current = picker.value;
    picker.innerHTML = ['<option value="">All categories</option>']
      .concat(listingState.categories.map((category) =>
        `<option value="${category.id}">${escapeHtml(category.name)}</option>`))
      .join("");
    picker.value = current;
  }
  [$("listingGuideRatio"), $("listingSlotRatio")].forEach((ratio) => {
    if (!ratio) return;
    const current = ratio.value;
    ratio.innerHTML = listingState.ratios
      .map((name) => `<option value="${escapeAttr(name)}">${escapeHtml(name)}</option>`)
      .join("");
    if (current) ratio.value = current;
  });
  const prompt = $("listingGuidePrompt");
  if (prompt && !prompt.value) prompt.value = listingState.prompt;
}

/** The ratio a set's own shape implies, as the slot's starting point. */
function ratioForShape(orientation) {
  if (orientation === "landscape") return "3:2";
  if (orientation === "square") return "1:1";
  return "2:3";
}

// ------------------------------------------------------------- the set list

function renderSetList() {
  const list = $("listingSetList");
  if (!list) return;
  if (!listingState.sets.length) {
    list.innerHTML = `<div class="gallery-empty">No sets yet</div>`;
    return;
  }
  list.innerHTML = listingState.sets.map((entry) => {
    const active = listingState.draft && listingState.draft.id === entry.id ? " active" : "";
    const shape = entry.orientation === "any" ? "any shape" : entry.orientation;
    const pictures = (entry.items || []).filter((item) => item.kind === "mockup").length;
    return `
      <div class="listing-set-row${active}">
        <button class="listing-set-open" type="button" data-set="${entry.id}"
                title="${escapeAttr(`${entry.name} -- ${entry.product_type || "all product types"}, ${shape}`)}">
          <span class="listing-set-name">${escapeHtml(entry.name)}</span>
          <span class="listing-set-meta">${pictures} mockups</span>
        </button>
        <button class="listing-set-drop" type="button" data-drop-set="${entry.id}"
                title="Delete this set" aria-label="Delete ${escapeAttr(entry.name)}">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8"
               stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
            <path d="M3 6h18"></path>
            <path d="M8 6V4h8v2"></path>
            <path d="M19 6l-1 14H6L5 6"></path>
            <path d="M10 11v6M14 11v6"></path>
          </svg>
        </button>
      </div>
    `;
  }).join("");
  list.querySelectorAll("[data-set]").forEach((row) => {
    row.onclick = () => editSet(Number(row.dataset.set));
  });
  list.querySelectorAll("[data-drop-set]").forEach((button) => {
    button.onclick = (event) => {
      // The row underneath opens the set; the bin must not open it as well.
      event.stopPropagation();
      deleteSet(Number(button.dataset.dropSet));
    };
  });
}

// ---------------------------------------------------------------- the draft

/** The saved shape (a flat item list) as the three slots the screen edits. */
function draftFromSet(entry) {
  const items = entry.items || [];
  const hero = items.find((item) => item.kind === "mockup" && item.hero);
  const guide = items.find((item) => item.kind === "size_guide");
  return {
    id: entry.id ?? null,
    name: entry.name || "",
    product_type: entry.product_type || "",
    orientation: entry.orientation || "any",
    hero: hero?.template_id || "",
    mockups: items
      .filter((item) => item.kind === "mockup" && !item.hero && item.template_id)
      .map((item) => item.template_id),
    guide: guide ? (guide.guide_id ?? null) : undefined,
  };
}

/** ...and back again, which is the only shape the server knows. */
function draftToItems(draft) {
  const items = [];
  if (draft.hero) items.push({ kind: "mockup", hero: true, template_id: draft.hero });
  draft.mockups.forEach((templateId) => {
    items.push({ kind: "mockup", hero: false, template_id: templateId });
  });
  if (draft.guide !== undefined) {
    items.push(draft.guide ? { kind: "size_guide", guide_id: draft.guide } : { kind: "size_guide" });
  }
  return items;
}

function editSet(setId) {
  const found = listingState.sets.find((entry) => entry.id === setId);
  if (!found) return;
  listingState.draft = draftFromSet(found);
  listingState.slot = "hero";
  renderSetList();
  renderEditor();
  // Opening a set shows it from the top, not wherever the last one was left.
  document.querySelector(".listing-slots")?.scrollTo({ top: 0 });
}

function newSet() {
  listingState.draft = {
    id: null,
    name: "",
    product_type: "",
    orientation: "any",
    hero: "",
    mockups: [],
    guide: null,
  };
  listingState.slot = "hero";
  renderSetList();
  renderEditor();
  $("listingSetName")?.focus();
}

// --------------------------------------------------------------- the slots

function chosenCard(templateId, action) {
  const template = templateById(templateId);
  const name = template?.name || templateId;
  return `
    <figure class="listing-chosen">
      <img src="${escapeAttr(template ? previewUrl(template) : "")}" alt="${escapeAttr(name)}" loading="lazy">
      <figcaption>${escapeHtml(name)}</figcaption>
      <button class="listing-chosen-drop" type="button" data-${action}="${escapeAttr(templateId)}"
              title="Remove">&times;</button>
    </figure>
  `;
}

function renderEditor() {
  const editor = $("listingEditor");
  const hint = $("listingEditorHint");
  const draft = listingState.draft;
  if (!editor) return;
  editor.classList.toggle("hidden", !draft);
  if (hint) hint.classList.toggle("hidden", Boolean(draft));
  if (!draft) {
    renderPicker();
    return;
  }

  $("listingSetName").value = draft.name || "";
  $("listingSetProduct").value = draft.product_type || "";
  $("listingSetOrientation").value = draft.orientation || "any";

  $("listingHeroBody").innerHTML = draft.hero
    ? chosenCard(draft.hero, "drop-hero")
    : `<p class="listing-slot-empty">Pick a MAIN mockup</p>`;

  $("listingMockupsBody").innerHTML = draft.mockups.length
    ? draft.mockups.map((templateId) => chosenCard(templateId, "drop-mockup")).join("")
    : `<p class="listing-slot-empty">Pick from the right</p>`;
  $("listingMockupCount").textContent = `${draft.mockups.length} of ${MAX_MOCKUPS}`;

  if (draft.guide === undefined) {
    $("listingGuideBody").innerHTML = `<p class="listing-slot-empty">No chart</p>`;
  } else if (draft.guide) {
    const guide = guideById(draft.guide);
    $("listingGuideBody").innerHTML = `
      <figure class="listing-chosen">
        <img src="/api/admin/size-guides/${draft.guide}/asset" alt="${escapeAttr(guide?.name || "")}" loading="lazy">
        <figcaption>${escapeHtml(guide?.ratio || "")}</figcaption>
        <button class="listing-chosen-drop" type="button" data-drop-guide="1" title="Remove">&times;</button>
      </figure>`;
  } else {
    $("listingGuideBody").innerHTML = `
      <figure class="listing-chosen listing-chosen-auto">
        <span class="listing-guide-auto">Matched to the artwork</span>
        <figcaption>Chosen by ratio</figcaption>
        <button class="listing-chosen-drop" type="button" data-drop-guide="1" title="Remove">&times;</button>
      </figure>`;
  }

  editor.querySelectorAll("[data-drop-hero]").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      draft.hero = "";
      renderEditor();
    };
  });
  editor.querySelectorAll("[data-drop-mockup]").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      draft.mockups = draft.mockups.filter((id) => id !== button.dataset.dropMockup);
      renderEditor();
    };
  });
  editor.querySelectorAll("[data-drop-guide]").forEach((button) => {
    button.onclick = (event) => {
      event.stopPropagation();
      draft.guide = undefined;
      renderEditor();
    };
  });

  const SLOT_HINTS = {
    hero: "The main image is the picture Etsy shows in search, so only a MAIN mockup can be it.",
    mockups: `Up to ${MAX_MOCKUPS} more pictures, in the order you pick them.`,
    guide: "Left as matched, the chart is chosen by the artwork's ratio.",
  };
  [["hero", "listingSlotHero"], ["mockups", "listingSlotMockups"], ["guide", "listingSlotGuide"]]
    .forEach(([slot, id]) => {
      const section = $(id);
      if (!section) return;
      section.classList.toggle("active", listingState.slot === slot);
      section.onclick = () => {
        if (listingState.slot === slot) return;
        listingState.slot = slot;
        renderEditor();
      };
    });
  const slotRatio = $("listingSlotRatio");
  if (slotRatio && !slotRatio.dataset.touched) {
    slotRatio.value = ratioForShape(draft.orientation);
  }
  const slotHint = $("listingSlotHint");
  if (slotHint) slotHint.textContent = SLOT_HINTS[listingState.slot] || "";
  renderPicker();
}

// ---------------------------------------------------------------- the picker

function renderPicker() {
  const gallery = $("listingPickerGallery");
  const title = $("listingPickerTitle");
  const filters = document.querySelector(".listing-picker-filters");
  if (!gallery) return;
  const draft = listingState.draft;
  const slot = listingState.slot;
  if (title) {
    title.textContent = !draft
      ? "Mockups"
      : slot === "hero" ? "MAIN mockups" : slot === "guide" ? "Size guides" : "Mockups";
  }
  if (filters) filters.classList.toggle("hidden", !draft || slot === "guide");
  if (!draft) {
    gallery.innerHTML = `<div class="gallery-empty">Pick a set to fill</div>`;
    return;
  }

  if (slot === "guide") {
    gallery.innerHTML = [`
      <button class="listing-picker-card${draft.guide === null ? " active" : ""}" type="button" data-guide="auto">
        <span class="listing-guide-auto">Matched to the artwork</span>
        <span class="listing-picker-name">Chosen by ratio</span>
      </button>
    `].concat(listingState.guides.map((guide) => `
      <button class="listing-picker-card${draft.guide === guide.id ? " active" : ""}" type="button"
              data-guide="${guide.id}" title="${escapeAttr(guide.name)}">
        <img src="/api/admin/size-guides/${guide.id}/asset" alt="${escapeAttr(guide.name)}" loading="lazy">
        <span class="listing-picker-name">${escapeHtml(guide.ratio)}</span>
      </button>
    `)).join("");
    gallery.querySelectorAll("[data-guide]").forEach((card) => {
      card.onclick = () => {
        draft.guide = card.dataset.guide === "auto" ? null : Number(card.dataset.guide);
        renderEditor();
      };
    });
    return;
  }

  // The slot decides what may fill it: a MAIN mockup is the listing's main
  // image and nothing else, so it is the only thing the main slot offers and
  // the one thing the rest never sees.
  const wantMain = slot === "hero";
  const categoryId = Number($("listingPickerCategory")?.value || 0);
  const orientation = $("listingPickerOrientation")?.value || "";
  const templates = listingState.templates.filter((template) => {
    if (isMainCategoryName(template.category_name) !== wantMain) return false;
    if (categoryId && Number(template.category_id) !== categoryId) return false;
    if (orientation && template.orientation !== orientation) return false;
    return true;
  });

  if (!templates.length) {
    gallery.innerHTML = `<div class="gallery-empty">No ${wantMain ? "MAIN " : ""}mockups match this filter</div>`;
    return;
  }
  gallery.innerHTML = templates.map((template) => {
    const chosen = wantMain
      ? draft.hero === template.template_id
      : draft.mockups.includes(template.template_id);
    return `
      <button class="listing-picker-card${chosen ? " active" : ""}" type="button"
              data-template="${escapeAttr(template.template_id)}" title="${escapeAttr(template.name)}">
        <img src="${escapeAttr(previewUrl(template))}" alt="${escapeAttr(template.name)}" loading="lazy">
        <span class="listing-picker-name">${escapeHtml(template.name)}</span>
      </button>
    `;
  }).join("");
  gallery.querySelectorAll("[data-template]").forEach((card) => {
    card.onclick = () => chooseTemplate(card.dataset.template);
  });
}

function chooseTemplate(templateId) {
  const draft = listingState.draft;
  if (!draft) return;
  if (listingState.slot === "hero") {
    draft.hero = draft.hero === templateId ? "" : templateId;
    renderEditor();
    return;
  }
  if (draft.mockups.includes(templateId)) {
    draft.mockups = draft.mockups.filter((id) => id !== templateId);
  } else if (draft.mockups.length >= MAX_MOCKUPS) {
    toast(`A listing takes at most ${MAX_MOCKUPS} mockups beside the main image`);
    return;
  } else {
    draft.mockups.push(templateId);
  }
  renderEditor();
}

// ----------------------------------------------------------------- saving

async function saveSet() {
  const draft = listingState.draft;
  if (!draft) return;
  if (!draft.name.trim()) {
    toast("A set needs a name");
    $("listingSetName")?.focus();
    return;
  }
  const payload = {
    name: draft.name.trim(),
    product_type: draft.product_type || null,
    orientation: draft.orientation || "any",
    items: draftToItems(draft),
  };
  const button = $("listingSaveSet");
  button.disabled = true;
  try {
    const answer = draft.id
      ? await api(`/api/admin/listing-sets/${draft.id}`, { method: "PATCH", body: JSON.stringify(payload) })
      : await api("/api/admin/listing-sets", { method: "POST", body: JSON.stringify(payload) });
    const existing = listingState.sets.findIndex((entry) => entry.id === answer.set.id);
    if (existing >= 0) listingState.sets[existing] = answer.set;
    else listingState.sets.push(answer.set);
    listingState.sets.sort((a, b) => a.name.localeCompare(b.name));
    listingState.draft = draftFromSet(answer.set);
    renderSetList();
    renderEditor();
    toast("Set saved");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
  }
}

async function deleteSet(setId) {
  const entry = listingState.sets.find((saved) => saved.id === setId);
  if (!entry) return;
  // A set is a decision the admin made once and builds every listing from, so
  // it is never deleted on one click.
  const confirmed = await systemConfirm(
    `Delete "${entry.name}"?`,
    "The listings already built from it are not affected, but the set itself cannot be brought back."
  );
  if (!confirmed) return;
  try {
    await api(`/api/admin/listing-sets/${setId}`, { method: "DELETE" });
    listingState.sets = listingState.sets.filter((saved) => saved.id !== setId);
    if (listingState.draft?.id === setId) listingState.draft = null;
    renderSetList();
    renderEditor();
    toast("Set deleted");
  } catch (error) {
    toast(error.message);
  }
}

// ------------------------------------------------------------ size guides

function renderGuides() {
  const grid = $("listingGuideGrid");
  if (!grid) return;
  if (!listingState.guides.length) {
    grid.innerHTML = `<div class="gallery-empty">No size guides yet &mdash; pick a style below</div>`;
    return;
  }
  grid.innerHTML = listingState.guides.map((guide) => `
    <div class="listing-guide-card">
      <img src="/api/admin/size-guides/${guide.id}/asset" alt="${escapeAttr(guide.name)}"
           title="${escapeAttr(guide.name)}" loading="lazy" data-open="${guide.id}"
           data-missing="Its image file is gone">
      <div class="listing-guide-meta">
        <strong>${escapeHtml(guide.ratio)}</strong>
        <span>${escapeHtml(guide.source === "ai" ? "generated" : "uploaded")}</span>
      </div>
      <button class="icon-button" type="button" data-drop="${guide.id}" title="Delete">&times;</button>
    </div>
  `).join("");
  // A chart whose file has gone is said so, rather than shown as a broken
  // image the admin has to guess at.
  grid.querySelectorAll("[data-missing]").forEach((image) => {
    image.onerror = () => {
      const card = image.closest(".listing-guide-card");
      image.remove();
      if (card) card.insertAdjacentHTML(
        "afterbegin",
        `<p class="listing-guide-lost">${escapeHtml(image.dataset.missing)}</p>`
      );
    };
  });
  // A chart is judged full size or not at all: the labels are the point of it.
  grid.querySelectorAll("[data-open]").forEach((image) => {
    image.onclick = () => {
      const gallery = listingState.guides.map((guide) => ({
        src: `/api/admin/size-guides/${guide.id}/asset`,
        title: `${guide.name} -- ${guide.ratio}`,
      }));
      showLightbox(image.getAttribute("src"), image.getAttribute("title"), gallery);
    };
  });
  grid.querySelectorAll("[data-drop]").forEach((button) => {
    button.onclick = async () => {
      const id = Number(button.dataset.drop);
      if (!(await systemConfirm("Delete this size guide?", guideById(id)?.name || ""))) return;
      try {
        await api(`/api/admin/size-guides/${id}`, { method: "DELETE" });
        listingState.guides = listingState.guides.filter((entry) => entry.id !== id);
        if (listingState.draft?.guide === id) listingState.draft.guide = null;
        renderGuides();
        renderEditor();
      } catch (error) {
        toast(error.message);
      }
    };
  });
}

/** The styles a chart can be drawn in: one click draws, the pencil edits. */
function renderStyles() {
  const row = $("listingStyleRow");
  if (!row) return;
  row.innerHTML = listingState.presets.map((preset) => `
    <div class="listing-style">
      <button class="listing-style-draw" type="button" data-style="${escapeAttr(preset.key)}">
        <strong>${escapeHtml(preset.name)}</strong>
        <span>${escapeHtml(preset.note || "")}</span>
      </button>
      <button class="listing-style-edit${preset.example ? " has-example" : ""}" type="button"
              data-style-example="${escapeAttr(preset.key)}"
              title="${preset.example ? "This style has an example -- click to replace it" : "Show this style the picture it should imitate"}">&#9635;</button>
      <button class="listing-style-edit" type="button" data-style-edit="${escapeAttr(preset.key)}"
              title="Open this wording below to change it first">&#9998;</button>
    </div>
  `).join("");
  row.querySelectorAll("[data-style]").forEach((button) => {
    button.onclick = () => generateGuide(button, $("listingGuideRatio").value, { preset: button.dataset.style });
  });
  row.querySelectorAll("[data-style-example]").forEach((button) => {
    button.onclick = () => {
      const picker = $("listingStyleExampleFile");
      picker.dataset.style = button.dataset.styleExample;
      picker.click();
    };
  });
  row.querySelectorAll("[data-style-edit]").forEach((button) => {
    button.onclick = () => {
      const preset = listingState.presets.find((entry) => entry.key === button.dataset.styleEdit);
      if (!preset) return;
      $("listingGuidePrompt").value = preset.prompt;
      $("listingGuidePrompt").focus();
      toast(`${preset.name} opened below -- edit it, then Generate`);
    };
  });
}

function guideAdded(guide) {
  listingState.guides.push(guide);
  renderGuides();
  renderEditor();
}

async function uploadGuide() {
  const input = $("listingGuideFile");
  const file = input?.files?.[0];
  if (!file) {
    input?.click();
    return;
  }
  const form = new FormData();
  form.append("guide", file);
  form.append("ratio", $("listingGuideRatio").value);
  const button = $("listingGuideUpload");
  button.disabled = true;
  button.textContent = "Uploading...";
  try {
    const response = await fetch("/api/admin/size-guides", {
      method: "POST",
      headers: csrfHeaders(),
      body: form,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "Upload failed");
    input.value = "";
    guideAdded(payload.guide);
    toast("Size guide added");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.textContent = "Upload chart";
  }
}

async function generateGuide(button, ratio, { pin = false, preset = "" } = {}) {
  // A style button holds markup, not a word: replacing its text while the
  // chart draws flattened it, and it never came back.
  const label = button.innerHTML;
  button.disabled = true;
  button.classList.add("is-busy");
  try {
    // A style is sent as itself; the box below is sent only when it holds a
    // rewrite, so trying a style never overwrites the studio's own wording.
    const form = new FormData();
    form.append("ratio", ratio);
    if (preset) form.append("preset", preset);
    else form.append("prompt", $("listingGuidePrompt")?.value || "");
    if (listingState.reference) form.append("reference", listingState.reference);
    const response = await fetch("/api/admin/size-guides/generate", {
      method: "POST",
      headers: csrfHeaders(),
      body: form,
    });
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error || "The size guide could not be drawn");
    if (!preset) listingState.prompt = $("listingGuidePrompt")?.value || listingState.prompt;
    if (pin && listingState.draft) listingState.draft.guide = payload.guide.id;
    guideAdded(payload.guide);
    toast(pin ? "Size guide drawn and set" : "Size guide drawn");
  } catch (error) {
    toast(error.message);
  } finally {
    button.disabled = false;
    button.classList.remove("is-busy");
    button.innerHTML = label;
  }
}

// ------------------------------------------------------------------ wiring

function showTab(which) {
  const sets = which === "sets";
  $("listingSetsPane")?.classList.toggle("hidden", !sets);
  $("listingGuidesPane")?.classList.toggle("hidden", sets);
  $("listingTabSets")?.classList.toggle("active", sets);
  $("listingTabGuides")?.classList.toggle("active", !sets);
}

if ($("openListingSets")) {
  $("openListingSets").onclick = async () => {
    $("listingSetsModal").classList.add("open");
    showTab("sets");
    if (!listingState.loaded) {
      try {
        await loadEverything();
      } catch (error) {
        toast(error.message);
      }
    }
  };
}

if ($("closeListingSets")) {
  $("closeListingSets").onclick = () => $("listingSetsModal").classList.remove("open");
}
if ($("listingSetsModal")) {
  $("listingSetsModal").onclick = (event) => {
    if (event.target === $("listingSetsModal")) $("listingSetsModal").classList.remove("open");
  };
}

// The editor redraws whenever a slot changes, and a redraw fills the fields
// from the draft -- so what is typed has to reach the draft as it is typed,
// or choosing a mockup would wipe the name the set was just given.
if ($("listingSetName")) {
  $("listingSetName").oninput = () => {
    if (listingState.draft) listingState.draft.name = $("listingSetName").value;
  };
}
if ($("listingSetProduct")) {
  $("listingSetProduct").onchange = () => {
    if (listingState.draft) listingState.draft.product_type = $("listingSetProduct").value;
  };
}
if ($("listingSetOrientation")) {
  $("listingSetOrientation").onchange = () => {
    if (listingState.draft) listingState.draft.orientation = $("listingSetOrientation").value;
  };
}

if ($("listingTabSets")) $("listingTabSets").onclick = () => showTab("sets");
if ($("listingTabGuides")) $("listingTabGuides").onclick = () => showTab("guides");
if ($("listingNewSet")) $("listingNewSet").onclick = newSet;
if ($("listingSaveSet")) $("listingSaveSet").onclick = saveSet;
if ($("listingGuideUpload")) $("listingGuideUpload").onclick = uploadGuide;
if ($("listingGuideFile")) $("listingGuideFile").onchange = uploadGuide;
if ($("listingGuideGenerate")) {
  $("listingGuideGenerate").onclick = () =>
    generateGuide($("listingGuideGenerate"), $("listingGuideRatio").value);
}
if ($("listingReferenceButton")) {
  $("listingReferenceButton").onclick = () => {
    if (listingState.reference) {
      // A second click clears it, so an example never sticks to later charts
      // without the user knowing.
      listingState.reference = null;
      $("listingReferenceFile").value = "";
      $("listingReferenceName").textContent = "";
      return;
    }
    $("listingReferenceFile").click();
  };
}
if ($("listingStyleExampleFile")) {
  // The example belongs to the style, so it is sent with every chart drawn in
  // that style -- and never with wording the admin wrote themselves.
  $("listingStyleExampleFile").onchange = async () => {
    const input = $("listingStyleExampleFile");
    const file = input.files?.[0];
    const style = input.dataset.style;
    input.value = "";
    if (!file || !style) return;
    const form = new FormData();
    form.append("example", file);
    try {
      const response = await fetch(`/api/admin/size-guides/styles/${encodeURIComponent(style)}/example`, {
        method: "POST",
        headers: csrfHeaders(),
        body: form,
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "The example could not be saved");
      const preset = listingState.presets.find((entry) => entry.key === style);
      if (preset) preset.example = true;
      renderStyles();
      toast("Example saved for that style");
    } catch (error) {
      toast(error.message);
    }
  };
}

if ($("listingReferenceFile")) {
  $("listingReferenceFile").onchange = () => {
    const file = $("listingReferenceFile").files?.[0] || null;
    listingState.reference = file;
    $("listingReferenceName").textContent = file ? `example: ${file.name} (click to clear)` : "";
  };
}
if ($("listingSlotGenerate")) {
  // Generated from the set, the chart is pinned to it in the same step.
  $("listingSlotGenerate").onclick = (event) => {
    event.stopPropagation();
    generateGuide($("listingSlotGenerate"), $("listingSlotRatio").value, { pin: true });
  };
}
if ($("listingSlotRatio")) {
  $("listingSlotRatio").onclick = (event) => event.stopPropagation();
  $("listingSlotRatio").onchange = () => {
    $("listingSlotRatio").dataset.touched = "1";
  };
}
if ($("listingPromptReset")) {
  $("listingPromptReset").onclick = () => {
    $("listingGuidePrompt").value = listingState.defaultPrompt;
  };
}
if ($("listingPickerCategory")) $("listingPickerCategory").onchange = renderPicker;
if ($("listingPickerOrientation")) $("listingPickerOrientation").onchange = renderPicker;

/** The sets a given artwork shape can be built with, for the Test window. */
export async function availableListingSets(orientation) {
  if (!listingState.loaded) {
    try {
      const answer = await api("/api/admin/listing-sets");
      listingState.sets = answer.sets || [];
    } catch (_error) {
      return [];
    }
  }
  return listingState.sets.filter(
    (entry) => !orientation || entry.orientation === "any" || entry.orientation === orientation
  );
}
