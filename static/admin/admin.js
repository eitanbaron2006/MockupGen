import { api, csrfHeaders, maskVersion } from "./modules/api.js";
import {
  configureEngineSettings,
  initClassicSubmodeButtons,
  loadLocalModels,
  loadSettings,
  providerTitle,
  saveSettings,
  showProvider,
  switchDetectionProvider,
  testEngine,
  updateDetectionModeSwitch,
} from "./modules/engineSettings.js";
import {
  DEFAULT_EFFECTS,
  EFFECT_DOM,
  defaultEffects,
  effectGroupForKey,
  effectInstances,
  getFieldElement,
  prepareEffectGroupControls,
  primaryEffect,
  readEffectInstanceValues,
  setEffectInstanceValues,
  setEffectValueLabel,
  syncEffectAddButton,
  toggleEffectPanelCollapsed,
  updateEffectPanelCollapsed,
} from "./modules/effects.js";
// The Listing sets screen wires itself up on import, the way the other
// windows do; nothing here calls into it.
import "./modules/listingSets.js";
import "./modules/mockupStudio.js";
import { renderMockupGallery, renderTestGallery } from "./modules/testModal.js";
import {
  DEFAULT_SELECTION_STYLE,
  saveSelectionStylePreference,
  state,
  testState,
  wizardState,
} from "./modules/state.js";
import {
  $,
  dismissSystemDialog,
  openSystemDialog,
  wireSystemDialog,
  setStatus,
  systemAlert,
  systemConfirm,
  systemPrompt,
  toast,
} from "./modules/dom.js";
import {
  KEYS,
  readBoolean,
  readJson,
  readNumber,
  writeBoolean,
  writeJson,
  writeNumber,
} from "./modules/preferences.js";
import { makeToolbarDraggable } from "./modules/canvasRails.js";
import { watchSliders } from "./modules/sliders.js";
import {
  areaCorners,
  clampStyleNumber,
  cloneObject,
  confidenceLabel,
  dataURLtoFile,
  escapeAttr,
  escapeHtml,
  getMatrix3d,
  resolveFitMode,
  statusClass,
  usableCorners,
} from "./modules/helpers.js";

(() => {
  const DEFAULT_SIDEBAR_WIDTH = 252;
  const MIN_SIDEBAR_WIDTH = 232;
  const MAX_SIDEBAR_WIDTH = 520;


  function setSidebarWidth(width, persist = true) {
    const nextWidth = clampStyleNumber(width, MIN_SIDEBAR_WIDTH, MAX_SIDEBAR_WIDTH, DEFAULT_SIDEBAR_WIDTH);
    document.documentElement.style.setProperty("--sidebar-width", `${nextWidth}px`);
    if (persist) writeNumber(KEYS.sidebarWidth, nextWidth);
    return nextWidth;
  }

  function loadSidebarWidthPreference() {
    return setSidebarWidth(readNumber(KEYS.sidebarWidth, DEFAULT_SIDEBAR_WIDTH), false);
  }

  loadSidebarWidthPreference();


  // A rendered mockup runs to several megabytes, and a data: URL that size
  // makes a poor download link: the browser saves what it can and the file will
  // not open. Hand the buttons a blob instead -- nothing touches disk on the
  // server either way -- and name the file after what was actually rendered.
  let downloadObjectUrl = null;

  function setDownloadTarget(outputUrl) {
    if (downloadObjectUrl) {
      URL.revokeObjectURL(downloadObjectUrl);
      downloadObjectUrl = null;
    }
    if (!outputUrl) {
      clearDownloadTarget("The mockup could not be rendered");
      return;
    }
    let href = outputUrl;
    let filename = "mockup.png";
    if (outputUrl.startsWith("data:")) {
      try {
        const mime = outputUrl.slice(5, outputUrl.indexOf(";"));
        const extension = mime === "image/jpeg" ? "jpg" : mime.split("/")[1] || "png";
        filename = `mockup.${extension}`;
        downloadObjectUrl = URL.createObjectURL(dataURLtoFile(outputUrl, filename));
        href = downloadObjectUrl;
      } catch (error) {
        console.error("Could not prepare the download:", error);
      }
    } else {
      const clean = outputUrl.split("?")[0];
      filename = clean.slice(clean.lastIndexOf("/") + 1) || filename;
    }
    [$("downloadMockupButton"), $("toolbarDownloadButton")].forEach((button) => {
      if (!button) return;
      button.href = href;
      button.setAttribute("download", filename);
      button.style.pointerEvents = "auto";
      button.style.opacity = "1";
      button.setAttribute("title", "Download realistic mockup");
    });
  }

  function clearDownloadTarget(reason) {
    if (downloadObjectUrl) {
      URL.revokeObjectURL(downloadObjectUrl);
      downloadObjectUrl = null;
    }
    // A link with an empty href downloads the page itself, which arrives as an
    // image file that will not open. Leave it disabled instead.
    [$("downloadMockupButton"), $("toolbarDownloadButton")].forEach((button) => {
      if (!button) return;
      button.removeAttribute("href");
      button.style.pointerEvents = "none";
      button.style.opacity = "0.5";
      button.setAttribute("title", reason || "Generating high-fidelity download...");
    });
  }







  const DEFAULT_GREEN_FRAME_SETTINGS = {
    use_perspective: true,
    use_vector_clip: true,
    fit_mode: "cover",
    artwork_scale: 1,
    offset_x: 0,
    offset_y: 0,
    edge_expand: 0,
    tolerance: 442,
    mask_expand_left: 0,
    mask_expand_right: 0,
    mask_expand_top: 0,
    mask_expand_bottom: 0,
    mask_build_quality: 2,
    feather_radius: 0,
    edge_aa_radius: 0,
    aa_scale: 1,
    enable_inner_shadow: false,
    inner_shadow_strength: 0.35,
    inner_shadow_size: 10,
    contain_bg_color: "#ffffff"
  };

  function isMultiRegionTemplate(template = state.selected) {
    if (!template) return false;
    const raw = template.raw_artwork_area;
    const art = template.artwork_area;
    const hasRawRegions = Boolean(raw && typeof raw === "object" && Array.isArray(raw.regions) && raw.regions.length > 1);
    const hasArtRegions = Boolean(art && typeof art === "object" && Array.isArray(art.regions) && art.regions.length > 1);
    return hasRawRegions || hasArtRegions;
  }

  /** Whether the green frame settings reach this template's render.
   *
   * Not the same question as isGreenFrameTemplate, which drives the editor's
   * green-only behaviour -- the live overlay, positioning with the mouse -- and
   * excludes a template with more than one frame. The renderer draws no such
   * line: a set of green frames goes through the same pipeline as a single one
   * (see the branch at simple_mockup_service.render_simple_mockup, which takes
   * it on "green raw and a mask, or more than one region"), and reads the same
   * effects.green_frame_mockups block. So the panel that edits that block
   * follows the render, and a set is no longer left with settings in force and
   * no way to reach them.
   */
  function greenFrameControlsApply(template = state.selected) {
    if (!template) return false;
    return usesGreenFramePipeline(template) || isGreenFrameTemplate(template);
  }

  function isGreenFrameTemplate(template = state.selected) {
    if (!template) return false;
    if (isMultiRegionTemplate(template)) return false;
    if (wizardState && wizardState.active && !wizardState.isMultiFrame) return false;
    if (template.detection_provider === "vertex" || template.detection_provider === "local") {
      return false;
    }
    // If the template does not have a mask image, it is a standard geometric/polygon template
    if (!template.mask_name && !template.mask) {
      return false;
    }
    const raw = template.raw_artwork_area;
    if (raw && typeof raw === "object") {
      if (raw.provider === "vertex" || raw.mode === "vertex" || raw.provider === "local" || raw.mode === "local" || raw.provider === "classic" || raw.mode === "auto" || raw.mode === "geometry" || raw.layers) {
        return false;
      }
      if (raw.mode === "green_frames_mockups" || raw.provider === "green_frames_mockups") return true;
      if (raw.mode === "color_pick" || raw.provider === "color_pick") return true;
      if (raw.mode === "frame_points" || raw.provider === "frame_points") return true;
    }
    return Boolean(template.mask_name === "mask.png" || template.mask === "mask.png");
  }




  function greenFrameSettings(effects) {
    return {
      ...DEFAULT_GREEN_FRAME_SETTINGS,
      ...((effects && effects.green_frame_mockups) || {})
    };
  }

  function setGreenFrameLabel(id, value, suffix = "") {
    const element = $(id);
    if (element) element.textContent = `${value}${suffix}`;
  }

  function updateGreenFrameControlLabels() {
    if (!$("greenArtworkScale")) return;
    setGreenFrameLabel("greenArtworkScaleVal", $("greenArtworkScale").value, "%");
    setGreenFrameLabel("greenOffsetXVal", $("greenOffsetX").value, "%");
    setGreenFrameLabel("greenOffsetYVal", $("greenOffsetY").value, "%");
    setGreenFrameLabel("greenEdgeExpandVal", $("greenEdgeExpand").value, "px");
    setGreenFrameLabel("greenToleranceVal", $("greenTolerance").value, "");
    ["Top", "Bottom", "Left", "Right"].forEach((side) => {
      setGreenFrameLabel(`greenMaskExpand${side}Val`, $(`greenMaskExpand${side}`).value, "px");
    });
    setGreenFrameLabel("greenMaskBuildQualityVal", $("greenMaskBuildQuality").value, "x");
    setGreenFrameLabel("greenFeatherRadiusVal", $("greenFeatherRadius").value, "px");
    setGreenFrameLabel("greenEdgeAARadiusVal", $("greenEdgeAARadius").value, "px");
    setGreenFrameLabel("greenAAScaleVal", $("greenAAScale").value, "x");
  }

  function populateGreenFrameControls(template, effects) {
    const panel = $("greenFramePanel");
    if (!panel) return;
    panel.classList.toggle("hidden", !greenFrameControlsApply(template));
    const settings = greenFrameSettings(effects);
    $("greenUsePerspective").checked = settings.use_perspective;
    $("greenUseVectorClip").checked = settings.use_vector_clip;
    $("greenFitMode").value = settings.fit_mode;
    $("greenArtworkScale").value = Math.round(Number(settings.artwork_scale || 1) * 100);
    $("greenOffsetX").value = Math.round(Number(settings.offset_x || 0) * 100);
    $("greenOffsetY").value = Math.round(Number(settings.offset_y || 0) * 100);
    $("greenEdgeExpand").value = settings.edge_expand;
    $("greenTolerance").value = settings.tolerance;
    ["Top", "Bottom", "Left", "Right"].forEach((side) => {
      $(`greenMaskExpand${side}`).value = settings[`mask_expand_${side.toLowerCase()}`] || 0;
    });
    $("greenMaskBuildQuality").value = settings.mask_build_quality;
    $("greenFeatherRadius").value = settings.feather_radius;
    $("greenEdgeAARadius").value = settings.edge_aa_radius;
    $("greenAAScale").value = settings.aa_scale;
    $("greenContainBgColor").value = settings.contain_bg_color || "#ffffff";
    updateGreenFrameControlLabels();
  }

  function readGreenFrameControls() {
    return {
      use_perspective: $("greenUsePerspective").checked,
      use_vector_clip: $("greenUseVectorClip").checked,
      fit_mode: $("greenFitMode").value,
      artwork_scale: Number($("greenArtworkScale").value) / 100,
      offset_x: Number($("greenOffsetX").value) / 100,
      offset_y: Number($("greenOffsetY").value) / 100,
      edge_expand: Number($("greenEdgeExpand").value),
      tolerance: Number($("greenTolerance").value),
      mask_expand_top: Number($("greenMaskExpandTop").value),
      mask_expand_bottom: Number($("greenMaskExpandBottom").value),
      mask_expand_left: Number($("greenMaskExpandLeft").value),
      mask_expand_right: Number($("greenMaskExpandRight").value),
      mask_build_quality: Number($("greenMaskBuildQuality").value),
      feather_radius: Number($("greenFeatherRadius").value),
      edge_aa_radius: Number($("greenEdgeAARadius").value),
      aa_scale: Number($("greenAAScale").value),
      // Legacy green inner shadow retired — the standard Inner Frame Shadow
      // effect (target IMG) handles green mockups now.
      enable_inner_shadow: false,
      contain_bg_color: $("greenContainBgColor").value || "#ffffff"
    };
  }












  function setupEffectInstanceControls() {
    Object.keys(EFFECT_DOM).forEach((key) => {
      const def = EFFECT_DOM[key];
      const enabled = $(def.enabledId);
      if (!enabled) return;
      const group = enabled.closest(".effect-group");
      if (!group || group.dataset.effectKey) return;
      prepareEffectGroupControls(group, key, "1");
      const header = group.querySelector(".effect-header");
      const label = header.querySelector("label");
      if (label) {
        label.classList.add("effect-title-toggle");
        label.setAttribute("title", "Click the effect name to expand or collapse settings");
      }
      const addButton = document.createElement("button");
      addButton.type = "button";
      addButton.className = "icon-button effect-add-instance";
      addButton.dataset.effectKey = key;
      addButton.setAttribute("aria-label", `Add ${def.label} instance`);
      addButton.setAttribute("title", "Add second instance");
      addButton.textContent = "+";
      header.appendChild(addButton);
      updateEffectPanelCollapsed(group);
    });
  }

  function removeSecondEffectInstance(key) {
    const second = effectGroupForKey(key, "2");
    if (second) second.remove();
    syncEffectAddButton(key);
    updateEffectsState();
  }

  function createSecondEffectInstance(key, config) {
    const original = effectGroupForKey(key, "1");
    if (!original || effectGroupForKey(key, "2")) return;
    const clone = original.cloneNode(true);
    clone.classList.add("effect-instance-secondary");
    prepareEffectGroupControls(clone, key, "2");
    const header = clone.querySelector(".effect-header");
    header.querySelectorAll(".link-button, .effect-add-instance").forEach((button) => button.remove());
    const titleLabel = header.querySelector("label");
    if (titleLabel && !titleLabel.querySelector(".effect-instance-label")) {
      const badge = document.createElement("span");
      badge.className = "effect-instance-label";
      badge.textContent = "Second";
      titleLabel.appendChild(badge);
    }
    const deleteButton = document.createElement("button");
    deleteButton.type = "button";
    deleteButton.className = "icon-button effect-remove-instance";
    deleteButton.dataset.effectKey = key;
    deleteButton.setAttribute("aria-label", `Remove second ${EFFECT_DOM[key].label} instance`);
    deleteButton.setAttribute("title", "Remove second instance");
    deleteButton.textContent = "×";
    header.appendChild(deleteButton);
    original.insertAdjacentElement("afterend", clone);
    setEffectInstanceValues(clone, key, config || cloneObject(DEFAULT_EFFECTS[key]));
    syncEffectAddButton(key);
  }

  function renderAdditionalEffectInstances(effects) {
    Object.keys(EFFECT_DOM).forEach((key) => {
      const second = effectGroupForKey(key, "2");
      if (second) second.remove();
      const instances = effectInstances(effects, key);
      if (instances[1]) createSecondEffectInstance(key, { ...cloneObject(DEFAULT_EFFECTS[key]), ...instances[1] });
      syncEffectAddButton(key);
    });
  }

  // Colour tolerance for Colour Pick and Frame Points. At 40 a flat
  // placeholder keeps a few pixels of its own colour showing around the
  // artwork; 80 closes that without splitting one frame into several.
  const DEFAULT_MASK_TOLERANCE = 80;









  async function loadCategories(preferredId) {
    state.categories = (await api("/api/admin/categories")).categories;
    const selectedId = preferredId || (state.selectedCategory && state.selectedCategory.id);
    state.selectedCategory =
      state.categories.find((category) => category.id === selectedId) ||
      state.categories[0] ||
      null;
    renderCategories();
  }

  function renderCategories() {
    $("categories").innerHTML = state.categories.map((category) => `
      <div class="category-row ${state.selectedCategory && state.selectedCategory.id === category.id ? "selected" : ""}">
        <button class="category category-select" data-category="${category.id}">
          <span class="category-name">${escapeHtml(category.name)}</span><span class="count">${category.template_count}</span>
        </button>
        <div class="category-actions">
          <button class="category-action category-rename" type="button" data-category="${category.id}" aria-label="Rename ${escapeAttr(category.name)}" title="Rename category">✎</button>
          <button class="category-action category-delete" type="button" data-category="${category.id}" aria-label="Delete ${escapeAttr(category.name)}" title="${category.template_count > 0 ? "Only empty categories can be deleted" : "Delete empty category"}" ${category.template_count > 0 ? "disabled" : ""}>×</button>
        </div>
      </div>
    `).join("") || '<div class="empty">Create a product category to begin.</div>';
    $("breadcrumb").textContent = state.selectedCategory ? state.selectedCategory.name : "Select category";
    document.querySelectorAll(".category-select").forEach((button) => {
      button.onclick = async () => {
        if (state.busy) return;
        autoSaveCurrent();
        state.selectedCategory = state.categories.find((category) => category.id === Number(button.dataset.category));
        state.queueFilter = "all";
        renderCategories();
        await loadTemplates();
      };
    });
    document.querySelectorAll(".category-rename").forEach((button) => {
      button.onclick = () => renameCategory(Number(button.dataset.category));
    });
    document.querySelectorAll(".category-delete").forEach((button) => {
      button.onclick = () => deleteCategory(Number(button.dataset.category));
    });
  }

  async function renameCategory(categoryId) {
    if (state.busy) return;
    const category = state.categories.find((item) => item.id === categoryId);
    if (!category) return;
    const nextName = await systemPrompt("Rename category", category.name);
    if (nextName === null) return;
    const cleaned = nextName.trim();
    if (!cleaned || cleaned === category.name) return;
    try {
      const payload = await api(`/api/admin/categories/${category.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name: cleaned })
      });
      await loadCategories(payload.category.id);
      await loadTemplates(state.selected && state.selected.template_id);
      toast("Category renamed");
    } catch (error) {
      toast(error.message);
    }
  }

  async function deleteCategory(categoryId) {
    if (state.busy) return;
    const category = state.categories.find((item) => item.id === categoryId);
    if (!category) return;
    if (Number(category.template_count) > 0) {
      toast("Only empty categories can be deleted");
      return;
    }
    const confirmed = await systemConfirm(
      "Delete category",
      `Delete empty category "${category.name}"?`,
      { confirmLabel: "Delete category", danger: true }
    );
    if (!confirmed) return;
    try {
      await api(`/api/admin/categories/${category.id}`, { method: "DELETE" });
      if (state.selectedCategory && state.selectedCategory.id === category.id) {
        state.selectedCategory = null;
      }
      await loadCategories();
      await loadTemplates();
      toast("Category deleted");
    } catch (error) {
      toast(error.message);
    }
  }

  async function loadTemplates(preferredTemplateId) {
    if (!state.selectedCategory) {
      state.templates = [];
      state.selected = null;
      renderQueue();
      renderEditor();
      return;
    }
    const query = `?product_type=${encodeURIComponent(state.selectedCategory.slug)}`;
    state.templates = (await api(`/api/admin/templates${query}`)).templates;
    preloadEditorBackgrounds(state.templates);
    const desiredId = preferredTemplateId || (state.selected && state.selected.template_id);
    state.selected =
      state.templates.find((template) => template.template_id === desiredId) ||
      state.templates[0] ||
      null;
    renderQueue();
    renderEditor();
    prefetchGreenFrameRegularRenders();
  }

  function filteredTemplates() {
    if (state.queueFilter === "approved") return state.templates.filter((t) => t.status === "active");
    if (state.queueFilter === "review") return state.templates.filter((t) => t.status !== "active");
    return state.templates;
  }

  function renderQueue() {
    const visible = filteredTemplates();
    $("queueCount").textContent = state.templates.length;
    document.querySelectorAll(".filter-pill").forEach((pill) => {
      pill.classList.toggle("active", pill.dataset.filter === state.queueFilter);
    });
    $("queue").innerHTML = visible.map((template) => `
      <div class="queue-item ${state.selected && state.selected.template_id === template.template_id ? "selected" : ""}">
        <input type="checkbox" class="queue-checkbox" data-template="${template.template_id}" ${state.selectedForBatch.has(template.template_id) ? "checked" : ""} ${state.busy ? "disabled" : ""}>
        <button class="queue-select" type="button" data-template="${template.template_id}" title="${escapeAttr(template.name)}" ${state.busy ? "disabled" : ""}>
          <img class="thumb" src="/api/admin/templates/${template.template_id}/asset/preview.png" alt="">
          <span>
            <span class="file-title">${escapeHtml(template.name)}</span>
            <span class="meta">${escapeHtml(template.orientation)} <span class="pill ${statusClass(template)}">${template.status === "active" ? "Approved" : "Review"}</span></span>
          </span>
        </button>
        <button class="queue-delete" type="button" data-template="${template.template_id}" aria-label="Delete ${escapeAttr(template.name)}" title="Delete mockup" ${state.busy ? "disabled" : ""}>
          <svg class="trash-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 14px; height: 14px;"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
        </button>
      </div>
    `).join("") || '<div class="empty">No templates match this filter.</div>';
    $("queue").querySelectorAll(".queue-select").forEach((button) => {
      button.onclick = () => {
        autoSaveCurrent();
        exitAllDetectionModes();
        state.selected = state.templates.find((template) => template.template_id === button.dataset.template);
        renderQueue();
        renderEditor();
      };
    });
    $("queue").querySelectorAll(".queue-checkbox").forEach((box) => {
      box.onchange = (e) => {
        if (e.target.checked) {
          state.selectedForBatch.add(e.target.dataset.template);
        } else {
          state.selectedForBatch.delete(e.target.dataset.template);
        }
        updateBatchControls();
      };
    });
    // Scope to the queue list: other UI elements reuse the .queue-delete class
    // (e.g. the Global PNG Overlay clear button) and must keep their handlers.
    $("queue").querySelectorAll(".queue-delete").forEach((button) => {
      button.onclick = () => {
        const template = state.templates.find((item) => item.template_id === button.dataset.template);
        openDeleteModal(template);
      };
    });
    updateBatchControls();
  }

  function updateBatchControls() {
    const visible = filteredTemplates();
    const allVisibleSelected = visible.length > 0 && visible.every(t => state.selectedForBatch.has(t.template_id));
    $("selectAllCheckbox").checked = allVisibleSelected;
    $("selectAllCheckbox").disabled = state.busy || visible.length === 0;
    $("batchDetectButton").disabled = state.busy || state.selectedForBatch.size === 0;
    $("batchDeleteButton").disabled = state.busy || state.selectedForBatch.size === 0;
  }

  $("selectAllCheckbox").onchange = (e) => {
    const visible = filteredTemplates();
    if (e.target.checked) {
      visible.forEach(t => state.selectedForBatch.add(t.template_id));
    } else {
      visible.forEach(t => state.selectedForBatch.delete(t.template_id));
    }
    renderQueue();
  };

  $("batchDetectButton").onclick = async () => {
    if (state.selectedForBatch.size === 0) return;
    const ids = Array.from(state.selectedForBatch);
    state.busy = true;
    renderQueue();
    renderEditor();

    try {
      const response = await api("/api/admin/templates/batch-detect", {
        method: "POST",
        body: JSON.stringify({ template_ids: ids })
      });
      if (response.success && response.results) {
        let errorCount = 0;
        response.results.forEach(result => {
          if (result.success && result.template) {
            const index = state.templates.findIndex(t => t.template_id === result.template_id);
            if (index !== -1) {
              state.templates[index] = result.template;
              if (state.selected && state.selected.template_id === result.template_id) {
                state.selected = result.template;
              }
            }
          } else {
            errorCount++;
          }
        });
        if (errorCount > 0) {
          await systemAlert(
            "Batch detection finished",
            `${errorCount} template(s) failed to detect properly.`
          );
        }
      }
    } catch (error) {
      await systemAlert("Batch detection failed", error.message);
    } finally {
      state.busy = false;
      renderQueue();
      renderEditor();
    }
  };

  $("batchDeleteButton").onclick = async () => {
    if (state.selectedForBatch.size === 0 || state.busy) return;
    const ids = Array.from(state.selectedForBatch);
    const confirmed = await systemConfirm(
      "Delete selected mockups",
      `Delete ${ids.length} selected mockup${ids.length === 1 ? "" : "s"}?`,
      { confirmLabel: "Delete mockups", danger: true }
    );
    if (!confirmed) return;

    const selectedTemplateId = state.selected && state.selected.template_id;
    state.busy = true;
    renderQueue();
    renderEditor();

    try {
      setStatus("Deleting selected mockups...");
      for (const templateId of ids) {
        await api(`/api/admin/templates/${templateId}`, { method: "DELETE" });
      }
      state.selectedForBatch.clear();
      state.selected = ids.includes(selectedTemplateId) ? null : state.selected;
      await loadCategories(state.selectedCategory && state.selectedCategory.id);
      await loadTemplates(state.selected && state.selected.template_id);
      toast("Selected mockups deleted");
      setStatus("Selected mockups deleted");
    } catch (error) {
      setStatus("Batch delete failed", true);
      toast(error.message);
    } finally {
      state.busy = false;
      renderQueue();
      renderEditor();
    }
  };

  function orientationTitle(value) {
    return value === "landscape" ? "Wide" : value.charAt(0).toUpperCase() + value.slice(1);
  }


  let editorBackgroundLoadToken = 0;
  const editorBackgroundPreloadCache = new Map();
  function preloadEditorBackgrounds(templates) {
    templates.forEach((template) => {
      const backgroundUrl = `/api/admin/templates/${template.template_id}/asset/background.png`;
      if (editorBackgroundPreloadCache.has(backgroundUrl)) return;
      const backgroundImage = new Image();
      backgroundImage.src = backgroundUrl;
      editorBackgroundPreloadCache.set(backgroundUrl, backgroundImage);
    });
  }

  function loadEditorBackgroundAtomically(template) {
    const backgroundUrl = `/api/admin/templates/${template.template_id}/asset/background.png`;
    const token = ++editorBackgroundLoadToken;
    const preloadedBackground = editorBackgroundPreloadCache.get(backgroundUrl) || new Image();
    editorBackgroundPreloadCache.set(backgroundUrl, preloadedBackground);
    const applyPreloadedBackground = () => {
      if (!state.selected || state.selected.template_id !== template.template_id || token !== editorBackgroundLoadToken) return;
      const canvas = $("canvasImage");
      // Assigning src does not update naturalWidth/naturalHeight until the new
      // bitmap is swapped in, cached or not, and every overlay is scaled by
      // those. Drawing on the next frame laid the artwork out with the previous
      // template's aspect -- and nothing redrew it afterwards -- so wait for the
      // element to actually hold this template's mockup.
      canvas.onload = () => {
        canvas.onload = null;
        drawSelection();
      };
      canvas.src = backgroundUrl;
      if (canvas.complete && canvas.naturalWidth > 0) {
        // Already showing this background: no load event is coming.
        canvas.onload = null;
        requestAnimationFrame(() => {
          drawSelection();
        });
      }
    };
    preloadedBackground.onload = () => {
      applyPreloadedBackground();
    };
    preloadedBackground.onerror = () => {
      if (!state.selected || state.selected.template_id !== template.template_id || token !== editorBackgroundLoadToken) return;
      $("canvasImage").onload = () => {
        requestAnimationFrame(drawSelection);
      };
      $("canvasImage").src = backgroundUrl;
    };
    if (preloadedBackground.complete && preloadedBackground.naturalWidth > 0) {
      applyPreloadedBackground();
    } else if (!preloadedBackground.src) {
      preloadedBackground.src = backgroundUrl;
    }
  }

  function renderEditor() {
    const template = state.selected;
    const hasTemplate = Boolean(template);
    $("emptyEditor").classList.toggle("hidden", hasTemplate);
    $("stage").classList.toggle("hidden", !hasTemplate);
    $("inspector").classList.toggle("inactive", !hasTemplate);
    $("detectButton").disabled = state.busy || !hasTemplate;
    $("saveButton").disabled = state.busy || !hasTemplate;
    $("approveButton").disabled = state.busy || !hasTemplate;
    $("publishButton").disabled = state.busy || !hasTemplate;
    if (!template) {
      setGlobalOverlayPlacementActive(false);
      setGreenFramePlacementActive(false);
      $("currentTitle").textContent = "Select a mockup";
      $("confidence").textContent = "";
      $("coordX").textContent = "-";
      $("coordY").textContent = "-";
      $("coordW").textContent = "-";
      $("coordH").textContent = "-";
      $("zoomHud").classList.add("hidden");
      $("selectionStyleToolbar").classList.add("hidden");
      if ($("actionRail")) $("actionRail").classList.add("hidden");
      closeSelectionStylePanel();
      return;
    }

    // Reset preview mode on template switch/re-render
    state.isPreviewingMockup = false;
    if (maskDetectState.active) closeMaskDetectionHud();
    setGreenFramePlacementActive(false);
    if ($("selectionRenderedMockup")) {
      $("selectionRenderedMockup").classList.add("hidden");
      $("selectionRenderedMockup").src = "";
    }
    if ($("downloadMockupButton")) {
      $("downloadMockupButton").classList.add("hidden");
    }
    if ($("previewMockupButton")) {
      $("previewMockupButton").textContent = "Preview Mockup";
    }
    if ($("toolbarPreviewButton")) {
      $("toolbarPreviewButton").classList.remove("active");
    }
    syncActionRail();
    if ($("toolbarDownloadButton")) {
      $("toolbarDownloadButton").classList.add("hidden");
    }

    // Reset zoom and pan if template has changed
    if (state.lastSelectedTemplateId !== template.template_id) {
      // Mouse-positioning mode belongs to the previous template; leaving it
      // active while switching corrupts the editor state.
      if (state.globalOverlayPlacementActive) {
        state.wasPreviewingMockup = false;
        setGlobalOverlayPlacementActive(false);
      }
      state.zoom = 1;
      state.pan = { x: 0, y: 0 };
      state.lastSelectedTemplateId = template.template_id;
      applyZoomPan();
    }
    $("zoomHud").classList.remove("hidden");
    $("selectionStyleToolbar").classList.remove("hidden");
    if ($("actionRail")) $("actionRail").classList.remove("hidden");

    $("currentTitle").textContent = template.name;
    $("editorSub").textContent = template.status === "active"
      ? "Published template. Detection changes remain proposals until approved again."
      : "Draft template. Review the proposal before approval.";
    $("inspectorStatus").textContent = template.status === "active" ? "Approved template" : "Awaiting approval";
    $("proposalState").textContent = template.status === "active"
      ? "Approved rectangle is active. Run Detect frame to compare safely."
      : "Detect frame or adjust the artwork area before approval.";
    $("selectionSvg").classList.add("hidden");
    loadEditorBackgroundAtomically(template);
    $("templateName").value = template.name;
    $("categorySelect").innerHTML = state.categories.map((category) =>
      `<option value="${category.id}">${escapeHtml(category.name)}</option>`
    ).join("");
    $("categorySelect").value = template.category_id;
    $("fitMode").value = template.fit_mode;
    document.querySelectorAll(".direction").forEach((element) => {
      element.classList.toggle("active", element.dataset.direction === template.orientation);
    });

    // Populate Realism Effects values
    const effects = template.effects || defaultEffects();
    if (!template.effects) {
      template.effects = effects;
    }
    populateGreenFrameControls(template, effects);
    const innerShadowEffect = primaryEffect(effects, "inner_shadow");
    const glassReflectionEffect = primaryEffect(effects, "glass_reflection");
    const matteFinishEffect = primaryEffect(effects, "matte_finish");
    const colorTintEffect = primaryEffect(effects, "color_tint");
    const goboShadowEffect = primaryEffect(effects, "gobo_shadow");
    const photoshopAdjustmentsEffect = primaryEffect(effects, "photoshop_adjustments");
    const globalReflectionsEffect = primaryEffect(effects, "global_reflections");
    const globalPngOverlayEffect = primaryEffect(effects, "global_png_overlay");

    // Update segmented controls highlights
    document.querySelectorAll(".segmented-control[data-effect-key]").forEach((ctrl) => {
      const key = ctrl.getAttribute("data-effect-key");
      if (DEFAULT_EFFECTS[key]) {
        const fallbackTarget = ["inner_shadow", "glass_reflection", "matte_finish", "color_tint", "gobo_shadow"].includes(key) 
          ? "artwork" 
          : "all";
        const currentTarget = primaryEffect(effects, key).target || fallbackTarget;
        
        ctrl.querySelectorAll(".segment-btn").forEach((btn) => {
          btn.classList.toggle("active", btn.getAttribute("data-target-val") === currentTarget);
        });
      }
    });
    
    // Set inner shadow fields
    const shadowEnabled = innerShadowEffect.enabled || false;
    $("innerShadowEnabled").checked = shadowEnabled;
    const shadowRoot = effectGroupForKey("inner_shadow", "1");
    if (shadowRoot) {
      shadowRoot.dataset.effectCollapsed = String(!shadowEnabled);
      updateEffectPanelCollapsed(shadowRoot);
    }
    
    $("shadowOpacity").value = innerShadowEffect.opacity ?? 0.4;
    $("shadowOpacityVal").textContent = Math.round((innerShadowEffect.opacity ?? 0.4) * 100) + "%";
    
    $("shadowBlur").value = innerShadowEffect.blur ?? 15;
    $("shadowBlurVal").textContent = (innerShadowEffect.blur ?? 15) + "px";
    
    $("shadowTop").value = innerShadowEffect.top ?? 10;
    $("shadowTopVal").textContent = (innerShadowEffect.top ?? 10) + "px";
    
    $("shadowBottom").value = innerShadowEffect.bottom ?? 10;
    $("shadowBottomVal").textContent = (innerShadowEffect.bottom ?? 10) + "px";
    
    $("shadowLeft").value = innerShadowEffect.left ?? 10;
    $("shadowLeftVal").textContent = (innerShadowEffect.left ?? 10) + "px";
    
    $("shadowRight").value = innerShadowEffect.right ?? 10;
    $("shadowRightVal").textContent = (innerShadowEffect.right ?? 10) + "px";
    
    // Set glass reflection fields
    const glassEnabled = glassReflectionEffect.enabled || false;
    $("glassReflectionEnabled").checked = glassEnabled;
    const glassRoot = effectGroupForKey("glass_reflection", "1");
    if (glassRoot) {
      glassRoot.dataset.effectCollapsed = String(!glassEnabled);
      updateEffectPanelCollapsed(glassRoot);
    }
    
    $("reflectionType").value = glassReflectionEffect.type || "diagonal";
    $("reflectionOpacity").value = glassReflectionEffect.opacity ?? 0.15;
    $("reflectionOpacityVal").textContent = Math.round((glassReflectionEffect.opacity ?? 0.15) * 100) + "%";

    // Set faded matte paper fields
    const matteEnabled = matteFinishEffect.enabled || false;
    $("matteFinishEnabled").checked = matteEnabled;
    const matteRoot = effectGroupForKey("matte_finish", "1");
    if (matteRoot) {
      matteRoot.dataset.effectCollapsed = String(!matteEnabled);
      updateEffectPanelCollapsed(matteRoot);
    }
    $("matteShadowLift").value = matteFinishEffect.shadow_lift ?? 0.08;
    $("matteShadowLiftVal").textContent = Math.round((matteFinishEffect.shadow_lift ?? 0.08) * 100) + "%";
    $("matteContrast").value = matteFinishEffect.contrast ?? -0.15;
    $("matteContrastVal").textContent = Math.round((matteFinishEffect.contrast ?? -0.15) * 100) + "%";

    // Set ambient warmth fields
    const tintEnabled = colorTintEffect.enabled || false;
    $("colorTintEnabled").checked = tintEnabled;
    const tintRoot = effectGroupForKey("color_tint", "1");
    if (tintRoot) {
      tintRoot.dataset.effectCollapsed = String(!tintEnabled);
      updateEffectPanelCollapsed(tintRoot);
    }
    $("tintTemperature").value = colorTintEffect.temperature ?? 25;
    const tempSign = (colorTintEffect.temperature ?? 25) > 0 ? "+" : "";
    $("tintTemperatureVal").textContent = tempSign + (colorTintEffect.temperature ?? 25);
    $("tintIntensity").value = colorTintEffect.intensity ?? 0.2;
    $("tintIntensityVal").textContent = Math.round((colorTintEffect.intensity ?? 0.2) * 100) + "%";

    // Set sunlight blinds fields
    const goboEnabled = goboShadowEffect.enabled || false;
    $("goboShadowEnabled").checked = goboEnabled;
    const goboRoot = effectGroupForKey("gobo_shadow", "1");
    if (goboRoot) {
      goboRoot.dataset.effectCollapsed = String(!goboEnabled);
      updateEffectPanelCollapsed(goboRoot);
    }
    $("goboOpacity").value = goboShadowEffect.opacity ?? 0.3;
    $("goboOpacityVal").textContent = Math.round((goboShadowEffect.opacity ?? 0.3) * 100) + "%";
    $("goboScale").value = goboShadowEffect.scale ?? 1.0;
    $("goboScaleVal").textContent = (goboShadowEffect.scale ?? 1.0) + "x";

    // Set Photoshop Adjustments
    const psEnabled = photoshopAdjustmentsEffect.enabled || false;
    $("photoshopAdjustmentsEnabled").checked = psEnabled;
    const psRoot = effectGroupForKey("photoshop_adjustments", "1");
    if (psRoot) {
      psRoot.dataset.effectCollapsed = String(!psEnabled);
      updateEffectPanelCollapsed(psRoot);
    }
    $("photoshopColorFilter").value = photoshopAdjustmentsEffect.color_filter || "none";
    $("photoshopBrightness").value = photoshopAdjustmentsEffect.brightness ?? 0.0;
    const psBrtVal = Math.round((photoshopAdjustmentsEffect.brightness ?? 0.0) * 100);
    $("photoshopBrightnessVal").textContent = (psBrtVal >= 0 ? "+" : "") + psBrtVal + "%";
    $("photoshopContrast").value = photoshopAdjustmentsEffect.contrast ?? 0.0;
    const psCtrVal = Math.round((photoshopAdjustmentsEffect.contrast ?? 0.0) * 100);
    $("photoshopContrastVal").textContent = (psCtrVal >= 0 ? "+" : "") + psCtrVal + "%";
    $("photoshopSaturation").value = photoshopAdjustmentsEffect.saturation ?? 0.0;
    const psSatVal = Math.round((photoshopAdjustmentsEffect.saturation ?? 0.0) * 100);
    $("photoshopSaturationVal").textContent = (psSatVal >= 0 ? "+" : "") + psSatVal + "%";

    // Set Global Reflections & Sun rays
    const refEnabled = globalReflectionsEffect.enabled || false;
    $("globalReflectionsEnabled").checked = refEnabled;
    const refRoot = effectGroupForKey("global_reflections", "1");
    if (refRoot) {
      refRoot.dataset.effectCollapsed = String(!refEnabled);
      updateEffectPanelCollapsed(refRoot);
    }
    $("globalWindowType").value = globalReflectionsEffect.window_type || "none";
    $("globalWindowOpacity").value = globalReflectionsEffect.window_opacity ?? 0.2;
    $("globalWindowOpacityVal").textContent = Math.round((globalReflectionsEffect.window_opacity ?? 0.2) * 100) + "%";
    $("globalWindowBlur").value = globalReflectionsEffect.window_blur ?? 20;
    $("globalWindowBlurVal").textContent = (globalReflectionsEffect.window_blur ?? 20) + "px";
    $("globalRaysType").value = globalReflectionsEffect.rays_type || "none";
    $("globalRaysOpacity").value = globalReflectionsEffect.rays_opacity ?? 0.2;
    $("globalRaysOpacityVal").textContent = Math.round((globalReflectionsEffect.rays_opacity ?? 0.2) * 100) + "%";
    $("globalRaysAngle").value = globalReflectionsEffect.rays_angle ?? 0;
    $("globalRaysAngleVal").textContent = (globalReflectionsEffect.rays_angle ?? 0) + "°";

    // Set Global PNG Overlay
    const overlayEnabled = globalPngOverlayEffect.enabled || false;
    $("globalPngOverlayEnabled").checked = overlayEnabled;
    const overlayPanelRoot = effectGroupForKey("global_png_overlay", "1");
    if (overlayPanelRoot) {
      overlayPanelRoot.dataset.effectCollapsed = String(!overlayEnabled);
      updateEffectPanelCollapsed(overlayPanelRoot);
    }
    EFFECT_DOM.global_png_overlay.fields.forEach((field) => {
      const element = $(field.id);
      if (!element) return;
      const value = globalPngOverlayEffect[field.prop] ?? DEFAULT_EFFECTS.global_png_overlay[field.prop];
      if (field.type === "boolean") element.checked = Boolean(value);
      else element.value = value;
      setEffectValueLabel(field, value);
    });
    
    const overlayImgData = globalPngOverlayEffect.image || "";
    const overlayRoot = effectGroupForKey("global_png_overlay", "1");
    if (overlayRoot) overlayRoot.dataset.overlayImage = overlayImgData;
    if (overlayImgData) {
      $("globalOverlayName").textContent = "Overlay loaded";
      $("globalOverlayName").setAttribute("title", "PNG Overlay base64 encoded");
    } else {
      $("globalOverlayName").textContent = "No file";
      $("globalOverlayName").removeAttribute("title");
    }

    if (template.detection_provider) {
      $("confidence").textContent = confidenceLabel(template.detection_confidence);
    } else {
      $("confidence").textContent = "";
    }
    updateCoordinateLabels();
    renderAdditionalEffectInstances(effects);
    if ($("canvasImage").complete) {
      requestAnimationFrame(drawSelection);
    }
  }

  function updateCoordinateLabels() {
    const area = state.selected && state.selected.artwork_area;
    $("coordX").textContent = area ? area.x : "-";
    $("coordY").textContent = area ? area.y : "-";
    $("coordW").textContent = area ? area.width : "-";
    $("coordH").textContent = area ? area.height : "-";
  }

  function getRenderedImageRect(img) {
    const naturalWidth = img.naturalWidth;
    const naturalHeight = img.naturalHeight;
    if (!naturalWidth || !naturalHeight) return null;

    const clientWidth = img.clientWidth;
    const clientHeight = img.clientHeight;

    const imageRatio = naturalWidth / naturalHeight;
    const clientRatio = clientWidth / clientHeight;

    let renderedWidth, renderedHeight, left, top;

    if (clientRatio > imageRatio) {
      renderedHeight = clientHeight;
      renderedWidth = clientHeight * imageRatio;
      left = (clientWidth - renderedWidth) / 2;
      top = 0;
    } else {
      renderedWidth = clientWidth;
      renderedHeight = clientWidth / imageRatio;
      left = 0;
      top = (clientHeight - renderedHeight) / 2;
    }

    return {
      width: renderedWidth,
      height: renderedHeight,
      left: left,
      top: top
    };
  }

  function applyZoomPan() {
    const stage = $("stage");
    if (!stage) return;
    stage.style.transform = `translate(${state.pan.x}px, ${state.pan.y}px) scale(${state.zoom})`;
    stage.style.transformOrigin = "0 0";

    const textEl = $("zoomText");
    if (textEl) {
      textEl.textContent = `${Math.round(state.zoom * 100)}%`;
    }
    renderGlobalOverlayPlacement();
  }

  function activeGlobalOverlayRoot() {
    return effectGroupForKey("global_png_overlay", "1");
  }

  function activeGlobalOverlayImage() {
    const root = activeGlobalOverlayRoot();
    const fromRoot = root?.dataset.overlayImage || "";
    const fromState = primaryEffect(state.selected?.effects || {}, "global_png_overlay").image || "";
    return fromRoot || fromState;
  }

  function activeGlobalOverlayConfig() {
    const config = { ...cloneObject(DEFAULT_EFFECTS.global_png_overlay), ...primaryEffect(state.selected?.effects || {}, "global_png_overlay") };
    EFFECT_DOM.global_png_overlay.fields.forEach((field) => {
      const element = $(field.id);
      if (!element) return;
      if (field.type === "number") config[field.prop] = Number(element.value);
      else if (field.type === "boolean") config[field.prop] = element.checked;
      else config[field.prop] = element.value;
    });
    config.image = activeGlobalOverlayImage();
    return config;
  }

  function anchorFractions(anchor) {
    return {
      top_left: [0, 0],
      top: [0.5, 0],
      top_right: [1, 0],
      left: [0, 0.5],
      center: [0.5, 0.5],
      right: [1, 0.5],
      bottom_left: [0, 1],
      bottom: [0.5, 1],
      bottom_right: [1, 1]
    }[anchor] || [0.5, 0.5];
  }

  function renderGlobalOverlayPlacement() {
    const layer = $("globalOverlayPlacementLayer");
    const item = $("globalOverlayPlacementItem");
    const img = $("globalOverlayPlacementImg");
    if (!layer || !item || !img) return;
    if (!state.globalOverlayPlacementActive || !state.selected || state.isPreviewingMockup) {
      layer.classList.add("hidden");
      img.src = "";
      return;
    }
    const rect = getRenderedImageRect($("canvasImage"));
    const config = activeGlobalOverlayConfig();
    if (!rect || !config.image) {
      layer.classList.add("hidden");
      return;
    }
    layer.classList.remove("hidden");
    layer.style.left = `${rect.left}px`;
    layer.style.top = `${rect.top}px`;
    layer.style.width = `${rect.width}px`;
    layer.style.height = `${rect.height}px`;

    const naturalRatio = img.naturalWidth && img.naturalHeight ? img.naturalHeight / img.naturalWidth : 1;
    const width = Math.max(1, rect.width * Math.max(0.01, Number(config.scale) || 1));
    const height = Math.max(1, width * naturalRatio);
    const [ax, ay] = anchorFractions(config.anchor);
    const pointX = (Number(config.position_x || 0) + 1) * rect.width / 2;
    const pointY = (Number(config.position_y || 0) + 1) * rect.height / 2;

    item.style.width = `${width}px`;
    item.style.height = `${height}px`;
    item.style.left = `${pointX - width * ax}px`;
    item.style.top = `${pointY - height * ay}px`;
    item.style.opacity = String(config.opacity ?? 0.5);
    item.style.transform = `rotate(${Number(config.rotation || 0)}deg)`;
    item.style.transformOrigin = `${ax * 100}% ${ay * 100}%`;
    item.style.mixBlendMode = config.blend_mode === "normal" ? "normal" : config.blend_mode.replace("_", "-");
    item.style.filter = `blur(${Number(config.blur || 0)}px)`;
    item.classList.toggle("repeat-preview", Boolean(config.repeat));
    item.style.backgroundImage = config.repeat ? `url("${config.image}")` : "";
    item.style.backgroundRepeat = config.repeat ? "repeat" : "";
    item.style.backgroundSize = config.repeat ? `${width}px ${height}px` : "";
    img.src = config.image;
    img.style.transform = `scale(${config.flip_x ? -1 : 1}, ${config.flip_y ? -1 : 1})`;
  }

  function setGlobalOverlayPlacementActive(active) {
    const wasPlacementActive = state.globalOverlayPlacementActive;
    state.globalOverlayPlacementActive = Boolean(active);
    const button = $("globalOverlayPlaceBtn");
    if (button) {
      button.classList.toggle("active", state.globalOverlayPlacementActive);
      button.textContent = state.globalOverlayPlacementActive ? "Exit mouse positioning" : "Position with mouse";
    }
    if (state.globalOverlayPlacementActive) {
      if (!activeGlobalOverlayImage()) {
        toast("Upload a PNG overlay first.");
        state.globalOverlayPlacementActive = false;
        if (button) {
          button.classList.remove("active");
          button.textContent = "Position with mouse";
        }
      } else {
        state.wasPreviewingMockup = state.isPreviewingMockup;
        state.isPreviewingMockup = false;
        $("selectionSvg").classList.add("hidden");
        // Keep the artwork visible while positioning the overlay; drawSelection
        // renders the appropriate layer (CSS overlay or lightweight render).
        drawSelection();
      }
    } else {
      if (state.wasPreviewingMockup) {
        state.wasPreviewingMockup = false;
        togglePreviewMode();
      } else {
        drawSelection();
      }
    }
    renderGlobalOverlayPlacement();
    applyPlacementCanvasLock(wasPlacementActive, state.globalOverlayPlacementActive);
  }

  // Canvas lock: freezes zoom & pan so mouse-positioning of overlays cannot
  // accidentally move the image. Auto-engaged during placement modes.
  function setCanvasLock(locked) {
    state.canvasLocked = Boolean(locked);
    const lockBtn = $("canvasLockBtn");
    if (lockBtn) {
      lockBtn.classList.toggle("active", state.canvasLocked);
      lockBtn.title = state.canvasLocked
        ? "Canvas locked — zoom & pan disabled (click to unlock)"
        : "Lock canvas to prevent accidental zoom & pan";
      const openIcon = lockBtn.querySelector(".lock-icon-open");
      const closedIcon = lockBtn.querySelector(".lock-icon-closed");
      if (openIcon) openIcon.classList.toggle("hidden", state.canvasLocked);
      if (closedIcon) closedIcon.classList.toggle("hidden", !state.canvasLocked);
    }
    ["zoomInBtn", "zoomOutBtn", "zoomResetBtn"].forEach((id) => {
      const btn = $(id);
      if (btn) btn.disabled = state.canvasLocked;
    });
  }

  // Artwork-polygon lock: freezes the selection quad (corner handles + whole
  // area drag) so positioning an overlay never moves the artwork by mistake.
  function setPolygonLock(locked) {
    state.polygonLocked = Boolean(locked);
    const lockBtn = $("polygonLockBtn");
    if (lockBtn) {
      lockBtn.classList.toggle("active", state.polygonLocked);
      lockBtn.title = state.polygonLocked
        ? "Artwork area locked — corners & moving disabled (click to unlock)"
        : "Lock artwork area to prevent accidental moving";
      const openIcon = lockBtn.querySelector(".lock-icon-open");
      const closedIcon = lockBtn.querySelector(".lock-icon-closed");
      if (openIcon) openIcon.classList.toggle("hidden", state.polygonLocked);
      if (closedIcon) closedIcon.classList.toggle("hidden", !state.polygonLocked);
    }
    const selectionSvg = $("selectionSvg");
    if (selectionSvg) selectionSvg.classList.toggle("polygon-locked", state.polygonLocked);
  }

  function applyPlacementCanvasLock(wasActive, isActive) {
    if (isActive && !wasActive) {
      state.lockBeforePlacement = state.canvasLocked;
      setCanvasLock(true);
      // Lock the artwork polygon by default during mouse positioning; the
      // user can still unlock it manually from the HUD if needed.
      state.polygonLockBeforePlacement = state.polygonLocked;
      setPolygonLock(true);
    } else if (!isActive && wasActive) {
      setCanvasLock(Boolean(state.lockBeforePlacement));
      setPolygonLock(Boolean(state.polygonLockBeforePlacement));
    }
  }

  function setGreenFramePlacementActive(active) {
    const wasPlacementActive = state.greenFramePlacementActive;
    state.greenFramePlacementActive = Boolean(active);
    const button = $("greenFramePlaceBtn");
    if (button) {
      button.classList.toggle("active", state.greenFramePlacementActive);
      button.textContent = state.greenFramePlacementActive ? "Exit mouse positioning" : "Position with mouse";
    }
    const placementLayer = $("greenFramePlacementLayer");
    if (placementLayer) {
      placementLayer.classList.toggle("hidden", !state.greenFramePlacementActive);
    }
    
    if (state.greenFramePlacementActive) {
      if (!state.selectionStyle.overlayImage) {
        toast("Choose or upload a preview image first.");
        state.greenFramePlacementActive = false;
        if (button) {
          button.classList.remove("active");
          button.textContent = "Position with mouse";
        }
        if (placementLayer) {
          placementLayer.classList.add("hidden");
        }
      } else {
        if ($("selectionRenderedMockup")) $("selectionRenderedMockup").classList.add("hidden");
        drawSelection();
      }
    } else {
      if ($("selectionImageOverlay")) $("selectionImageOverlay").classList.add("hidden");
      drawSelection();
    }
    applyPlacementCanvasLock(wasPlacementActive, state.greenFramePlacementActive);
  }

  function applySelectionStyle() {
    const style = state.selectionStyle;
    const svg = $("selectionSvg");
    if (svg) {
      svg.style.setProperty("--selection-color", style.polygonColor);
      svg.style.setProperty("--selection-fill-opacity", style.polygonOpacity / 100);
      svg.style.setProperty("--selection-stroke-width", `${style.polygonWidth}px`);
      svg.style.setProperty("--cross-color", style.crossColor);
      svg.style.setProperty("--cross-opacity", style.crossOpacity / 100);
      svg.style.setProperty("--cross-stroke-width", `${style.crossWidth}px`);
    }
    const selectionPolygon = $("selectionPolygon");
    if (selectionPolygon) {
      selectionPolygon.classList.toggle("image-mode", style.overlayMode === "image" && Boolean(style.overlayImage));
    }
    const selectionImage = $("selectionImage");
    if (selectionImage) {
      const hasImage = style.overlayMode === "image" && Boolean(style.overlayImage);
      selectionImage.setAttribute("href", hasImage ? style.overlayImage : "");
    }
    document.querySelectorAll(".style-segment").forEach((button) => {
      if (button.dataset.overlayMode) {
        button.classList.toggle("active", button.dataset.overlayMode === style.overlayMode);
      }
    });
    const isImageMode = style.overlayMode === "image" && Boolean(style.overlayImage);
    if ($("overlayFitModeContainer")) {
      $("overlayFitModeContainer").classList.toggle("hidden", !isImageMode);
    }
    if ($("previewMockupButton")) {
      $("previewMockupButton").classList.toggle("hidden", !isImageMode);
    }
    if ($("toolbarPreviewButton")) {
      $("toolbarPreviewButton").classList.toggle("hidden", !isImageMode);
    }
    if (state.selected) {
      document.querySelectorAll(".style-overlay-fit").forEach((button) => {
        button.classList.toggle("active", button.dataset.overlayFit === state.selected.fit_mode);
      });
    }
    if ($("overlayImageName")) {
      const name = style.overlayImageName || "No image selected";
      let displayName = name;
      if (style.overlayImageName && style.overlayImageName.length > 25) {
        displayName = style.overlayImageName.substring(0, 12) + "..." + style.overlayImageName.substring(style.overlayImageName.length - 10);
      }
      $("overlayImageName").textContent = displayName;
      if (style.overlayImageName) {
        $("overlayImageName").setAttribute("title", style.overlayImageName);
      } else {
        $("overlayImageName").removeAttribute("title");
      }
    }
    if ($("polygonColorSwatch")) $("polygonColorSwatch").style.background = style.polygonColor;
    if ($("crossColorIcon")) $("crossColorIcon").style.color = style.crossColor;
    if ($("polygonColorInput")) $("polygonColorInput").value = style.polygonColor;
    if ($("crossColorInput")) $("crossColorInput").value = style.crossColor;
    if ($("polygonOpacityInput")) $("polygonOpacityInput").value = style.polygonOpacity;
    if ($("crossOpacityInput")) $("crossOpacityInput").value = style.crossOpacity;
    if ($("polygonWidthInput")) $("polygonWidthInput").value = style.polygonWidth;
    if ($("crossWidthInput")) $("crossWidthInput").value = style.crossWidth;
    if ($("polygonOpacityValue")) $("polygonOpacityValue").textContent = `${style.polygonOpacity}%`;
    if ($("crossOpacityValue")) $("crossOpacityValue").textContent = `${style.crossOpacity}%`;
    if ($("polygonWidthValue")) $("polygonWidthValue").textContent = `${style.polygonWidth}px`;
    if ($("crossWidthValue")) $("crossWidthValue").textContent = `${style.crossWidth}px`;
  }


  function setOverlayMode(mode) {
    const nextMode = mode === "image" ? "image" : "polygon";
    if (nextMode === "image" && !state.selectionStyle.overlayImage) {
      $("overlayImageInput").click();
      return;
    }
    state.selectionStyle.overlayMode = nextMode;
    applySelectionStyle();
    saveSelectionStylePreference();
    drawSelection();
  }

  function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const url = URL.createObjectURL(file);
      const image = new Image();
      image.onload = () => {
        const maxSide = 1600;
        const scale = Math.min(1, maxSide / Math.max(image.naturalWidth, image.naturalHeight));
        const canvas = document.createElement("canvas");
        canvas.width = Math.max(1, Math.round(image.naturalWidth * scale));
        canvas.height = Math.max(1, Math.round(image.naturalHeight * scale));
        const context = canvas.getContext("2d");
        context.drawImage(image, 0, 0, canvas.width, canvas.height);
        URL.revokeObjectURL(url);
        resolve(canvas.toDataURL("image/webp", 0.86));
      };
      image.onerror = () => {
        URL.revokeObjectURL(url);
        reject(new Error("Could not read image"));
      };
      image.src = url;
    });
  }

  async function chooseOverlayImage(file) {
    if (!file) return;
    try {
      const dataUrl = await fileToDataUrl(file);
      state.selectionStyle.overlayImage = dataUrl;
      state.selectionStyle.overlayImageName = file.name;
      state.selectionStyle.overlayMode = "image";
      const img = new Image();
      img.onload = () => {
        state.selectionStyle.overlayImageWidth = img.naturalWidth;
        state.selectionStyle.overlayImageHeight = img.naturalHeight;
        applySelectionStyle();
        saveSelectionStylePreference();
        drawSelection();
        
        if (isGreenFrameTemplate()) {
          refreshGreenFrameMockupPreview();
        } else if (state.isPreviewingMockup) {
          refreshPreviewMockup();
        }
        prefetchGreenFrameRegularRenders();
      };
      img.src = dataUrl;
    } catch (error) {
      toast(error.message || "Could not load image");
    }
  }

  function clearOverlayImage() {
    state.selectionStyle.overlayImage = "";
    state.selectionStyle.overlayImageName = "";
    state.selectionStyle.overlayMode = "polygon";
    applySelectionStyle();
    saveSelectionStylePreference();
    if ($("selectionRenderedMockup") && isGreenFrameTemplate()) {
      $("selectionRenderedMockup").classList.add("hidden");
      $("selectionRenderedMockup").src = "";
    }
    drawSelection();
  }

  function openSelectionStylePanel(panelId, button) {
    $("selectionStylePopover").classList.remove("hidden");
    $("selectionStylePopover").style.top = `${button.offsetTop}px`;
    document.querySelectorAll(".style-tool").forEach((tool) => {
      tool.classList.toggle("active", tool === button);
    });
    document.querySelectorAll(".style-panel").forEach((panel) => {
      panel.classList.toggle("hidden", panel.id !== panelId);
    });
  }

  function closeSelectionStylePanel() {
    $("selectionStylePopover").classList.add("hidden");
    document.querySelectorAll(".style-tool").forEach((tool) => {
      tool.classList.remove("active");
    });
  }

  /** Are these four corners something that can be drawn?
   *
   * Older detections left corner lists like [{}, {"x": null}, ...] behind. Fed
   * to an SVG they become "NaN,NaN", which the browser rejects attribute by
   * attribute -- a console full of errors and no outline on the canvas.
   */


  function greenFrameOverlayRegions(template) {
    if (isMultiRegionTemplate(template)) {
      const rawRegions = template.raw_artwork_area && Array.isArray(template.raw_artwork_area.regions)
        ? template.raw_artwork_area.regions
        : [];
      return rawRegions
        .filter((region) => region && Number(region.width) > 0 && Number(region.height) > 0)
        .map((region) => ({
          ...region,
          corners: usableCorners(region.corners) ? region.corners
            : usableCorners(region.inner_corners) ? region.inner_corners
              : areaCorners(region)
        }))
        .filter((region) => Array.isArray(region.corners) && region.corners.length >= 4);
    }
    const area = template.artwork_area;
    if (!area) return [];
    const corners = usableCorners(area.corners) ? area.corners : areaCorners(area);
    return [
      {
        ...area,
        corners: corners
      }
    ];
  }

  /** Does this template render through the green-frame pipeline?
   *
   * Mirrors the branch in render_simple_mockup: a geometric multi-frame
   * template and a single non-green detection composite straight onto their
   * corners, and only the green pipeline widens the artwork past the frame.
   */
  function usesGreenFramePipeline(template) {
    const raw = template.raw_artwork_area;
    const regions = raw && Array.isArray(raw.regions) ? raw.regions : [];
    const multiRegion = regions.length > 1;
    const mode = raw && raw.mode;
    const provider = template.detection_provider;
    if (mode === "geometry" && multiRegion) return false;
    if ((provider === "vertex" || provider === "local") && !multiRegion) return false;
    const greenRaw = Boolean(raw)
      && provider !== "vertex" && provider !== "local"
      && raw.provider !== "vertex" && raw.mode !== "vertex"
      && raw.provider !== "local" && raw.mode !== "local"
      && (mode === "green_frames_mockups" || raw.provider === "green_frames_mockups" || multiRegion);
    return Boolean((templateMaskUrl(template) && greenRaw) || multiRegion);
  }

  /** Where the renderer will put one frame's artwork: the quad it warps the
   * artwork onto, and the box it fits the artwork into first.
   *
   * Mirrors _render_perspective_region / _draw_rect for the green pipeline and
   * _render_geometric_frames / the single-frame path for everything else. The
   * frame bounds the artwork exactly in all of them -- the wide coverage
   * envelope bleeds the artwork's edge colour past the frame, it does not warp
   * the artwork onto a wider quad -- so the editor never draws past the frame
   * either.
   *
   * `settings` is null wherever no green pipeline runs; there the artwork is
   * fitted to the frame's bounding box rather than to its side lengths.
   */
  function greenFrameArtworkPlacement(corners, settings) {
    const xs = corners.map((point) => point.x);
    const ys = corners.map((point) => point.y);
    const left = Math.min(...xs);
    const top = Math.min(...ys);
    const right = Math.max(...xs);
    const bottom = Math.max(...ys);

    if (!settings) {
      return {
        quad: corners.map((point) => ({ ...point })),
        width: Math.max(1, Math.round(right - left)),
        height: Math.max(1, Math.round(bottom - top))
      };
    }

    // With the perspective warp off the renderer fills the region's upright
    // bounding box and lets the mask cut the shape out, so the editor must not
    // slant the artwork either.
    const quad = settings.use_perspective === false
      ? [
        { x: left, y: top },
        { x: right, y: top },
        { x: right, y: bottom },
        { x: left, y: bottom }
      ]
      : corners.map((point) => ({ ...point }));

    // The renderer sizes the artwork by the longer of each pair of opposite
    // sides, so a frame in perspective fits the same way here as there.
    const side = (a, b) => Math.hypot(quad[b].x - quad[a].x, quad[b].y - quad[a].y);
    return {
      quad,
      width: Math.max(2, Math.round(Math.max(side(0, 1), side(3, 2)))),
      height: Math.max(2, Math.round(Math.max(side(0, 3), side(1, 2))))
    };
  }


  function templateMaskUrl(template) {
    const maskName = template.mask_name || template.mask;
    return maskName
      ? `/api/admin/templates/${template.template_id}/asset/${maskName}?v=${maskVersion()}`
      : null;
  }

  /** Clip the artwork layer with the mask, pinned to the mockup underneath.
   *
   * The mask describes the mockup -- where its openings are -- so it is
   * anchored to the background image and never moves. Editing a frame changes
   * how artwork is warped inside an opening, never the opening itself.
   */
  // Which mask files are known to load. A mask that 404s masks everything out,
  // so one that fails is dropped rather than left hiding the artwork.
  const maskAvailability = new Map();

  function applyOverlayMask(overlayDiv, maskUrl, rect) {
    if (!maskUrl || maskAvailability.get(maskUrl) === "missing") {
      overlayDiv.style.maskImage = "";
      overlayDiv.style.webkitMaskImage = "";
      return;
    }
    if (!maskAvailability.has(maskUrl)) {
      maskAvailability.set(maskUrl, "checking");
      const probe = new Image();
      probe.onload = () => maskAvailability.set(maskUrl, "ready");
      probe.onerror = () => {
        maskAvailability.set(maskUrl, "missing");
        drawSelection();
      };
      probe.src = maskUrl;
    }
    const size = `${rect.width}px ${rect.height}px`;
    const position = `${rect.left}px ${rect.top}px`;
    overlayDiv.style.maskImage = `url("${maskUrl}")`;
    overlayDiv.style.webkitMaskImage = `url("${maskUrl}")`;
    // The mask is a greyscale image with no alpha channel, and CSS masks read
    // alpha by default -- which would be opaque everywhere and clip nothing.
    // Read it as luminance instead: white shows the artwork, black hides it.
    overlayDiv.style.maskMode = "luminance";
    overlayDiv.style.webkitMaskSourceType = "luminance";
    overlayDiv.style.maskRepeat = "no-repeat";
    overlayDiv.style.webkitMaskRepeat = "no-repeat";
    overlayDiv.style.maskSize = size;
    overlayDiv.style.webkitMaskSize = size;
    overlayDiv.style.maskPosition = position;
    overlayDiv.style.webkitMaskPosition = position;
  }

  function renderGreenFrameArtworkOverlay(template, image) {
    const overlayDiv = $("selectionImageOverlay");
    const overlayImg = $("selectionOverlayImg");
    const rect = getRenderedImageRect(image);
    if (!overlayDiv || !overlayImg || !rect || !state.selectionStyle.overlayImage) return;

    overlayDiv.querySelectorAll(".green-frame-region-overlay").forEach((node) => node.remove());
    overlayDiv.classList.remove("hidden");
    overlayDiv.style.left = "0";
    overlayDiv.style.top = "0";
    overlayDiv.style.width = "100%";
    overlayDiv.style.height = "100%";
    overlayDiv.style.transform = "none";
    overlayDiv.style.overflow = "visible";
    overlayImg.classList.add("hidden");
    overlayImg.src = state.selectionStyle.overlayImage;

    // A mask-backed template renders through the green-frame pipeline, whose
    // own settings override the template ones. Read the same ones the renderer
    // will -- fit mode, perspective warp, coverage envelope -- or the editor
    // places the artwork a different way than the finished mockup does.
    // parse_green_frame_settings reads them off the template's own green-frame
    // effect and falls back to the template fit mode, so read them from the
    // same place; the panel controls write straight into the template, so this
    // is still what the admin has just picked.
    const greenSettings = greenFrameSettings(template.effects);
    // Only a template that renders through the green pipeline is laid out by
    // the green settings; everywhere else the renderer fits the artwork to the
    // frame's bounding box and warps it straight onto the corners.
    const placementSettings = usesGreenFramePipeline(template) ? greenSettings : null;
    // parse_green_frame_settings falls back to the template's own fit mode when
    // the green effect does not name one, so read the effect itself rather than
    // the panel defaults -- those carry "cover", which cropped the sides off
    // artwork on a template set to stretch.
    const greenFitMode = template.effects && template.effects.green_frame_mockups
      && template.effects.green_frame_mockups.fit_mode;
    const templateFit = ($("fitMode") && $("fitMode").value) || template.fit_mode;
    const rawFitMode = (placementSettings && greenFitMode) || templateFit || "cover";
    const artworkScale = Number(($("greenArtworkScale") && $("greenArtworkScale").value) || 100) / 100;
    const offsetX = Number(($("greenOffsetX") && $("greenOffsetX").value) || 0) / 100;
    const offsetY = Number(($("greenOffsetY") && $("greenOffsetY").value) || 0) / 100;
    const naturalW = state.selectionStyle.overlayImageWidth || overlayImg.naturalWidth || 100;
    const naturalH = state.selectionStyle.overlayImageHeight || overlayImg.naturalHeight || 100;

    // With a mask in play the artwork is drawn oversized and trimmed by it, so
    // the canvas shows the mask's true outline instead of a four-corner
    // approximation that leaves the placeholder peeking out at the edges.
    overlayDiv.style.maskImage = "";
    overlayDiv.style.webkitMaskImage = "";
    // The mask only ever removes from what is drawn, so drawing on the frame's
    // own corners is what keeps the editing frame an exact bound on the image:
    // never larger than the frame, never smaller.
    applyOverlayMask(overlayDiv, templateMaskUrl(template), rect);

    greenFrameOverlayRegions(template).forEach((region) => {
      const corners = region.corners;
      if (!corners || corners.length < 4) return;

      const placement = greenFrameArtworkPlacement(corners, placementSettings);
      const displayPoints = placement.quad.map(p => ({
        x: (p.x / template.canvas_width) * rect.width,
        y: (p.y / template.canvas_height) * rect.height
      }));
      const quadW = placement.width;
      const quadH = placement.height;

      const fitMode = resolveFitMode(rawFitMode, naturalW, naturalH, quadW, quadH);

      const regionDiv = document.createElement("div");
      regionDiv.className = "green-frame-region-overlay";
      regionDiv.style.position = "absolute";
      regionDiv.style.width = `${quadW}px`;
      regionDiv.style.height = `${quadH}px`;
      regionDiv.style.left = `${rect.left}px`;
      regionDiv.style.top = `${rect.top}px`;
      regionDiv.style.transformOrigin = "0 0";
      regionDiv.style.overflow = "hidden";
      regionDiv.style.transform = `matrix3d(${getMatrix3d(quadW, quadH, displayPoints[0], displayPoints[1], displayPoints[2], displayPoints[3]).join(",")})`;

      const regionImg = document.createElement("img");
      regionImg.src = state.selectionStyle.overlayImage;
      let baseW = quadW;
      let baseH = quadH;
      if (fitMode === "stretch") {
        baseW = quadW;
        baseH = quadH;
      } else if (fitMode === "contain") {
        const imageRatio = naturalW / naturalH;
        const containerRatio = quadW / quadH;
        if (imageRatio > containerRatio) {
          baseW = quadW;
          baseH = quadW / imageRatio;
        } else {
          baseH = quadH;
          baseW = quadH * imageRatio;
        }
      } else { // "cover"
        const imageRatio = naturalW / naturalH;
        const containerRatio = quadW / quadH;
        if (imageRatio > containerRatio) {
          baseH = quadH;
          baseW = quadH * imageRatio;
        } else {
          baseW = quadW;
          baseH = quadW / imageRatio;
        }
      }
      const scaledW = baseW * artworkScale;
      const scaledH = baseH * artworkScale;
      regionImg.style.position = "absolute";
      regionImg.style.width = `${scaledW}px`;
      regionImg.style.height = `${scaledH}px`;
      regionImg.style.left = `${(quadW - scaledW) / 2 + (offsetX * quadW) / 2}px`;
      regionImg.style.top = `${(quadH - scaledH) / 2 + (offsetY * quadH) / 2}px`;
      regionImg.style.objectFit = "fill";
      regionImg.style.maxWidth = "none";
      regionImg.style.maxHeight = "none";
      regionImg.style.display = "block";
      regionDiv.appendChild(regionImg);
      overlayDiv.appendChild(regionDiv);
    });
  }

  /** Draw an editable frame over every detected region: a polygon with a
   * crosshair handle on each corner.
   *
   * A frame is a frame however detection found it, so a mask-backed template
   * -- GREEN FRAMES, COLOR PICK, FRAME POINTS -- is edited exactly the way a
   * multi-frame one is. Only the delete badge is held back where there is a
   * single frame: removing it would leave the template with none.
   */
  function renderRegionFrames(template, regions, rect, showDelete) {
    const multiGroup = $("multiRegionSvgGroup");
    if (!multiGroup) return;
    multiGroup.classList.remove("hidden");
    multiGroup.innerHTML = "";

    const style = state.selectionStyle;
    const strokeColor = style.polygonColor || "#ed6f5c";
    const strokeWidth = style.polygonWidth || 2;
    const fillColor = style.polygonColor || "#ed6f5c";
    const fillOpacity = (style.polygonOpacity || 15) / 100;
    const crossColor = style.crossColor || "#ed6f5c";
    const crossStrokeWidth = style.crossWidth || 1.5;
    const crossOpacity = (style.crossOpacity || 100) / 100;
    const halfSize = 12 / state.zoom;
    const hitboxR = 14 / state.zoom;
    const badgeRadius = 11 / state.zoom;
    const deleteBadges = [];

    regions.forEach((region, rIdx) => {
      const corners = usableCorners(region.corners) ? region.corners
        : usableCorners(region.inner_corners) ? region.inner_corners
          : areaCorners(region);
      if (!usableCorners(region.corners)) region.corners = corners;

      const displayPoints = corners.map(p => ({
        x: (p.x / template.canvas_width) * rect.width,
        y: (p.y / template.canvas_height) * rect.height
      }));
      const pointsStr = displayPoints.map(p => `${p.x},${p.y}`).join(" ");

      const poly = document.createElementNS("http://www.w3.org/2000/svg", "polygon");
      poly.setAttribute("points", pointsStr);
      poly.setAttribute("class", "multi-region-polygon");
      poly.dataset.regionIndex = String(rIdx);
      poly.style.fill = fillColor;
      poly.style.fillOpacity = String(fillOpacity);
      poly.style.stroke = strokeColor;
      poly.style.strokeWidth = `${strokeWidth}px`;
      poly.style.cursor = "move";
      multiGroup.appendChild(poly);

      corners.forEach((p, cIdx) => {
        const cx = (p.x / template.canvas_width) * rect.width;
        const cy = (p.y / template.canvas_height) * rect.height;

        const g = document.createElementNS("http://www.w3.org/2000/svg", "g");
        g.setAttribute("class", "handle-group");
        g.dataset.regionIndex = String(rIdx);
        g.dataset.cornerIndex = String(cIdx);

        const hLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
        hLine.setAttribute("class", "cross-line h-line");
        hLine.setAttribute("x1", cx - halfSize);
        hLine.setAttribute("x2", cx + halfSize);
        hLine.setAttribute("y1", cy);
        hLine.setAttribute("y2", cy);
        hLine.style.stroke = crossColor;
        hLine.style.strokeWidth = `${crossStrokeWidth}px`;
        hLine.style.opacity = String(crossOpacity);
        hLine.style.pointerEvents = "none";

        const vLine = document.createElementNS("http://www.w3.org/2000/svg", "line");
        vLine.setAttribute("class", "cross-line v-line");
        vLine.setAttribute("x1", cx);
        vLine.setAttribute("x2", cx);
        vLine.setAttribute("y1", cy - halfSize);
        vLine.setAttribute("y2", cy + halfSize);
        vLine.style.stroke = crossColor;
        vLine.style.strokeWidth = `${crossStrokeWidth}px`;
        vLine.style.opacity = String(crossOpacity);
        vLine.style.pointerEvents = "none";

        const circle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
        circle.setAttribute("class", "svg-handle handle-hitbox");
        circle.setAttribute("cx", cx);
        circle.setAttribute("cy", cy);
        circle.setAttribute("r", hitboxR);
        circle.dataset.regionIndex = String(rIdx);
        circle.dataset.cornerIndex = String(cIdx);
        circle.style.cursor = "crosshair";
        circle.style.fill = "transparent";

        g.appendChild(hLine);
        g.appendChild(vLine);
        g.appendChild(circle);
        multiGroup.appendChild(g);
      });

      if (displayPoints.length > 0) {
        const minX = Math.min(...displayPoints.map(p => p.x));
        const maxX = Math.max(...displayPoints.map(p => p.x));
        const minY = Math.min(...displayPoints.map(p => p.y));

        const tag = document.createElementNS("http://www.w3.org/2000/svg", "text");
        tag.setAttribute("class", "svg-zone-tag");
        tag.setAttribute("x", minX);
        tag.setAttribute("y", minY - 8);
        tag.textContent = `Frame ${rIdx + 1}`;
        multiGroup.appendChild(tag);

        // Delete badges are drawn after every region so a neighbouring
        // frame's polygon can never sit on top of one and eat the click.
        deleteBadges.push({ rIdx, x: maxX, y: minY - badgeRadius - 2 });
      }
    });

    if (showDelete) deleteBadges.forEach(({ rIdx, x, y }) => {
      const badgeG = document.createElementNS("http://www.w3.org/2000/svg", "g");
      badgeG.setAttribute("class", "region-delete-badge");
      badgeG.style.cursor = "pointer";
      // The parent SVG sets pointer-events:none; opt back in explicitly so
      // the badge stays clickable even against a stale cached stylesheet.
      badgeG.style.pointerEvents = "auto";

      const badgeCircle = document.createElementNS("http://www.w3.org/2000/svg", "circle");
      badgeCircle.setAttribute("cx", x);
      badgeCircle.setAttribute("cy", y);
      badgeCircle.setAttribute("r", badgeRadius);
      badgeCircle.setAttribute("fill", "#ef4444");
      badgeCircle.setAttribute("stroke", "#ffffff");
      badgeCircle.setAttribute("stroke-width", 1.5 / state.zoom);

      const badgeText = document.createElementNS("http://www.w3.org/2000/svg", "text");
      badgeText.setAttribute("x", x);
      badgeText.setAttribute("y", y + badgeRadius * 0.36);
      badgeText.setAttribute("text-anchor", "middle");
      badgeText.setAttribute("fill", "#ffffff");
      badgeText.setAttribute("font-size", `${badgeRadius * 1.25}px`);
      badgeText.setAttribute("font-weight", "bold");
      badgeText.setAttribute("pointer-events", "none");
      badgeText.textContent = "✕";

      const badgeTitle = document.createElementNS("http://www.w3.org/2000/svg", "title");
      badgeTitle.textContent = `Remove frame ${rIdx + 1}`;

      badgeG.appendChild(badgeCircle);
      badgeG.appendChild(badgeText);
      badgeG.appendChild(badgeTitle);
      // Swallow the pointerdown so the canvas drag handler never starts a
      // gesture here; the click that follows then reaches the badge.
      badgeG.addEventListener("pointerdown", (e) => {
        e.preventDefault();
        e.stopPropagation();
      });
      badgeG.addEventListener("click", (e) => {
        e.preventDefault();
        e.stopPropagation();
        removeDetectedRegion(rIdx);
      });
      multiGroup.appendChild(badgeG);
    });
  }

  function drawSelection() {
    if (state.globalOverlayPlacementActive) {
      $("selectionSvg").classList.add("hidden");
      renderGlobalOverlayPlacement();
    }
    if (maskDetectState.active) {
      clearDetectionOverlays();
      return;
    }
    if (state.isPreviewingMockup) {
      $("selectionSvg").classList.add("hidden");
      return;
    }
    const template = state.selected;
    const image = $("canvasImage");
    const selectionSvg = $("selectionSvg");
    if (!template || !template.artwork_area || !image.naturalWidth) {
      selectionSvg.classList.add("hidden");
      return;
    }
    // Overlays are sized from the canvas image, so one belonging to another
    // template would scale the artwork by the wrong mockup's aspect. Draw when
    // the right one has loaded instead.
    if (image.src && image.src.indexOf(`/templates/${template.template_id}/`) === -1) {
      selectionSvg.classList.add("hidden");
      image.addEventListener("load", () => drawSelection(), { once: true });
      return;
    }

    const rect = getRenderedImageRect(image);
    if (!rect) {
      selectionSvg.classList.add("hidden");
      return;
    }

    // Align SVG overlay exactly with the rendered pixels of the background image
    selectionSvg.style.left = `${rect.left}px`;
    selectionSvg.style.top = `${rect.top}px`;
    selectionSvg.style.width = `${rect.width}px`;
    selectionSvg.style.height = `${rect.height}px`;
    applySelectionStyle();
    const multiGroup = $("multiRegionSvgGroup");

    if (isGreenFrameTemplate(template)) {
      // A mask-backed frame is still a frame: draw it with the same polygon
      // and corner handles a multi-frame template gets, so a detection made
      // by colour or by seed points can be adjusted like any other.
      const maskedRegions = template.raw_artwork_area && Array.isArray(template.raw_artwork_area.regions)
        ? template.raw_artwork_area.regions.filter((region) => region && (region.corners || region.width))
        : [];
      if (maskedRegions.length) {
        selectionSvg.classList.remove("hidden");
        renderRegionFrames(template, maskedRegions, rect, maskedRegions.length > 1);
      } else {
        selectionSvg.classList.add("hidden");
        if (multiGroup) {
          multiGroup.innerHTML = "";
          multiGroup.classList.add("hidden");
        }
      }
      $("selectionPolygon").classList.add("hidden");
      for (let i = 0; i < 4; i++) {
        const hg = $(`handle_group_${i}`);
        if (hg) hg.classList.add("hidden");
        const rh = $(`raw_handle_${i}`);
        if (rh) rh.classList.add("hidden");
      }
      if ($("svgZoneTag")) $("svgZoneTag").classList.add("hidden");
      if ($("svgRawZoneTag")) $("svgRawZoneTag").classList.add("hidden");
      if ($("rawSelectionPolygon")) $("rawSelectionPolygon").classList.add("hidden");

      if (state.selectionStyle.overlayImage) {
        const rendered = $("selectionRenderedMockup");
        const hasVisibleRender = Boolean(rendered && rendered.src && !rendered.classList.contains("hidden"));
        if (state.drag || state.greenFrameDrag || !hasVisibleRender || detectionReviewState.active) {
          if (rendered) rendered.classList.add("hidden");
          renderGreenFrameArtworkOverlay(template, image);
        } else {
          $("selectionImageOverlay").classList.add("hidden");
        }
        if (!state.drag && !state.greenFrameDrag && !detectionReviewState.active) {
          ensureGreenFrameRegularRender(template);
        }
      } else {
        $("selectionImageOverlay").classList.add("hidden");
        if ($("selectionRenderedMockup")) {
          $("selectionRenderedMockup").classList.add("hidden");
          $("selectionRenderedMockup").src = "";
        }
      }
      return;
    }

    if (isMultiRegionTemplate(template)) {
      selectionSvg.classList.remove("hidden");
      $("selectionPolygon").classList.add("hidden");
      for (let i = 0; i < 4; i++) {
        const hg = $(`handle_group_${i}`);
        if (hg) hg.classList.add("hidden");
        const rh = $(`raw_handle_${i}`);
        if (rh) rh.classList.add("hidden");
      }
      if ($("svgZoneTag")) $("svgZoneTag").classList.add("hidden");
      if ($("svgRawZoneTag")) $("svgRawZoneTag").classList.add("hidden");
      if ($("rawSelectionPolygon")) $("rawSelectionPolygon").classList.add("hidden");

      renderRegionFrames(template, template.raw_artwork_area.regions, rect, true);
    } else {
      if (multiGroup) {
        multiGroup.innerHTML = "";
        multiGroup.classList.add("hidden");
      }
      $("selectionPolygon").classList.remove("hidden");
      for (let i = 0; i < 4; i++) {
        const hg = $(`handle_group_${i}`);
        if (hg) hg.classList.remove("hidden");
        const rh = $(`raw_handle_${i}`);
        if (rh) rh.classList.add("hidden");
      }
      if ($("svgZoneTag")) $("svgZoneTag").classList.remove("hidden");
      if ($("svgRawZoneTag")) $("svgRawZoneTag").classList.add("hidden");
      if ($("rawSelectionPolygon")) $("rawSelectionPolygon").classList.add("hidden");
      selectionSvg.classList.remove("hidden");

      const area = template.artwork_area;
      const corners = areaCorners(area);
      const displayPoints = corners.map((p) => ({
        x: (p.x / template.canvas_width) * rect.width,
        y: (p.y / template.canvas_height) * rect.height
      }));
      const pointsStr = displayPoints.map((p) => `${p.x},${p.y}`).join(" ");
      $("selectionPolygon").setAttribute("points", pointsStr);

      corners.forEach((p, idx) => {
        const cx = (p.x / template.canvas_width) * rect.width;
        const cy = (p.y / template.canvas_height) * rect.height;
        const handle = $(`handle_${idx}`);
        if (handle) {
          handle.setAttribute("cx", cx);
          handle.setAttribute("cy", cy);
          handle.setAttribute("r", 14 / state.zoom);
        }

        const hLine = $(`h_line_${idx}`);
        const vLine = $(`v_line_${idx}`);
        if (hLine && vLine) {
          const halfSize = 12 / state.zoom;
          hLine.setAttribute("x1", cx - halfSize);
          hLine.setAttribute("x2", cx + halfSize);
          hLine.setAttribute("y1", cy);
          hLine.setAttribute("y2", cy);

          vLine.setAttribute("x1", cx);
          vLine.setAttribute("x2", cx);
          vLine.setAttribute("y1", cy - halfSize);
          vLine.setAttribute("y2", cy + halfSize);
        }
      });

      const rawPolygon = $("rawSelectionPolygon");
      const rawTag = $("svgRawZoneTag");
      const rawArea = template.raw_artwork_area;
      const rawBox = rawArea && Number.isFinite(Number(rawArea.x)) && Number.isFinite(Number(rawArea.width))
        ? areaCorners(rawArea)
        : null;
      const rawCorners = usableCorners(rawArea && rawArea.corners) ? rawArea.corners : rawBox;
      if (rawCorners && rawPolygon) {
        const rawPointsStr = rawCorners.map(p => {
          const cx = (p.x / template.canvas_width) * rect.width;
          const cy = (p.y / template.canvas_height) * rect.height;
          return `${cx},${cy}`;
        }).join(" ");
        rawPolygon.setAttribute("points", rawPointsStr);
        rawPolygon.classList.remove("hidden");

        if (rawTag && rawCorners.length > 0) {
          const tRawX = (rawCorners[0].x / template.canvas_width) * rect.width;
          const tRawY = (rawCorners[0].y / template.canvas_height) * rect.height - 25;
          rawTag.setAttribute("x", tRawX);
          rawTag.setAttribute("y", tRawY);
          rawTag.classList.remove("hidden");
        }

        rawCorners.forEach((p, idx) => {
          const cx = (p.x / template.canvas_width) * rect.width;
          const cy = (p.y / template.canvas_height) * rect.height;
          const rawMarker = $(`raw_handle_${idx}`);
          if (rawMarker) {
            rawMarker.setAttribute("cx", cx);
            rawMarker.setAttribute("cy", cy);
            rawMarker.classList.remove("hidden");
          }
        });
      } else {
        if (rawPolygon) rawPolygon.classList.add("hidden");
        if (rawTag) rawTag.classList.add("hidden");
        for (let idx = 0; idx < 4; idx++) {
          const rawMarker = $(`raw_handle_${idx}`);
          if (rawMarker) rawMarker.classList.add("hidden");
        }
      }

      if (corners.length > 0) {
        const tX = (corners[0].x / template.canvas_width) * rect.width;
        const tY = (corners[0].y / template.canvas_height) * rect.height - 10;
        const tag = $("svgZoneTag");
        if (tag) {
          tag.setAttribute("x", tX);
          tag.setAttribute("y", tY);
        }
      }
    }

    if (state.selectionStyle.overlayImage) {
      renderGreenFrameArtworkOverlay(template, image);
    } else {
      $("selectionImageOverlay").classList.add("hidden");
      if ($("selectionRenderedMockup")) {
        $("selectionRenderedMockup").classList.add("hidden");
        $("selectionRenderedMockup").src = "";
      }
    }
  }

  function beginDrag(event) {
    if (!state.selected || !state.selected.artwork_area || state.busy) return;
    if (state.polygonLocked) return;

    const target = event.target;
    const template = state.selected;

    // Check if dragging a multi-region frame. A mask-backed template edits the
    // same way: its frames live in raw_artwork_area.regions too, and that is
    // what the renderer reads, so dragging one moves the artwork it holds.
    if (target.dataset && target.dataset.regionIndex !== undefined) {
      event.preventDefault();
      const rIdx = Number(target.dataset.regionIndex);
      const isCorner = target.classList.contains("svg-handle");
      const cIdx = isCorner ? Number(target.dataset.cornerIndex) : -1;
      const region = template.raw_artwork_area?.regions?.[rIdx];
      if (!region) return;
      if (!region.corners) region.corners = areaCorners(region);

      target.setPointerCapture(event.pointerId);
      state.drag = {
        isMulti: true,
        regionIndex: rIdx,
        handle: isCorner ? "corner" : "move",
        cornerIndex: cIdx,
        startX: event.clientX,
        startY: event.clientY,
        corners: region.corners.map(c => ({ ...c }))
      };
      return;
    }

    // Below here the whole artwork area is dragged as one rectangle, which a
    // mask-backed template does not use: its geometry lives in its regions.
    if (isGreenFrameTemplate(template)) return;

    let handle = "move";
    let index = -1;

    if (target.classList.contains("svg-handle")) {
      handle = "corner";
      index = Number(target.dataset.index);
    } else if (target.id !== "selectionPolygon") {
      return;
    }

    event.preventDefault();
    target.setPointerCapture(event.pointerId);

    if (!state.selected.artwork_area.corners) {
      const area = state.selected.artwork_area;
      state.selected.artwork_area.corners = [
        { x: area.x, y: area.y },
        { x: area.x + area.width, y: area.y },
        { x: area.x + area.width, y: area.y + area.height },
        { x: area.x, y: area.y + area.height }
      ];
    }

    state.drag = {
      isMulti: false,
      startX: event.clientX,
      startY: event.clientY,
      corners: state.selected.artwork_area.corners.map(c => ({ ...c })),
      handle: handle,
      cornerIndex: index
    };
  }

  function continueDrag(event) {
    if (!state.drag || !state.selected) return;
    const template = state.selected;
    const image = $("canvasImage");
    const rect = getRenderedImageRect(image);
    if (!rect) return;
    const dx = Math.round(((event.clientX - state.drag.startX) / state.zoom) * template.canvas_width / rect.width);
    const dy = Math.round(((event.clientY - state.drag.startY) / state.zoom) * template.canvas_height / rect.height);

    if (state.drag.isMulti) {
      const rIdx = state.drag.regionIndex;
      const region = template.raw_artwork_area.regions[rIdx];
      let nextCorners = state.drag.corners.map(c => ({ ...c }));

      if (state.drag.handle === "move") {
        nextCorners.forEach(p => {
          p.x = Math.max(0, Math.min(template.canvas_width, p.x + dx));
          p.y = Math.max(0, Math.min(template.canvas_height, p.y + dy));
        });
      } else if (state.drag.handle === "corner") {
        const cIdx = state.drag.cornerIndex;
        nextCorners[cIdx].x = Math.max(0, Math.min(template.canvas_width, state.drag.corners[cIdx].x + dx));
        nextCorners[cIdx].y = Math.max(0, Math.min(template.canvas_height, state.drag.corners[cIdx].y + dy));
      }

      region.corners = nextCorners;
      // The opening belongs to the mockup and never moves; this only records
      // that the artwork inside it is now placed by hand.
      template.raw_artwork_area.corners_edited = true;
      const xs = nextCorners.map(c => c.x);
      const ys = nextCorners.map(c => c.y);
      region.x = Math.min(...xs);
      region.y = Math.min(...ys);
      region.width = Math.max(...xs) - region.x;
      region.height = Math.max(...ys) - region.y;

      const allXs = template.raw_artwork_area.regions.flatMap(r => (r.corners || areaCorners(r)).map(c => c.x));
      const allYs = template.raw_artwork_area.regions.flatMap(r => (r.corners || areaCorners(r)).map(c => c.y));
      template.artwork_area.x = Math.min(...allXs);
      template.artwork_area.y = Math.min(...allYs);
      template.artwork_area.width = Math.max(...allXs) - template.artwork_area.x;
      template.artwork_area.height = Math.max(...allYs) - template.artwork_area.y;
      if (template.raw_artwork_area.regions.length === 1) {
        // Detection writes the single frame's corners into the artwork area as
        // well; keep the two telling the same story once it has been dragged.
        template.artwork_area.corners = nextCorners.map(c => ({ ...c }));
      }

      updateCoordinateLabels();
      drawSelection();
      $("proposalState").textContent = `Adjusted Frame ${rIdx + 1} locally. Save draft or approve to keep.`;
      return;
    }

    let nextCorners = state.drag.corners.map(c => ({ ...c }));

    if (state.drag.handle === "move") {
      let canMove = true;
      for (const p of nextCorners) {
        const nx = p.x + dx;
        const ny = p.y + dy;
        if (nx < 0 || nx > template.canvas_width || ny < 0 || ny > template.canvas_height) {
          canMove = false;
          break;
        }
      }
      if (canMove) {
        nextCorners.forEach(p => {
          p.x += dx;
          p.y += dy;
        });
      }
    } else if (state.drag.handle === "corner") {
      const idx = state.drag.cornerIndex;
      const nx = Math.max(0, Math.min(template.canvas_width, state.drag.corners[idx].x + dx));
      const ny = Math.max(0, Math.min(template.canvas_height, state.drag.corners[idx].y + dy));
      nextCorners[idx].x = nx;
      nextCorners[idx].y = ny;
    }

    template.artwork_area.corners = nextCorners;

    const xs = nextCorners.map(c => c.x);
    const ys = nextCorners.map(c => c.y);
    const minX = Math.min(...xs);
    const maxX = Math.max(...xs);
    const minY = Math.min(...ys);
    const maxY = Math.max(...ys);
    template.artwork_area.x = minX;
    template.artwork_area.y = minY;
    template.artwork_area.width = maxX - minX;
    template.artwork_area.height = maxY - minY;

    updateCoordinateLabels();
    drawSelection();
    $("proposalState").textContent = "Adjusted locally. Save draft or approve to keep this perspective frame.";
  }

  function endDrag() {
    if (state.drag && state.selected) {
      greenRegularRenderUrlCache.clear();
      if ($("selectionRenderedMockup")) {
        $("selectionRenderedMockup").classList.add("hidden");
        $("selectionRenderedMockup").src = "";
      }
      persistTemplateState(state.selected);
      drawSelection();
    }
    state.drag = null;
  }


  function setBusy(active) {
    state.busy = active;
    $("analysisOverlay").classList.toggle("hidden", !active);
    $("detectButton").disabled = active || !state.selected;
    $("detectButton").textContent = active ? "Analyzing..." : "Detect frame";
    $("saveButton").disabled = active;
    $("approveButton").disabled = active;
    $("publishButton").disabled = active;
    if ($("resetDetectionButton")) $("resetDetectionButton").disabled = active || !state.selected;
    updateDetectionModeSwitch();
    renderQueue();
  }


  async function importFiles(files) {
    if (!files.length) return;
    if (!state.selectedCategory) {
      toast("Create or select a category before importing.");
      return;
    }
    const body = new FormData();
    body.append("category_id", state.selectedCategory.id);
    [...files].forEach((file) => body.append("mockups", file));
    try {
      setStatus("Importing mockups...");
      const payload = await api("/api/admin/templates/import", { method: "POST", body });
      await loadCategories(state.selectedCategory.id);
      await loadTemplates(payload.templates[0].template_id);
      toast(`${payload.templates.length} mockup images imported`);
      setStatus("Import complete");
    } catch (error) {
      setStatus("Import failed", true);
      toast(error.message);
    }
  }

  async function saveTemplate(showToast = true, skipReload = false) {
    if (!state.selected || !state.selected.artwork_area) throw new Error("Define an artwork area first.");
    const payload = await api(`/api/admin/templates/${state.selected.template_id}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: $("templateName").value,
        category_id: Number($("categorySelect").value),
        artwork_area: state.selected.artwork_area,
        fit_mode: $("fitMode").value,
        effects: state.selected.effects || null,
        raw_artwork_area: state.selected.raw_artwork_area || null,
        mask_name: state.selected.mask_name || null
      })
    });
    state.selected = payload.template;
    if (!skipReload) {
      await loadCategories(state.selected.category_id);
      await loadTemplates(state.selected.template_id);
    }
    if (showToast) toast("Template draft saved");
    return state.selected;
  }

  async function persistTemplateState(template, options = {}) {
    if (!template || !template.artwork_area || (state.busy && !options.force)) return;
    try {
      const payload = await api(`/api/admin/templates/${template.template_id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: template.name,
          category_id: template.category_id,
          artwork_area: template.artwork_area,
          fit_mode: template.fit_mode,
          effects: template.effects || null,
          raw_artwork_area: template.raw_artwork_area || null,
          mask_name: template.mask_name || null
        })
      });
      const idx = state.templates.findIndex(t => t.template_id === template.template_id);
      if (idx !== -1) {
        state.templates[idx] = payload.template;
      }
    } catch (e) {
      console.error("Auto-save failed:", e);
    }
  }

  function autoSaveCurrent() {
    if (state.selected && !state.busy) {
      const nameInput = $("templateName");
      const catSelect = $("categorySelect");
      const fitModeSelect = $("fitMode");
      if (nameInput) state.selected.name = nameInput.value;
      if (catSelect) state.selected.category_id = Number(catSelect.value);
      if (fitModeSelect) state.selected.fit_mode = fitModeSelect.value;
      persistTemplateState(state.selected);
    }
  }


  async function approveTemplate() {
    if (!state.selected) return;
    try {
      setStatus("Publishing approved template...");
      await saveTemplate(false);
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/activate`, { method: "POST" });
      state.selected = payload.template;
      greenRegularRenderUrlCache.clear();
      if ($("selectionRenderedMockup")) {
        $("selectionRenderedMockup").classList.add("hidden");
        $("selectionRenderedMockup").src = "";
      }
      await loadCategories(state.selected.category_id);
      await loadTemplates(state.selected.template_id);
      $("proposalState").textContent = "Approved rectangle is active in the public API.";
      toast("Template approved and published");
      setStatus("Template published");
    } catch (error) {
      setStatus("Publish failed", true);
      toast(error.message);
    }
  }


  function showSystemDialog({
    title = "Confirm action",
    message = "",
    confirmText = "OK",
    cancelText = "Cancel",
    isDanger = false,
    showCancel = true,
    inputPlaceholder = null,
    defaultValue = ""
  } = {}) {
    return new Promise((resolve) => {
      const dialog = $("systemDialog");
      const titleEl = $("systemDialogTitle");
      const messageEl = $("systemDialogMessage");
      const inputEl = $("systemDialogInput");
      const cancelBtn = $("systemDialogCancel");
      const confirmBtn = $("systemDialogConfirm");

      if (!dialog || !titleEl || !messageEl || !confirmBtn) {
        resolve(false);
        return;
      }

      titleEl.textContent = title;
      messageEl.textContent = message;
      confirmBtn.textContent = confirmText;
      confirmBtn.className = isDanger ? "btn danger" : "btn primary";

      if (cancelBtn) {
        cancelBtn.textContent = cancelText;
        cancelBtn.classList.toggle("hidden", !showCancel);
      }

      const isPrompt = inputPlaceholder !== null;
      if (inputEl) {
        inputEl.classList.toggle("hidden", !isPrompt);
        if (isPrompt) {
          inputEl.placeholder = inputPlaceholder || "";
          inputEl.value = defaultValue || "";
        }
      }

      dialog.classList.add("open");
      if (isPrompt && inputEl) {
        setTimeout(() => inputEl.focus(), 50);
      } else {
        setTimeout(() => confirmBtn.focus(), 50);
      }

      function cleanup() {
        dialog.classList.remove("open");
        confirmBtn.onclick = null;
        if (cancelBtn) cancelBtn.onclick = null;
        dialog.onclick = null;
        document.removeEventListener("keydown", handleKeydown);
      }

      function handleConfirm() {
        cleanup();
        if (isPrompt && inputEl) {
          resolve(inputEl.value.trim());
        } else {
          resolve(true);
        }
      }

      function handleCancel() {
        cleanup();
        resolve(isPrompt ? null : false);
      }

      function handleKeydown(e) {
        if (e.key === "Escape" && showCancel) {
          e.preventDefault();
          handleCancel();
        } else if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          handleConfirm();
        }
      }

      confirmBtn.onclick = handleConfirm;
      if (cancelBtn) cancelBtn.onclick = handleCancel;
      dialog.onclick = (e) => {
        if (e.target === dialog && showCancel) handleCancel();
      };
      document.addEventListener("keydown", handleKeydown);
    });
  }

  function appConfirm(message, title = "Confirm action", isDanger = false, confirmText = "Confirm") {
    return showSystemDialog({
      title,
      message,
      confirmText,
      cancelText: "Cancel",
      isDanger,
      showCancel: true
    });
  }

  function appAlert(message, title = "Notice") {
    return showSystemDialog({
      title,
      message,
      confirmText: "OK",
      showCancel: false
    });
  }

  function openDeleteModal(template) {
    if (!template || state.busy) return;
    state.pendingDelete = template;
    $("deleteTarget").textContent = template.name;
    $("deleteMessage").textContent = template.status === "active"
      ? "This approved mockup will be removed from the import queue and the public API."
      : "This draft mockup will be removed from the import queue.";
    $("deleteModal").classList.add("open");
  }

  function closeDeleteModal() {
    if (state.busy) return;
    state.pendingDelete = null;
    $("deleteModal").classList.remove("open");
  }

  async function deleteTemplate() {
    const template = state.pendingDelete;
    if (!template || state.busy) return;
    try {
      state.busy = true;
      $("confirmDelete").disabled = true;
      $("cancelDelete").disabled = true;
      $("confirmDelete").textContent = "Deleting...";
      renderQueue();
      renderEditor();
      setStatus("Deleting mockup...");
      await api(`/api/admin/templates/${template.template_id}`, { method: "DELETE" });
      $("deleteModal").classList.remove("open");
      const nextSelected = state.selected && state.selected.template_id === template.template_id
        ? null
        : state.selected;
      state.pendingDelete = null;
      state.selected = nextSelected;
      await loadCategories(state.selectedCategory && state.selectedCategory.id);
      await loadTemplates(nextSelected && nextSelected.template_id);
      toast("Mockup deleted");
      setStatus("Mockup deleted");
    } catch (error) {
      setStatus("Delete failed", true);
      toast(error.message);
    } finally {
      state.busy = false;
      $("confirmDelete").disabled = false;
      $("cancelDelete").disabled = false;
      $("confirmDelete").textContent = "Delete mockup";
      renderQueue();
      renderEditor();
    }
  }

  async function detectFrame() {
    if (!state.selected) {
      toast("Select a mockup before running detection.");
      return;
    }

    // Detection is an editing action, and the editor cannot draw while a
    // rendered preview is on screen: drawSelection returns early in preview
    // mode, so the detected frames were left sitting on a blank canvas with no
    // artwork in them until the result was approved.
    if (state.isPreviewingMockup) await togglePreviewMode();

    // Always save state and clear overlays before any detection flow
    saveDetectionPreState();
    clearDetectionOverlays();

    // If classic detection is active, run the selected internal classic submode.
    if ((state.settings.DETECTION_PROVIDER || "classic") === "classic") {
      const submode = state.settings.CLASSIC_SUBMODE || "auto";
      if (submode === "green_frames") {
        runClassicGreenFramesDetection();
        return;
      }
      if (submode === "color_pick") {
        runColorPickMode();
        return;
      }
      if (submode === "frame_points") {
        runFramePointsMode();
        return;
      }
      startDetectionWizard();
      return;
    }

    // If Vertex AI is selected but known to be unavailable, fallback gracefully to classic
    if (state.settings.DETECTION_PROVIDER === "vertex" && state.providerHealth && state.providerHealth.vertex && !state.providerHealth.vertex.available) {
      const err = state.providerHealth.vertex.error || "Vertex AI connection unavailable";
      toast(`Vertex AI unavailable (${err}). Using Classic Detection.`);
      setStatus("Vertex AI unavailable, falling back to Classic", true);
      startDetectionWizard();
      return;
    }

    // Vertex AI / Local AI providers
    setBusy(true);
    const engineName = providerTitle(state.settings.DETECTION_PROVIDER || "classic");
    $("analysisLabel").textContent = `${engineName} is analyzing the frame`;
    setStatus(`${engineName} is detecting the artwork area...`);
    $("proposalState").textContent = "Analyzing the selected background image...";
    $("detectionResult").className = "rule result-rule";
    try {
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, { method: "POST", timeout: 35000 });
      setBusy(false);
      showDetectionReview(payload, { mode: "ai" });
    } catch (error) {
      setBusy(false);
      $("detectionResult").classList.add("error");
      $("detectionResult").textContent = error.message;
      $("proposalState").textContent = "Detection failed. Open detection settings or retry with Classic.";
      toast(error.message);
      setStatus("Detection failed", true);
    }
  }

  async function runClassicGreenFramesDetection() {
    setBusy(true);
    $("analysisLabel").textContent = "Classic green frames is analyzing the mockup";
    if ($("analysisSub")) $("analysisSub").textContent = "Building a green-screen mask and perspective corners.";
    setStatus("Detecting green frame mockup area...");
    $("detectionResult").className = "rule result-rule";
    try {
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, {
        method: "POST",
        body: JSON.stringify({ mode: "green_frames_mockups" })
      });
      setBusy(false);
      showDetectionReview(payload, { mode: "green_frames_mockups" });
    } catch (error) {
      setBusy(false);
      $("detectionResult").classList.add("error");
      $("detectionResult").textContent = error.message;
      $("proposalState").textContent = "Green frame detection failed. Try the standard classic mode or review manually.";
      toast(error.message);
      setStatus("Green frame detection failed", true);
    } finally {
      if ($("analysisSub")) $("analysisSub").textContent = "This can take several seconds.";
    }
  }

  // --- DETECTION SHARED INFRASTRUCTURE ---

  // Saved state for cancel/restore across ALL detection modes.
  const detectionReviewState = {
    active: false,
    prevTemplate: null,      // JSON snapshot before detection started
    pendingPayload: null,    // API response waiting for Accept/Retry/Cancel
    params: null,            // {mode, color, points, tolerance} for retry
    wizardApproved: false,   // set true when wizard accepts so closeWizard won't restore
  };

  function saveDetectionPreState() {
    detectionReviewState.prevTemplate = state.selected
      ? JSON.parse(JSON.stringify(state.selected))
      : null;
    detectionReviewState.wizardApproved = false;
  }

  /** Hide every overlay so only the bare mockup image is visible. */
  function clearDetectionOverlays() {
    if (state.greenFramePlacementActive) setGreenFramePlacementActive(false);
    if (state.globalOverlayPlacementActive) setGlobalOverlayPlacementActive(false);

    const svg = $("selectionSvg");
    if (svg) svg.classList.add("hidden");

    const rendered = $("selectionRenderedMockup");
    if (rendered) {
      rendered.classList.add("hidden");
      rendered.src = "";
    }

    const imageOverlay = $("selectionImageOverlay");
    if (imageOverlay) {
      imageOverlay.classList.add("hidden");
      imageOverlay.querySelectorAll(".green-frame-region-overlay").forEach(n => n.remove());
    }

    const greenLayer = $("greenFramePlacementLayer");
    if (greenLayer) greenLayer.classList.add("hidden");

    const globalLayer = $("globalOverlayPlacementLayer");
    if (globalLayer) globalLayer.classList.add("hidden");
  }

  /**
   * Show the detection result as a provisional preview.
   * The user must explicitly Accept before anything is committed.
   */
  function showDetectionReview(payload, params) {
    detectionReviewState.active = true;
    detectionReviewState.pendingPayload = payload;
    detectionReviewState.params = params;

    if ($("selectionRenderedMockup")) {
      $("selectionRenderedMockup").classList.add("hidden");
      $("selectionRenderedMockup").src = "";
    }
    greenRegularRenderUrlCache.clear();

    // Temporarily apply the result so the user can see it on the canvas.
    state.selected = { ...payload.template };
    if (payload.proposal?.raw_artwork_area) {
      state.selected.raw_artwork_area = payload.proposal.raw_artwork_area;
    }
    if (payload.proposal?.artwork_area) {
      state.selected.artwork_area = payload.proposal.artwork_area;
    }
    if (payload.template?.mask_name) {
      state.selected.mask_name = payload.template.mask_name;
    }
    updateTemplateInQueue(state.selected);
    renderEditor();


    const regions = Array.isArray(payload.proposal?.raw_artwork_area?.regions)
      ? payload.proposal.raw_artwork_area.regions
      : [];
    const regionCount = regions.length || 1;
    const mode = params.mode;

    // Tolerance slider — show for color/point modes, hide for green auto
    const tolRow = $("maskDetectToleranceRow");
    if (tolRow) {
      const showTol = mode === "color_pick" || mode === "frame_points";
      tolRow.classList.toggle("hidden", !showTol);
      if (showTol) {
        const tol = params.tolerance || DEFAULT_MASK_TOLERANCE;
        const tolInput = $("maskDetectTolerance");
        if (tolInput) tolInput.value = tol;
        const tolVal = $("maskDetectToleranceVal");
        if (tolVal) tolVal.textContent = String(tol);
      }
    }

    // Color swatch — only for color_pick
    if (mode === "color_pick" && params.color) {
      const [r, g, b] = params.color;
      const hex = `#${r.toString(16).padStart(2,"0")}${g.toString(16).padStart(2,"0")}${b.toString(16).padStart(2,"0")}`;
      $("maskDetectColorBox").style.background = hex;
      $("maskDetectColorLabel").textContent = `rgb(${r}, ${g}, ${b})`;
      $("maskDetectColorSwatch").classList.remove("hidden");
    } else {
      $("maskDetectColorSwatch").classList.add("hidden");
    }

    const actions = [
      { text: "Accept", class: "primary", onclick: () => acceptDetectionResult() },
      { text: "Retry", class: "secondary", onclick: () => retryDetection() },
    ];
    if (mode === "color_pick") {
      actions.push({ text: "New Color", class: "secondary", onclick: () => editColorPickMode() });
    } else if (mode === "frame_points") {
      actions.push({ text: "Edit Points", class: "secondary", onclick: () => editFramePointsMode() });
    }
    actions.push({ text: "Cancel", class: "danger", onclick: () => cancelDetection() });

    $("maskDetectionHud").classList.remove("hidden");
    $("proposalState").classList.add("hidden");

    updateMaskDetectUI(
      "REVIEW",
      `${regionCount} frame${regionCount !== 1 ? "s" : ""} detected`,
      "Review the highlighted frames. Accept to confirm or adjust settings and retry.",
      actions
    );
  }

  async function acceptDetectionResult() {
    if (!detectionReviewState.pendingPayload) { closeMaskDetectionHud(); return; }
    const payload = detectionReviewState.pendingPayload;
    detectionReviewState.active = false;
    detectionReviewState.prevTemplate = null;
    detectionReviewState.pendingPayload = null;
    detectionReviewState.params = null;

    greenRegularRenderUrlCache.clear();
    if ($("selectionRenderedMockup")) {
      $("selectionRenderedMockup").classList.add("hidden");
      $("selectionRenderedMockup").src = "";
    }

    // state.selected already has the new detection — just update the display
    if (payload.proposal) {
      $("confidence").textContent = confidenceLabel(payload.proposal.confidence);
      $("detectionResult").className = "rule result-rule success";
      $("detectionResult").textContent = `Detection: ${payload.proposal.reason || "Detection accepted."}`;
    }
    closeMaskDetectionHud();
    renderEditor();
    toast("Detection accepted");
    setStatus("Detection confirmed.");

    try {
      await persistTemplateState(state.selected, { force: true });
    } catch (err) {
      console.warn("Could not persist template state:", err);
    }
  }



  async function retryDetection() {
    if (!detectionReviewState.params) return;
    const tolerance = parseInt(($("maskDetectTolerance") || {}).value || String(DEFAULT_MASK_TOLERANCE), 10);
    const params = { ...detectionReviewState.params, tolerance };

    // Restore visual to clean state while re-detecting
    if (detectionReviewState.prevTemplate) {
      state.selected = JSON.parse(JSON.stringify(detectionReviewState.prevTemplate));
      updateTemplateInQueue(state.selected);
    }
    detectionReviewState.active = false;
    closeMaskDetectionHud();
    clearDetectionOverlays();

    if (params.mode === "color_pick" || params.mode === "frame_points") {
      await submitMaskDetection(params.mode, params);
    } else if (params.mode === "green_frames_mockups") {
      await runClassicGreenFramesDetection();
    } else {
      await detectFrame();
    }

  }

  function exitAllDetectionModes() {
    disableCanvasClickListener();
    disableMaskDetectClickListener();
    clearMaskDetectDots();

    maskDetectState.active = false;
    maskDetectState.mode = null;
    maskDetectState.sampledColor = null;
    maskDetectState.points = [];

    wizardState.active = false;
    wizardState.step = 0;
    wizardState.layers = [];
    wizardState.layerIndex = 0;
    wizardState.proposedCorners = null;

    detectionReviewState.active = false;

    if ($("maskDetectionHud")) $("maskDetectionHud").classList.add("hidden");
    if ($("detectionWizardHud")) $("detectionWizardHud").classList.add("hidden");
    if ($("maskDetectColorSwatch")) $("maskDetectColorSwatch").classList.add("hidden");
    if ($("maskDetectToleranceRow")) $("maskDetectToleranceRow").classList.add("hidden");
    if ($("proposalState")) $("proposalState").classList.remove("hidden");
    if ($("stage")) $("stage").classList.remove("stage-cursor-crosshair");

    clearDetectionOverlays();
    greenRegularRenderUrlCache.clear();
  }

  function cancelDetection() {
    const prevTemplate = detectionReviewState.prevTemplate;
    exitAllDetectionModes();
    detectionReviewState.prevTemplate = null;
    detectionReviewState.pendingPayload = null;
    detectionReviewState.params = null;

    if (prevTemplate) {
      state.selected = prevTemplate;
      updateTemplateInQueue(state.selected);
      renderEditor();
    }

    $("proposalState").textContent = "Detection cancelled.";
    toast("Detection cancelled");
  }

  function editColorPickMode() {
    const prevTemplate = detectionReviewState.prevTemplate;
    exitAllDetectionModes();
    if (prevTemplate) {
      state.selected = JSON.parse(JSON.stringify(prevTemplate));
      updateTemplateInQueue(state.selected);
    }
    runColorPickMode();
  }

  function editFramePointsMode() {
    const savedPoints = (detectionReviewState.params && Array.isArray(detectionReviewState.params.points))
      ? detectionReviewState.params.points.map(p => ({ ...p }))
      : [];
    const prevTemplate = detectionReviewState.prevTemplate;
    exitAllDetectionModes();
    if (prevTemplate) {
      state.selected = JSON.parse(JSON.stringify(prevTemplate));
      updateTemplateInQueue(state.selected);
    }
    if (savedPoints.length > 0) {
      maskDetectState.points = savedPoints;
      runFramePointsMode(true);
    } else {
      runFramePointsMode(false);
    }
  }

  // --- MASK DETECTION (COLOR PICK / FRAME POINTS) ---

  const maskDetectState = {
    active: false,
    mode: null,
    sampledColor: null,
    points: [],
    clickListener: null,
  };

  function showDetectionMethodPicker() {
    exitAllDetectionModes();
    maskDetectState.active = true;
    maskDetectState.mode = null;

    $("proposalState").classList.add("hidden");
    $("maskDetectionHud").classList.remove("hidden");
    $("maskDetectColorSwatch").classList.add("hidden");
    $("maskDetectToleranceRow").classList.add("hidden");

    updateMaskDetectUI("DETECT", "Detection Method", "Choose how to find frame areas in this mockup:", [
      { text: "Auto Detect", class: "secondary", onclick: () => { exitAllDetectionModes(); startDetectionWizard(); } },
      { text: "Color Pick", class: "secondary", onclick: () => runColorPickMode() },
      { text: "Frame Points", class: "secondary", onclick: () => runFramePointsMode() },
      { text: "Cancel", class: "danger", onclick: () => cancelDetection() },
    ]);
  }

  function runColorPickMode() {
    exitAllDetectionModes();
    maskDetectState.active = true;
    maskDetectState.mode = "color_pick";

    $("proposalState").classList.add("hidden");
    $("maskDetectionHud").classList.remove("hidden");
    $("maskDetectColorSwatch").classList.add("hidden");
    $("maskDetectToleranceRow").classList.remove("hidden");

    updateMaskDetectUI("COLOR PICK", "Sample Frame Color", "Click anywhere in the frame area to pick its color.", [
      { text: "Run Detection", class: "primary", disabled: true, id: "maskColorPickRunBtn", onclick: () => submitMaskDetection("color_pick") },
      { text: "Cancel", class: "danger", onclick: () => cancelDetection() },
    ]);

    $("stage").classList.add("stage-cursor-crosshair");
    disableMaskDetectClickListener();

    maskDetectState.clickListener = (e) => {
      const image = $("canvasImage");
      const rect = image.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;
      if (clickX < 0 || clickY < 0 || clickX > rect.width || clickY > rect.height) return;

      const naturalX = Math.round((clickX / rect.width) * image.naturalWidth);
      const naturalY = Math.round((clickY / rect.height) * image.naturalHeight);

      const tmpCanvas = document.createElement("canvas");
      tmpCanvas.width = image.naturalWidth;
      tmpCanvas.height = image.naturalHeight;
      const ctx = tmpCanvas.getContext("2d");
      try {
        ctx.drawImage(image, 0, 0);
        const px = ctx.getImageData(naturalX, naturalY, 1, 1).data;
        maskDetectState.sampledColor = [px[0], px[1], px[2]];
      } catch (_err) {
        toast("Could not sample color from this image.");
        return;
      }

      const [r, g, b] = maskDetectState.sampledColor;
      const hex = `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
      $("maskDetectColorBox").style.background = hex;
      $("maskDetectColorLabel").textContent = `rgb(${r}, ${g}, ${b})`;
      $("maskDetectColorSwatch").classList.remove("hidden");

      updateMaskDetectUI("COLOR PICK", "Sample Frame Color", `Color sampled: rgb(${r}, ${g}, ${b}). Adjust tolerance or click to re-pick.`, [
        { text: "Run Detection", class: "primary", id: "maskColorPickRunBtn", onclick: () => submitMaskDetection("color_pick") },
        { text: "Re-pick", class: "secondary", onclick: () => { maskDetectState.sampledColor = null; $("maskDetectColorSwatch").classList.add("hidden"); runColorPickMode(); } },
        { text: "Cancel", class: "danger", onclick: () => cancelDetection() },
      ]);
    };
    $("canvasImage").addEventListener("click", maskDetectState.clickListener);
  }

  function runFramePointsMode(preservePoints = false) {
    if (!preservePoints) {
      exitAllDetectionModes();
    } else {
      disableCanvasClickListener();
      disableMaskDetectClickListener();
      if ($("detectionWizardHud")) $("detectionWizardHud").classList.add("hidden");
      wizardState.active = false;
      clearDetectionOverlays();
      redrawMaskDetectDots();
    }

    maskDetectState.active = true;
    maskDetectState.mode = "frame_points";

    $("proposalState").classList.add("hidden");
    $("maskDetectionHud").classList.remove("hidden");
    $("maskDetectColorSwatch").classList.add("hidden");
    $("maskDetectToleranceRow").classList.remove("hidden");

    const count = maskDetectState.points.length;
    updateMaskDetectUI("FRAME POINTS", "Mark Frame Centers",
      count
        ? `${count} point(s) placed. Add more or click Done to detect.`
        : "Click inside each frame you want to detect. Each click places a numbered marker.",
      [
        { text: "Done", class: "primary", disabled: !count, id: "maskFramePointsDoneBtn", onclick: () => submitMaskDetection("frame_points") },
        { text: "Undo Last", class: "secondary", id: "maskFramePointsUndoBtn", onclick: () => undoLastFramePoint() },
        { text: "Cancel", class: "danger", onclick: () => cancelDetection() },
      ]
    );

    $("stage").classList.add("stage-cursor-crosshair");
    disableMaskDetectClickListener();

    maskDetectState.clickListener = (e) => {
      const image = $("canvasImage");
      const rect = image.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;
      if (clickX < 0 || clickY < 0 || clickX > rect.width || clickY > rect.height) return;

      const naturalX = Math.round((clickX / rect.width) * image.naturalWidth);
      const naturalY = Math.round((clickY / rect.height) * image.naturalHeight);

      maskDetectState.points.push({ x: naturalX, y: naturalY });
      addMaskDetectDot(maskDetectState.points.length, clickX, clickY);

      const count = maskDetectState.points.length;
      updateMaskDetectUI("FRAME POINTS", "Mark Frame Centers", `${count} point(s) placed. Add more or click Done to detect.`, [
        { text: "Done", class: "primary", id: "maskFramePointsDoneBtn", onclick: () => submitMaskDetection("frame_points") },
        { text: "Undo Last", class: "secondary", id: "maskFramePointsUndoBtn", onclick: () => undoLastFramePoint() },
        { text: "Cancel", class: "danger", onclick: () => cancelDetection() },
      ]);
    };
    $("canvasImage").addEventListener("click", maskDetectState.clickListener);
  }

  function undoLastFramePoint() {
    if (!maskDetectState.points.length) return;
    maskDetectState.points.pop();
    redrawMaskDetectDots();
    const count = maskDetectState.points.length;
    updateMaskDetectUI("FRAME POINTS", "Mark Frame Centers",
      count ? `${count} point(s) placed. Add more or click Done to detect.` : "Click inside each frame you want to detect.",
      [
        { text: "Done", class: "primary", disabled: !count, id: "maskFramePointsDoneBtn", onclick: () => submitMaskDetection("frame_points") },
        { text: "Undo Last", class: "secondary", id: "maskFramePointsUndoBtn", onclick: () => undoLastFramePoint() },
        { text: "Cancel", class: "danger", onclick: () => cancelDetection() },
      ]
    );
  }

  function addMaskDetectDot(number, displayX, displayY) {
    const layer = $("maskDetectionDotsLayer");
    if (!layer) return;
    layer.classList.remove("hidden");

    const stage = $("stage");
    const stageRect = stage.getBoundingClientRect();
    const imageRect = $("canvasImage").getBoundingClientRect();
    const left = imageRect.left - stageRect.left + displayX;
    const top = imageRect.top - stageRect.top + displayY;

    const dot = document.createElement("div");
    dot.className = "mask-detect-dot";
    dot.dataset.index = number - 1;
    dot.textContent = String(number);
    dot.style.left = `${left}px`;
    dot.style.top = `${top}px`;
    layer.appendChild(dot);
  }

  function clearMaskDetectDots() {
    const layer = $("maskDetectionDotsLayer");
    if (!layer) return;
    layer.innerHTML = "";
    layer.classList.add("hidden");
  }

  function redrawMaskDetectDots() {
    clearMaskDetectDots();
    if (!maskDetectState.points.length) return;
    const image = $("canvasImage");
    const imageRect = image.getBoundingClientRect();
    maskDetectState.points.forEach((pt, idx) => {
      const displayX = (pt.x / image.naturalWidth) * imageRect.width;
      const displayY = (pt.y / image.naturalHeight) * imageRect.height;
      addMaskDetectDot(idx + 1, displayX, displayY);
    });
  }

  async function submitMaskDetection(mode, overrideParams) {
    disableMaskDetectClickListener();
    $("stage").classList.remove("stage-cursor-crosshair");
    clearMaskDetectDots();

    // Show "detecting" state inside the existing HUD while waiting
    $("maskDetectionHud").classList.remove("hidden");
    $("proposalState").classList.add("hidden");
    $("maskDetectColorSwatch").classList.add("hidden");
    $("maskDetectToleranceRow").classList.add("hidden");
    updateMaskDetectUI("DETECTING", "Detecting…", "Please wait.", []);

    setBusy(true);
    const tolerance = parseInt(($("maskDetectTolerance") || {}).value || String(DEFAULT_MASK_TOLERANCE), 10);

    const params = overrideParams || {
      mode,
      tolerance,
      color: maskDetectState.sampledColor,
      points: (maskDetectState.points || []).map(p => ({ x: p.x, y: p.y })),
    };

    const body = { mode: params.mode, tolerance: params.tolerance };
    if (params.mode === "color_pick") {
      body.color = params.color;
    } else {
      body.points = params.points || [];
    }

    $("analysisLabel").textContent = params.mode === "color_pick"
      ? "Detecting regions by sampled color…"
      : "Detecting regions from seed points…";

    try {
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, {
        method: "POST",
        body: JSON.stringify(body),
      });
      setBusy(false);
      showDetectionReview(payload, params);
    } catch (error) {
      setBusy(false);
      $("detectionResult").className = "rule result-rule error";
      $("detectionResult").textContent = error.message;
      toast(error.message);
      setStatus("Detection failed: " + error.message, true);

      // Keep the HUD active so user can adjust tolerance or re-sample without losing context
      $("maskDetectionHud").classList.remove("hidden");
      $("proposalState").classList.add("hidden");
      $("maskDetectToleranceRow").classList.remove("hidden");
      const tolInput = $("maskDetectTolerance");
      if (tolInput) tolInput.value = params.tolerance || DEFAULT_MASK_TOLERANCE;
      const tolVal = $("maskDetectToleranceVal");
      if (tolVal) tolVal.textContent = String(params.tolerance || DEFAULT_MASK_TOLERANCE);

      if (params.mode === "color_pick" && params.color) {
        maskDetectState.active = true;
        maskDetectState.mode = "color_pick";
        maskDetectState.sampledColor = params.color;
        const [r, g, b] = params.color;
        const hex = `#${r.toString(16).padStart(2, "0")}${g.toString(16).padStart(2, "0")}${b.toString(16).padStart(2, "0")}`;
        $("maskDetectColorBox").style.background = hex;
        $("maskDetectColorLabel").textContent = `rgb(${r}, ${g}, ${b})`;
        $("maskDetectColorSwatch").classList.remove("hidden");

        updateMaskDetectUI("COLOR PICK", "No Regions Found", `No frames found for rgb(${r}, ${g}, ${b}) with tolerance ${params.tolerance}. Increase tolerance slider and click Run Detection, or re-pick.`, [
          { text: "Run Detection", class: "primary", id: "maskColorPickRunBtn", onclick: () => submitMaskDetection("color_pick") },
          { text: "Re-pick", class: "secondary", onclick: () => { maskDetectState.sampledColor = null; $("maskDetectColorSwatch").classList.add("hidden"); runColorPickMode(); } },
          { text: "Cancel", class: "danger", onclick: () => cancelDetection() },
        ]);
        runColorPickMode();
      } else if (params.mode === "frame_points") {
        maskDetectState.active = true;
        maskDetectState.mode = "frame_points";
        if (Array.isArray(params.points)) {
          maskDetectState.points = params.points.map(p => ({ ...p }));
        }
        redrawMaskDetectDots();
        const count = maskDetectState.points.length;
        updateMaskDetectUI("FRAME POINTS", "No Regions Found", `No frames detected at marker(s) with tolerance ${params.tolerance}. Adjust tolerance slider and click Done, or mark different spots.`, [
          { text: "Done", class: "primary", disabled: !count, id: "maskFramePointsDoneBtn", onclick: () => submitMaskDetection("frame_points") },
          { text: "Undo Last", class: "secondary", id: "maskFramePointsUndoBtn", onclick: () => undoLastFramePoint() },
          { text: "Cancel", class: "danger", onclick: () => cancelDetection() },
        ]);
        runFramePointsMode(true);
      } else {
        closeMaskDetectionHud();
      }
    }
  }

  function disableMaskDetectClickListener() {
    if (maskDetectState.clickListener) {
      const img = $("canvasImage");
      if (img) img.removeEventListener("click", maskDetectState.clickListener);
      maskDetectState.clickListener = null;
    }
  }

  function closeMaskDetectionHud() {
    disableMaskDetectClickListener();
    clearMaskDetectDots();
    $("stage").classList.remove("stage-cursor-crosshair");
    maskDetectState.active = false;
    maskDetectState.mode = null;
    const hud = $("maskDetectionHud");
    if (hud) hud.classList.add("hidden");
    const ps = $("proposalState");
    if (ps) ps.classList.remove("hidden");
  }

  function updateMaskDetectUI(badge, title, instruction, actions) {
    $("maskDetectBadge").textContent = badge;
    $("maskDetectTitle").textContent = title;
    $("maskDetectInstruction").textContent = instruction;
    const container = $("maskDetectActions");
    container.innerHTML = "";
    actions.forEach(act => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `wizard-btn ${act.class || ""}`;
      btn.textContent = act.text;
      if (act.disabled) btn.disabled = true;
      if (act.id) btn.id = act.id;
      btn.onclick = act.onclick;
      container.appendChild(btn);
    });
  }

  // --- PREMIUM 4-STAGE GUIDED DETECTION WIZARD ---
  async function startDetectionWizard() {
    if (!state.selected) {
      toast("Select a mockup before running detection.");
      return;
    }
    exitAllDetectionModes();
    if (!detectionReviewState.prevTemplate) saveDetectionPreState();

    // Immediately isolate from any previous mask or multi-region points
    state.selected.mask_name = null;
    state.selected.mask = null;
    state.selected.raw_artwork_area = { mode: "geometry" };
    if (state.selected.artwork_area) {
      delete state.selected.artwork_area.regions;
    }

    wizardState.active = true;
    wizardState.step = 1;
    wizardState.layers = [];
    wizardState.layerIndex = 0;
    wizardState.proposedCorners = null;

    // Hide default state and display the integrated footer wizard HUD
    $("proposalState").classList.add("hidden");
    $("detectionWizardHud").classList.remove("hidden");

    await runStage1Geometry();
  }

  async function runStage1Geometry() {
    wizardState.step = 1;
    wizardState.isMultiFrame = false;
    updateWizardUI("STAGE 1", "Automatic Geometry", "Searching for nesting frames in the mockup image...", []);

    try {
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, {
        method: "POST",
        body: JSON.stringify({ mode: "geometry" })
      });

      const detectedRegions = (payload.proposal?.raw_artwork_area?.regions) || (payload.proposal?.artwork_area?.regions) || [];
      if (detectedRegions.length > 1) {
        state.selected = {
          // Geometric frames render straight onto their corners, with no mask
          // in play. Naming one the template does not have pointed the canvas
          // mask at a 404, and a mask that cannot load hides everything it is
          // applied to -- the artwork vanished from every frame until the
          // detection was approved.
          ...payload.template,
          mask_name: payload.template?.mask_name || null,
          raw_artwork_area: payload.proposal.raw_artwork_area,
          artwork_area: payload.proposal.artwork_area
        };
        wizardState.isMultiFrame = true;
        updateTemplateInQueue(state.selected);
        drawSelection();

        updateWizardUI(
          "STAGE 1",
          "Automatic Multi-Frame Detection",
          `Found ${detectedRegions.length} frames across this mockup. Click '✕' on any frame to remove it:`,
          [
            { text: `Approve (${detectedRegions.length})`, class: "primary", onclick: () => approveWizardSelection() },
            { text: "Cancel", class: "danger", onclick: () => cancelDetection() }
          ]
        );
        return;
      }

      if (payload.template) {
        state.selected = {
          ...payload.template,
          mask_name: null,
          mask: null,
          raw_artwork_area: payload.proposal?.raw_artwork_area || { mode: "geometry" },
          artwork_area: payload.proposal?.artwork_area || state.selected.artwork_area
        };
        if (state.selected.artwork_area) {
          delete state.selected.artwork_area.regions;
        }
        updateTemplateInQueue(state.selected);
      }

      const layers = (payload.proposal && payload.proposal.raw_artwork_area && payload.proposal.raw_artwork_area.layers) || [];
      if (layers.length > 0) {
        wizardState.layers = layers;
        wizardState.layerIndex = layers.length - 1; // Default to the innermost (smallest) layer
        showWizardLayer();
      } else if (payload.proposal && payload.proposal.artwork_area && payload.proposal.artwork_area.corners) {
        wizardState.layers = [payload.proposal.artwork_area.corners];
        wizardState.layerIndex = 0;
        showWizardLayer();
      } else {
        updateWizardUI(
          "STAGE 1",
          "Automatic Geometry",
          "No nesting border layers detected. Choose next step:",
          [
            { text: "Try SAM 2.1", class: "primary", onclick: () => runStage2SamCenter() },
            { text: "Manual Click", class: "secondary", onclick: () => runStage3UserClick() },
            { text: "Cancel", class: "danger", onclick: () => cancelDetection() }
          ]
        );
      }
    } catch (error) {
      console.warn("Stage 1 Geometry failed:", error);
      toast("Stage 1 geometry detection encountered an issue: " + error.message);
      updateWizardUI(
        "STAGE 1",
        "Automatic Geometry",
        "Could not detect sharp frame contours. Choose next step:",
        [
          { text: "Try SAM 2.1", class: "primary", onclick: () => runStage2SamCenter() },
          { text: "Manual Click", class: "secondary", onclick: () => runStage3UserClick() },
          { text: "Cancel", class: "danger", onclick: () => cancelDetection() }
        ]
      );
    }
  }

  function showWizardLayer() {
    const currentLayer = wizardState.layers[wizardState.layerIndex];
    if (!currentLayer) return;

    state.selected.mask_name = null;
    state.selected.mask = null;
    if (!state.selected.artwork_area) {
      state.selected.artwork_area = {};
    }
    delete state.selected.artwork_area.regions;
    state.selected.artwork_area.corners = JSON.parse(JSON.stringify(currentLayer));

    const xs = currentLayer.map(c => c.x);
    const ys = currentLayer.map(c => c.y);
    state.selected.artwork_area.x = Math.min(...xs);
    state.selected.artwork_area.y = Math.min(...ys);
    state.selected.artwork_area.width = Math.max(...xs) - Math.min(...xs);
    state.selected.artwork_area.height = Math.max(...ys) - Math.min(...ys);

    updateCoordinateLabels();
    drawSelection();

    // Enable SVG polygon visibility
    $("selectionSvg").classList.remove("hidden");

    const actions = [
      { text: "Approve", class: "primary", onclick: () => approveWizardSelection() }
    ];

    if (wizardState.layers.length > 1) {
      actions.push({
        text: "Next Layer",
        class: "secondary",
        onclick: () => {
          wizardState.layerIndex = (wizardState.layerIndex - 1 + wizardState.layers.length) % wizardState.layers.length;
          showWizardLayer();
        }
      });
    }

    actions.push({ text: "Skip", class: "danger", onclick: () => runStage2SamCenter() });
    actions.push({ text: "Cancel", class: "danger", onclick: () => cancelDetection() });

    updateWizardUI(
      "STAGE 1",
      "Automatic Geometry",
      wizardState.layers.length > 1
        ? `Found ${wizardState.layers.length} nesting border layers. Currently showing Layer ${wizardState.layerIndex + 1}/${wizardState.layers.length}.`
        : "Found frame border. Click Approve to confirm or Skip for SAM 2.1.",
      actions
    );
  }

  async function runStage2SamCenter() {
    wizardState.step = 2;
    updateWizardUI("STAGE 2", "Automatic SAM 2.1 Center Guess", "Running local SAM 2.1 model centered around the image middle... Please wait...", []);

    try {
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, {
        method: "POST",
        body: JSON.stringify({ mode: "sam_center" })
      });

      const proposal = payload.proposal;
      if (proposal && proposal.artwork_area && proposal.artwork_area.corners) {
        state.selected.mask_name = null;
        state.selected.mask = null;
        state.selected.artwork_area = { ...proposal.artwork_area };
        delete state.selected.artwork_area.regions;
        state.selected.raw_artwork_area = proposal.raw_artwork_area || null;

        updateCoordinateLabels();
        drawSelection();

        $("selectionSvg").classList.remove("hidden");

        const actions = [
          { text: "Approve", class: "primary", onclick: () => approveWizardSelection() },
          { text: "Retry", class: "secondary", onclick: () => runStage2SamCenter() },
          { text: "Manual", class: "danger", onclick: () => runStage3UserClick() }
        ];

        updateWizardUI(
          "STAGE 2",
          "Automatic SAM 2.1 Center Guess",
          "SAM 2.1 found a frame centered around the middle of the mockup. Is this correct?",
          actions
        );
      } else {
        runStage3UserClick();
      }
    } catch (error) {
      console.warn("Stage 2 SAM Center failed:", error);
      runStage3UserClick();
    }
  }

  function runStage3UserClick() {
    wizardState.step = 3;
    wizardState.proposedCorners = null;

    // Clear all detection overlays and hide previous artwork so the background is 100% visible
    clearDetectionOverlays();

    // Hide current polygon handles so user knows we are waiting for a click
    $("selectionSvg").classList.add("hidden");
    $("selectionImageOverlay").classList.add("hidden");
    if ($("selectionRenderedMockup")) {
      $("selectionRenderedMockup").classList.add("hidden");
      $("selectionRenderedMockup").src = "";
    }

    const actions = [
      { text: "Lock", class: "primary", disabled: true, id: "btnLockContinue", onclick: () => runStage4FineTune() },
      { text: "Cancel", class: "danger", onclick: () => closeWizard() }
    ];

    updateWizardUI(
      "STAGE 3",
      "Semi-Automatic Click",
      "Click inside the frame to detect.",
      actions
    );

    enableCanvasClickListener();
  }

  function enableCanvasClickListener() {
    disableCanvasClickListener();

    const image = $("canvasImage");
    const selectionSvg = $("selectionSvg");

    // Hide the SVG overlay completely during Stage 3 so there are absolutely no polygons/handles blocking click events
    selectionSvg.classList.add("hidden");
    selectionSvg.classList.add("wizard-clicking");

    wizardState.clickListener = async (e) => {
      const rect = image.getBoundingClientRect();
      const clickX = e.clientX - rect.left;
      const clickY = e.clientY - rect.top;

      const naturalWidth = image.naturalWidth;
      const naturalHeight = image.naturalHeight;

      const naturalX = Math.round((clickX / rect.width) * naturalWidth);
      const naturalY = Math.round((clickY / rect.height) * naturalHeight);

      updateWizardUI(
        "STAGE 3",
        "Semi-Automatic Click",
        "Analyzing clicked point...",
        []
      );

      try {
        const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, {
          method: "POST",
          body: JSON.stringify({
            mode: "sam_point",
            point: { x: naturalX, y: naturalY }
          })
        });

        const proposal = payload.proposal;
        if (proposal && proposal.artwork_area && proposal.artwork_area.corners) {
          state.selected.mask_name = null;
          state.selected.mask = null;
          state.selected.artwork_area = { ...proposal.artwork_area };
          delete state.selected.artwork_area.regions;
          state.selected.raw_artwork_area = proposal.raw_artwork_area || null;
          wizardState.proposedCorners = proposal.artwork_area.corners;

          updateCoordinateLabels();

          // Re-enable and draw the new polygon overlay
          selectionSvg.classList.remove("hidden");
          drawSelection();

          const actions = [
            { text: "Lock", class: "primary", onclick: () => runStage4FineTune() },
            { text: "Retry", class: "secondary", onclick: () => runStage3UserClick() },
            { text: "Cancel", class: "danger", onclick: () => closeWizard() }
          ];

          updateWizardUI(
            "STAGE 3",
            "Semi-Automatic Click",
            "Frame generated. Lock or click elsewhere to retry.",
            actions
          );
        } else {
          toast("Could not resolve frame from this point. Please click elsewhere.");
          runStage3UserClick();
        }
      } catch (error) {
        toast(`SAM 2.1 failed: ${error.message}. Click elsewhere.`);
        runStage3UserClick();
      }
    };

    image.addEventListener("click", wizardState.clickListener);
  }

  function disableCanvasClickListener() {
    if (wizardState.clickListener) {
      $("canvasImage").removeEventListener("click", wizardState.clickListener);
      wizardState.clickListener = null;
    }
    $("selectionSvg").classList.remove("wizard-clicking");
    $("selectionSvg").classList.remove("hidden");
  }

  function runStage4FineTune() {
    disableCanvasClickListener();
    wizardState.step = 4;

    $("selectionSvg").classList.remove("hidden");
    drawSelection();

    const actions = [
      { text: "Confirm", class: "primary", onclick: () => approveWizardSelection() },
      { text: "Restart", class: "secondary", onclick: () => startDetectionWizard() }
    ];

    updateWizardUI(
      "STAGE 4",
      "Fine-Tuning",
      "Drag handles to fine-tune.",
      actions
    );
  }

  async function approveWizardSelection() {
    setBusy(true);
    detectionReviewState.wizardApproved = true;
    if (!wizardState.isMultiFrame) {
      state.selected.mask_name = null;
      state.selected.mask = null;
      if (state.selected.artwork_area) {
        delete state.selected.artwork_area.regions;
      }
    }
    closeWizard();

    try {
      await saveTemplate(false, true);

      // Call activate/approve endpoint to publish template
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/activate`, { method: "POST" });
      state.selected = payload.template;
      updateTemplateInQueue(state.selected);
      renderEditor();

      toast("Template successfully approved and active!");
      setStatus("Template approved and active!");
    } catch (error) {
      toast("Failed to save approved boundary: " + error.message);
      setStatus("Approval failed", true);
    } finally {
      setBusy(false);
    }
  }

  function removeDetectedRegion(rIdx) {
    if (!state.selected?.raw_artwork_area?.regions) return;
    state.selected.raw_artwork_area.regions.splice(rIdx, 1);
    const regions = state.selected.raw_artwork_area.regions;
    if (regions.length === 0) {
      // Removing the last frame leaves nothing to approve; drop back to the
      // pre-detection state rather than pulling the user into another mode.
      toast("All frames removed. Detection cancelled.");
      cancelDetection();
      return;
    }
    // Re-index remaining frames
    regions.forEach((r, i) => { r.index = i + 1; });
    const allCorners = regions.flatMap(r => r.corners || []);
    const xs = allCorners.map(c => c.x);
    const ys = allCorners.map(c => c.y);
    state.selected.artwork_area = {
      x: Math.min(...xs),
      y: Math.min(...ys),
      width: Math.max(...xs) - Math.min(...xs),
      height: Math.max(...ys) - Math.min(...ys),
      corners: regions[0].corners,
      regions: regions
    };
    updateTemplateInQueue(state.selected);
    drawSelection();
    toast(`Frame removed. ${regions.length} frame(s) remaining.`);
    if (wizardState.active && wizardState.isMultiFrame) {
      updateWizardUI(
        "STAGE 1",
        "Automatic Multi-Frame Detection",
        `Showing ${regions.length} frame(s). Click '✕' to remove any frame:`,
        [
          { text: `Approve (${regions.length})`, class: "primary", onclick: () => approveWizardSelection() },
          { text: "Cancel", class: "danger", onclick: () => cancelDetection() }
        ]
      );
    }
  }

  function closeWizard() {
    disableCanvasClickListener();
    wizardState.active = false;
    $("detectionWizardHud").classList.add("hidden");

    // If user cancelled without approving, restore the pre-detection state.
    if (!detectionReviewState.wizardApproved && detectionReviewState.prevTemplate) {
      state.selected = JSON.parse(JSON.stringify(detectionReviewState.prevTemplate));
      detectionReviewState.prevTemplate = null;
      renderEditor();
    }

    $("proposalState").classList.remove("hidden");
  }

  function updateWizardUI(badge, title, instruction, actions) {
    $("wizardStepIndicator").textContent = badge;
    $("wizardTitle").textContent = title;
    $("wizardInstruction").textContent = instruction;

    const actionsContainer = $("wizardActions");
    actionsContainer.innerHTML = "";

    actions.forEach(act => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = `wizard-btn ${act.class || ""}`;
      btn.textContent = act.text;
      if (act.disabled) btn.disabled = true;
      if (act.id) btn.id = act.id;
      btn.onclick = act.onclick;
      actionsContainer.appendChild(btn);
    });
  }

  function handleWizardEscapeKey() {
    if (!wizardState.active) return;
    if (wizardState.step === 1) {
      runStage2SamCenter();
    } else if (wizardState.step === 2) {
      runStage3UserClick();
    } else if (wizardState.step === 3) {
      closeWizard();
    } else if (wizardState.step === 4) {
      approveWizardSelection();
    }
  }

  function updateTemplateInQueue(updated) {
    state.templates = state.templates.map((template) =>
      template.template_id === updated.template_id ? updated : template
    );
    renderQueue();
  }

  function initSidebarResize() {
    const handle = $("sidebarResizeHandle");
    const sidebar = document.querySelector(".sidebar");
    if (!handle || !sidebar) return;
    handle.tabIndex = 0;

    const redrawAfterResize = () => {
      if (state.selected) {
        drawSelection();
      }
    };

    handle.addEventListener("pointerdown", (event) => {
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = sidebar.getBoundingClientRect().width;
      document.body.classList.add("sidebar-resizing");
      handle.setPointerCapture(event.pointerId);

      const resize = (moveEvent) => {
        const nextWidth = setSidebarWidth(startWidth + moveEvent.clientX - startX);
        document.documentElement.style.setProperty("--sidebar-width", `${nextWidth}px`);
        redrawAfterResize();
      };

      const stop = () => {
        document.body.classList.remove("sidebar-resizing");
        handle.removeEventListener("pointermove", resize);
        handle.removeEventListener("pointerup", stop);
        handle.removeEventListener("pointercancel", stop);
        redrawAfterResize();
      };

      handle.addEventListener("pointermove", resize);
      handle.addEventListener("pointerup", stop);
      handle.addEventListener("pointercancel", stop);
    });

    handle.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const currentWidth = sidebar.getBoundingClientRect().width;
      if (event.key === "Home") {
        setSidebarWidth(MIN_SIDEBAR_WIDTH);
      } else if (event.key === "End") {
        setSidebarWidth(MAX_SIDEBAR_WIDTH);
      } else {
        const delta = event.shiftKey ? 40 : 16;
        setSidebarWidth(currentWidth + (event.key === "ArrowRight" ? delta : -delta));
      }
      redrawAfterResize();
    });
  }

  initSidebarResize();

  document.querySelectorAll(".filter-pill").forEach((pill) => {
    pill.onclick = () => {
      if (state.busy) return;
      state.queueFilter = pill.dataset.filter;
      renderQueue();
    };
  });
  $("openCategory").onclick = () => {
    if (state.busy) return;
    $("categoryModal").classList.add("open");
  };
  $("cancelCategory").onclick = () => $("categoryModal").classList.remove("open");
  $("cancelDelete").onclick = closeDeleteModal;
  $("confirmDelete").onclick = deleteTemplate;
  $("deleteModal").onclick = (event) => {
    if (event.target === $("deleteModal")) closeDeleteModal();
  };
  wireSystemDialog();
  $("createCategory").onclick = async () => {
    try {
      const payload = await api("/api/admin/categories", {
        method: "POST",
        body: JSON.stringify({ name: $("newCategory").value })
      });
      $("newCategory").value = "";
      $("categoryModal").classList.remove("open");
      await loadCategories(payload.category.id);
      await loadTemplates();
      toast("Category created");
    } catch (error) {
      toast(error.message);
    }
  };
  $("chooseFiles").onclick = () => $("fileInput").click();
  $("fileInput").onchange = async (event) => {
    await importFiles(event.target.files);
    event.target.value = "";
  };

  window.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && dismissSystemDialog()) {
      event.preventDefault();
      return;
    }
    if (event.key === "Escape" && wizardState.active) {
      event.preventDefault();
      handleWizardEscapeKey();
      return;
    }
    if (event.code === "Space" && document.activeElement.tagName !== "INPUT" && document.activeElement.tagName !== "SELECT") {
      event.preventDefault();
      if (!state.spacePressed) {
        state.spacePressed = true;
        const ws = document.querySelector(".canvas-workspace");
        if (ws && !state.canvasLocked) ws.classList.add("panning-mode");
      }
    }
  });

  window.addEventListener("keyup", (event) => {
    if (event.code === "Space") {
      state.spacePressed = false;
      const ws = document.querySelector(".canvas-workspace");
      if (ws && !state.isPanning) {
        ws.classList.remove("panning-mode");
      }
    }
  });

  // Mouse scroll wheel zooming anchored to cursor
  const workspace = document.querySelector(".canvas-workspace");
  if (workspace) {
    workspace.addEventListener("wheel", (event) => {
      if (!state.selected) return;
      event.preventDefault();
      if (state.canvasLocked) return;

      const rect = workspace.getBoundingClientRect();
      const mouseX = event.clientX - rect.left;
      const mouseY = event.clientY - rect.top;

      const zoomFactor = 1.15;
      const oldZoom = state.zoom;
      let newZoom = oldZoom;

      if (event.deltaY < 0) {
        newZoom = Math.min(10, oldZoom * zoomFactor);
      } else {
        newZoom = Math.max(1, oldZoom / zoomFactor);
      }

      if (newZoom === oldZoom) return;

      const newPanX = mouseX - ((mouseX - state.pan.x) / oldZoom) * newZoom;
      const newPanY = mouseY - ((mouseY - state.pan.y) / oldZoom) * newZoom;

      state.zoom = newZoom;
      state.pan = { x: newPanX, y: newPanY };

      if (newZoom <= 1.02) {
        state.zoom = 1;
        state.pan = { x: 0, y: 0 };
      }

      applyZoomPan();
      drawSelection();
    }, { passive: false });

    // Pointer down event to begin panning
    workspace.addEventListener("pointerdown", (event) => {
      if (!state.selected || state.canvasLocked) return;

      const isMiddleClick = event.button === 1;
      const isSpacePan = event.button === 0 && state.spacePressed;

      if (isMiddleClick || isSpacePan) {
        event.preventDefault();
        state.isPanning = true;
        state.panStart = {
          x: event.clientX - state.pan.x,
          y: event.clientY - state.pan.y
        };
        workspace.classList.add("panning-mode");
        workspace.setPointerCapture(event.pointerId);
      }
    });

    // Pointer move event to pan
    workspace.addEventListener("pointermove", (event) => {
      if (!state.isPanning) return;
      event.preventDefault();

      state.pan = {
        x: event.clientX - state.panStart.x,
        y: event.clientY - state.panStart.y
      };
      applyZoomPan();
    });

    // Pointer up event to stop panning
    const endPanning = (event) => {
      if (state.isPanning) {
        state.isPanning = false;
        try {
          workspace.releasePointerCapture(event.pointerId);
        } catch (_err) { }
        if (!state.spacePressed) {
          workspace.classList.remove("panning-mode");
        }
      }
    };

    workspace.addEventListener("pointerup", endPanning);
    workspace.addEventListener("pointercancel", endPanning);

    // Double click empty workspace area to reset zoom & pan
    workspace.addEventListener("dblclick", (event) => {
      if (state.canvasLocked) return;
      if (event.target === workspace || event.target === $("stage") || event.target === $("canvasImage")) {
        resetZoomPan();
      }
    });
  }

  // Zoom HUD button controls
  $("zoomOutBtn").onclick = (e) => {
    e.stopPropagation();
    zoomIncrementally(-1);
  };

  $("zoomInBtn").onclick = (e) => {
    e.stopPropagation();
    zoomIncrementally(1);
  };

  $("zoomResetBtn").onclick = (e) => {
    e.stopPropagation();
    if (state.canvasLocked) return;
    resetZoomPan();
  };

  if ($("canvasLockBtn")) {
    $("canvasLockBtn").onclick = (e) => {
      e.stopPropagation();
      setCanvasLock(!state.canvasLocked);
      // A manual toggle overrides the placement auto-lock restore state.
      state.lockBeforePlacement = state.canvasLocked;
    };
  }

  if ($("polygonLockBtn")) {
    $("polygonLockBtn").onclick = (e) => {
      e.stopPropagation();
      setPolygonLock(!state.polygonLocked);
      // A manual toggle overrides the placement auto-lock restore state.
      state.polygonLockBeforePlacement = state.polygonLocked;
    };
  }

  function zoomIncrementally(direction) {
    if (state.canvasLocked) return;
    const oldZoom = state.zoom;
    const zoomFactor = 1.3;
    let newZoom = oldZoom;

    if (direction > 0) {
      newZoom = Math.min(10, oldZoom * zoomFactor);
    } else {
      newZoom = Math.max(1, oldZoom / zoomFactor);
    }

    if (newZoom === oldZoom) return;

    const ws = document.querySelector(".canvas-workspace");
    const wWidth = ws ? ws.clientWidth : 800;
    const wHeight = ws ? ws.clientHeight : 600;
    const centerX = wWidth / 2;
    const centerY = wHeight / 2;

    const newPanX = centerX - ((centerX - state.pan.x) / oldZoom) * newZoom;
    const newPanY = centerY - ((centerY - state.pan.y) / oldZoom) * newZoom;

    state.zoom = newZoom;
    state.pan = { x: newPanX, y: newPanY };

    if (newZoom <= 1.02) {
      state.zoom = 1;
      state.pan = { x: 0, y: 0 };
    }

    applyZoomPan();
    drawSelection();
  }

  function resetZoomPan() {
    state.zoom = 1;
    state.pan = { x: 0, y: 0 };
    applyZoomPan();
    drawSelection();
  }

  $("selectionSvg").addEventListener("pointerdown", beginDrag);
  $("selectionSvg").addEventListener("pointermove", continueDrag);
  $("selectionSvg").addEventListener("pointerup", endDrag);
  $("selectionSvg").addEventListener("pointercancel", endDrag);
  document.querySelectorAll(".style-tool").forEach((button) => {
    if (button.dataset.stylePanel) {
      button.onclick = () => openSelectionStylePanel(button.dataset.stylePanel, button);
    }
  });
  document.addEventListener("pointerdown", (event) => {
    if ($("selectionStylePopover").classList.contains("hidden")) return;
    const clickedPopover = event.target.closest("#selectionStylePopover");
    const clickedStyleToolWithPanel = event.target.closest(".style-tool[data-style-panel]");
    if (!clickedPopover && !clickedStyleToolWithPanel) {
      closeSelectionStylePanel();
    }
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape") closeSelectionStylePanel();
  });
  document.querySelectorAll(".style-segment").forEach((button) => {
    if (button.dataset.overlayMode) {
      button.onclick = () => setOverlayMode(button.dataset.overlayMode);
    }
  });
  document.querySelectorAll(".style-overlay-fit").forEach((button) => {
    button.onclick = () => {
      if (state.selected) {
        state.selected.fit_mode = button.dataset.overlayFit;
        if ($("fitMode")) {
          $("fitMode").value = button.dataset.overlayFit;
        }
        applySelectionStyle();
        drawSelection();
        persistTemplateState(state.selected);
        if (isGreenFrameTemplate()) {
          greenRegularRenderUrlCache.clear();
          refreshGreenFrameMockupPreview();
        }
      }
    };
  });
  $("overlayImageButton").onclick = () => $("overlayImageInput").click();
  $("overlayImageInput").onchange = async (event) => {
    await chooseOverlayImage(event.target.files[0]);
    event.target.value = "";
  };
  $("clearOverlayImage").onclick = clearOverlayImage;
  $("polygonColorInput").oninput = (event) => {
    state.selectionStyle.polygonColor = event.target.value;
    applySelectionStyle();
    saveSelectionStylePreference();
  };
  $("crossColorInput").oninput = (event) => {
    state.selectionStyle.crossColor = event.target.value;
    applySelectionStyle();
    saveSelectionStylePreference();
  };
  $("polygonOpacityInput").oninput = (event) => {
    state.selectionStyle.polygonOpacity = Number(event.target.value);
    applySelectionStyle();
    saveSelectionStylePreference();
  };
  $("crossOpacityInput").oninput = (event) => {
    state.selectionStyle.crossOpacity = Number(event.target.value);
    applySelectionStyle();
    saveSelectionStylePreference();
  };
  $("polygonWidthInput").oninput = (event) => {
    state.selectionStyle.polygonWidth = Number(event.target.value);
    applySelectionStyle();
    saveSelectionStylePreference();
  };
  $("crossWidthInput").oninput = (event) => {
    state.selectionStyle.crossWidth = Number(event.target.value);
    applySelectionStyle();
    saveSelectionStylePreference();
  };
  if ($("fitMode")) {
    $("fitMode").onchange = (event) => {
      if (state.selected) {
        state.selected.fit_mode = event.target.value;
        applySelectionStyle();
        drawSelection();
        persistTemplateState(state.selected);
        if (isGreenFrameTemplate()) {
          greenRegularRenderUrlCache.clear();
          refreshGreenFrameMockupPreview();
        }
      }
    };
  }

  let greenFrameSettingsSaveTimeout = null;
  function updateGreenFrameSettingsFromControls() {
    if (!state.selected || !$("greenFramePanel")) return;
    if (!state.selected.effects) state.selected.effects = defaultEffects();
    state.selected.effects.green_frame_mockups = readGreenFrameControls();
    updateGreenFrameControlLabels();
    // Perspective, envelope, fit and placement all move the artwork, and the
    // lightweight overlay is drawn from them, so redraw it now instead of
    // waiting for the debounced heavy preview.
    drawSelection();
    if (greenFrameSettingsSaveTimeout) clearTimeout(greenFrameSettingsSaveTimeout);
    greenFrameSettingsSaveTimeout = setTimeout(async () => {
      await persistTemplateState(state.selected, { force: state.isPreviewingMockup });
      if (greenFrameControlsApply() && state.selectionStyle.overlayImage) {
        if (state.isPreviewingMockup) {
          refreshPreviewMockup();
        } else {
          // The live in-editor render is a single-frame affair; a set redraws
          // through the lightweight overlay above, and through PREVIEW.
          refreshGreenFrameMockupPreview();
        }
      }
    }, 250);
  }

  [
    "greenUsePerspective",
    "greenUseVectorClip",
    "greenFitMode",
    "greenArtworkScale",
    "greenOffsetX",
    "greenOffsetY",
    "greenEdgeExpand",
    "greenTolerance",
    "greenMaskExpandTop",
    "greenMaskExpandBottom",
    "greenMaskExpandLeft",
    "greenMaskExpandRight",
    "greenMaskBuildQuality",
    "greenFeatherRadius",
    "greenEdgeAARadius",
    "greenAAScale",
    "greenContainBgColor"
  ].forEach((id) => {
    const element = $(id);
    if (!element) return;
    element.addEventListener("input", updateGreenFrameSettingsFromControls);
    element.addEventListener("change", updateGreenFrameSettingsFromControls);
  });

  // Collapsible Green frames controls panel (same UX as the effect panels),
  // with the collapsed state remembered across sessions.
  if ($("greenFramePanelToggle") && $("greenFramePanelBody")) {
    let greenPanelCollapsed = readBoolean(KEYS.greenPanelCollapsed);
    const applyGreenPanelCollapsed = () => {
      $("greenFramePanelBody").classList.toggle("hidden", greenPanelCollapsed);
    };
    applyGreenPanelCollapsed();
    $("greenFramePanelToggle").onclick = () => {
      greenPanelCollapsed = !greenPanelCollapsed;
      applyGreenPanelCollapsed();
      writeBoolean(KEYS.greenPanelCollapsed, greenPanelCollapsed);
    };
  }

  // Realism Effects Event Listeners & Live Preview Updates
  let refreshPreviewTimeout = null;
  let greenFramePreviewTimeout = null;

  async function renderMockupPreviewImage(options = {}) {
    const realism = options.realism !== false;
    const template = options.template || state.selected;
    if (!template) return null;

    // Disable download interactions temporarily to show rendering state
    // (lightweight non-realism renders must not touch the download links)
    if (realism && $("downloadMockupButton")) {
      $("downloadMockupButton").style.pointerEvents = "none";
      $("downloadMockupButton").style.opacity = "0.5";
    }
    if (realism && $("toolbarDownloadButton")) {
      $("toolbarDownloadButton").style.pointerEvents = "none";
      $("toolbarDownloadButton").style.opacity = "0.5";
      $("toolbarDownloadButton").setAttribute("title", "Generating high-fidelity download...");
    }

    try {
      const overlayImage = state.selectionStyle.overlayImage;
      if (!overlayImage) return;

      const file = dataURLtoFile(overlayImage, state.selectionStyle.overlayImageName || "artwork.png");
      const formData = new FormData();
      formData.append("mode", "simple");
      formData.append("template_id", template.template_id);
      formData.append("artwork", file);
      formData.append("realism", realism ? "true" : "false");
      // Canvas previews are throwaway, so the server keeps only the newest few
      // instead of letting every redraw pile up in the outputs folder.
      formData.append("preview", "true");

      let resolvedFitMode = template.fit_mode;
      if (resolvedFitMode === "auto") {
        resolvedFitMode = resolveFitMode(
          "auto",
          state.selectionStyle.overlayImageWidth,
          state.selectionStyle.overlayImageHeight,
          template.artwork_area.width,
          template.artwork_area.height
        );
      }
      formData.append("fit_mode", resolvedFitMode);

      const response = await fetch("/api/mockups/render", {
        method: "POST",
        headers: csrfHeaders(),
        body: formData
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Rendering failed");

      if (realism) setDownloadTarget(data.output_url);
      return data.output_url;
    } catch (err) {
      console.error("Live-preview refresh render failed:", err);
      if (realism) clearDownloadTarget("The mockup could not be rendered");
      return null;
    }
  }

  async function refreshPreviewMockup() {
    if (!state.selected || !state.isPreviewingMockup) return;
    const outputUrl = await renderMockupPreviewImage();
    if (outputUrl && state.isPreviewingMockup) {
      if ($("selectionRenderedMockup")) {
        $("selectionRenderedMockup").src = outputUrl;
        $("selectionRenderedMockup").classList.remove("hidden");
      }
      $("selectionImageOverlay").classList.add("hidden");
    }
  }

  async function refreshGreenFrameMockupPreview() {
    if (!isGreenFrameTemplate() || state.isPreviewingMockup || !state.selectionStyle.overlayImage) return;
    await saveTemplate(false, true);
    const outputUrl = await renderMockupPreviewImage();
    if (outputUrl && isGreenFrameTemplate() && !state.isPreviewingMockup) {
      if ($("selectionRenderedMockup")) {
        $("selectionRenderedMockup").src = outputUrl;
        $("selectionRenderedMockup").classList.remove("hidden");
      }
      $("selectionImageOverlay").classList.add("hidden");
      // This high-fidelity render also satisfies the lightweight regular-mode
      // render, so cache it and mark its key as fulfilled.
      if (state.selected) {
        greenRegularRenderKey = greenRegularRenderCacheKey(state.selected);
        greenRegularRenderUrlCache.set(greenRegularRenderKey, outputUrl);
      }
    }
  }

  // --- Lightweight regular-mode render for green-frame templates ---
  // Browsing green templates uses the same server render as Preview Mode for
  // exact placement/masking, but with realism=false so the effects pipeline
  // is skipped and the response stays fast. Results are cached per
  // template+artwork+settings, and all green templates in the queue are
  // prefetched in the background so switching between them is instant.
  let greenRegularRenderKey = "";
  let greenRegularRenderBusy = false;
  const greenRegularRenderUrlCache = new Map();
  let greenPrefetchToken = 0;
  let greenPrefetchRunning = false;

  function greenRegularRenderCacheKey(template) {
    const style = state.selectionStyle;
    const green = (template.effects && template.effects.green_frame_mockups) || {};
    return [
      template.template_id,
      template.fit_mode || "",
      style.overlayImageName || "",
      style.overlayImage ? style.overlayImage.length : 0,
      JSON.stringify(green)
    ].join("|");
  }

  function showGreenRegularRender(outputUrl) {
    const rendered = $("selectionRenderedMockup");
    if (!rendered) return;
    rendered.src = outputUrl;
    rendered.classList.remove("hidden");
    $("selectionImageOverlay").classList.add("hidden");
  }

  async function ensureGreenFrameRegularRender(template) {
    if (!isGreenFrameTemplate(template) || state.isPreviewingMockup || state.greenFrameDrag) return;
    if (!state.selectionStyle.overlayImage) return;
    const rendered = $("selectionRenderedMockup");
    const hasVisibleRender = Boolean(rendered && rendered.src && !rendered.classList.contains("hidden"));
    const key = greenRegularRenderCacheKey(template);
    const cachedUrl = greenRegularRenderUrlCache.get(key);
    if (cachedUrl) {
      if (key !== greenRegularRenderKey || !hasVisibleRender) {
        greenRegularRenderKey = key;
        showGreenRegularRender(cachedUrl);
      }
      prefetchGreenFrameRegularRenders();
      return;
    }
    if (greenRegularRenderBusy || (key === greenRegularRenderKey && hasVisibleRender)) return;
    greenRegularRenderBusy = true;
    let outputUrl = null;
    try {
      outputUrl = await renderMockupPreviewImage({ realism: false });
    } finally {
      greenRegularRenderBusy = false;
    }
    if (outputUrl) greenRegularRenderUrlCache.set(key, outputUrl);
    if (!state.selected || state.selected.template_id !== template.template_id) return;
    if (state.isPreviewingMockup || !isGreenFrameTemplate()) return;
    const desiredKey = greenRegularRenderCacheKey(state.selected);
    if (desiredKey !== key) {
      // Settings or artwork changed while rendering; fetch the fresh state.
      ensureGreenFrameRegularRender(state.selected);
      return;
    }
    greenRegularRenderKey = key;
    if (outputUrl) showGreenRegularRender(outputUrl);
    prefetchGreenFrameRegularRenders();
  }

  async function prefetchGreenFrameRegularRenders() {
    if (greenPrefetchRunning || !state.selectionStyle.overlayImage) return;
    const token = ++greenPrefetchToken;
    const queue = (state.templates || []).filter(
      (template) => isGreenFrameTemplate(template)
        && !greenRegularRenderUrlCache.has(greenRegularRenderCacheKey(template))
    );
    if (!queue.length) return;
    greenPrefetchRunning = true;
    try {
      for (const template of queue) {
        if (token !== greenPrefetchToken || !state.selectionStyle.overlayImage) return;
        const key = greenRegularRenderCacheKey(template);
        if (greenRegularRenderUrlCache.has(key)) continue;
        const outputUrl = await renderMockupPreviewImage({ realism: false, template });
        if (!outputUrl) continue;
        greenRegularRenderUrlCache.set(key, outputUrl);
        // Warm the browser image cache so the swap is instantaneous on click.
        const img = new Image();
        img.src = outputUrl;
      }
    } finally {
      greenPrefetchRunning = false;
    }
  }

  function setEffectUpdateLoading(active) {
    const overlay = $("effectUpdateOverlay");
    if (!overlay) return;
    overlay.classList.toggle("hidden", !active);
  }

  async function updateEffectsState(options = {}) {
    if (!state.selected) return;
    if (!state.selected.effects) {
      state.selected.effects = defaultEffects();
    }
    const effects = defaultEffects();
    
    effects.inner_shadow.enabled = $("innerShadowEnabled").checked;
    effects.inner_shadow.opacity = Number($("shadowOpacity").value);
    effects.inner_shadow.blur = Number($("shadowBlur").value);
    effects.inner_shadow.top = Number($("shadowTop").value);
    effects.inner_shadow.bottom = Number($("shadowBottom").value);
    effects.inner_shadow.left = Number($("shadowLeft").value);
    effects.inner_shadow.right = Number($("shadowRight").value);
    
    effects.glass_reflection.enabled = $("glassReflectionEnabled").checked;
    effects.glass_reflection.type = $("reflectionType").value;
    effects.glass_reflection.opacity = Number($("reflectionOpacity").value);

    if (!effects.matte_finish) {
      effects.matte_finish = { enabled: false, shadow_lift: 0.08, contrast: -0.15 };
    }
    effects.matte_finish.enabled = $("matteFinishEnabled").checked;
    effects.matte_finish.shadow_lift = Number($("matteShadowLift").value);
    effects.matte_finish.contrast = Number($("matteContrast").value);

    if (!effects.color_tint) {
      effects.color_tint = { enabled: false, temperature: 25, intensity: 0.2 };
    }
    effects.color_tint.enabled = $("colorTintEnabled").checked;
    effects.color_tint.temperature = Number($("tintTemperature").value);
    effects.color_tint.intensity = Number($("tintIntensity").value);

    if (!effects.gobo_shadow) {
      effects.gobo_shadow = { enabled: false, opacity: 0.3, scale: 1.0 };
    }
    effects.gobo_shadow.enabled = $("goboShadowEnabled").checked;
    effects.gobo_shadow.opacity = Number($("goboOpacity").value);
    effects.gobo_shadow.scale = Number($("goboScale").value);

    // Parse Photoshop Adjustments
    if (!effects.photoshop_adjustments) {
      effects.photoshop_adjustments = { enabled: false, brightness: 0.0, contrast: 0.0, saturation: 0.0, color_filter: "none" };
    }
    effects.photoshop_adjustments.enabled = $("photoshopAdjustmentsEnabled").checked;
    effects.photoshop_adjustments.brightness = Number($("photoshopBrightness").value);
    effects.photoshop_adjustments.contrast = Number($("photoshopContrast").value);
    effects.photoshop_adjustments.saturation = Number($("photoshopSaturation").value);
    effects.photoshop_adjustments.color_filter = $("photoshopColorFilter").value;

    // Parse Global Reflections
    if (!effects.global_reflections) {
      effects.global_reflections = { enabled: false, window_type: "none", window_opacity: 0.2, window_blur: 20.0, rays_type: "none", rays_opacity: 0.2, rays_angle: 0.0 };
    }
    effects.global_reflections.enabled = $("globalReflectionsEnabled").checked;
    effects.global_reflections.window_type = $("globalWindowType").value;
    effects.global_reflections.window_opacity = Number($("globalWindowOpacity").value);
    effects.global_reflections.window_blur = Number($("globalWindowBlur").value);
    effects.global_reflections.rays_type = $("globalRaysType").value;
    effects.global_reflections.rays_opacity = Number($("globalRaysOpacity").value);
    effects.global_reflections.rays_angle = Number($("globalRaysAngle").value);

    // Parse Global PNG Overlay
    if (!effects.global_png_overlay) {
      effects.global_png_overlay = cloneObject(DEFAULT_EFFECTS.global_png_overlay);
    }
    effects.global_png_overlay.enabled = $("globalPngOverlayEnabled").checked;
    EFFECT_DOM.global_png_overlay.fields.forEach((field) => {
      const element = $(field.id);
      if (!element) return;
      if (field.type === "number") effects.global_png_overlay[field.prop] = Number(element.value);
      else if (field.type === "boolean") effects.global_png_overlay[field.prop] = element.checked;
      else effects.global_png_overlay[field.prop] = element.value;
    });
    const primaryOverlayRoot = effectGroupForKey("global_png_overlay", "1");
    effects.global_png_overlay.image = primaryOverlayRoot?.dataset.overlayImage || primaryEffect(state.selected.effects, "global_png_overlay").image || "";
    
    // Parse target switches from segmented controls
    document.querySelectorAll('.effect-group[data-effect-instance="1"] .segmented-control[data-effect-key]').forEach((ctrl) => {
      const key = ctrl.getAttribute("data-effect-key");
      const activeBtn = ctrl.querySelector(".segment-btn.active");
      if (activeBtn && effects[key]) {
        effects[key].target = activeBtn.getAttribute("data-target-val");
      }
    });

    Object.keys(EFFECT_DOM).forEach((key) => {
      const secondRoot = effectGroupForKey(key, "2");
      if (!secondRoot) return;
      effects[key] = [
        effects[key],
        readEffectInstanceValues(secondRoot, key)
      ];
    });

    state.selected.effects = effects;

    const showLoading = Boolean(options.showLoading);
    const loadingStartedAt = Date.now();
    if (showLoading) {
      setEffectUpdateLoading(true);
      setStatus("Applying effect changes...");
    }

    try {
      await persistTemplateState(state.selected, { force: state.isPreviewingMockup });

      // Debounce live-refresh in preview mode, except checkbox changes show a loading overlay until refresh completes.
      if (state.isPreviewingMockup) {
        if (refreshPreviewTimeout) clearTimeout(refreshPreviewTimeout);
        if (showLoading) {
          await refreshPreviewMockup();
        } else {
          refreshPreviewTimeout = setTimeout(() => {
            refreshPreviewMockup();
          }, 250);
        }
      }
    } finally {
      if (showLoading) {
        const elapsed = Date.now() - loadingStartedAt;
        if (elapsed < 450) {
          await new Promise((resolve) => setTimeout(resolve, 450 - elapsed));
        }
        setEffectUpdateLoading(false);
        setStatus("Effect changes applied");
      }
    }
  }

  setupEffectInstanceControls();

  // Target segment buttons click delegation
  document.addEventListener("click", (e) => {
    const addButton = e.target.closest(".effect-add-instance");
    if (addButton) {
      e.preventDefault();
      const key = addButton.dataset.effectKey;
      if (!key || effectGroupForKey(key, "2")) return;
      const source = effectGroupForKey(key, "1");
      createSecondEffectInstance(key, readEffectInstanceValues(source, key));
      updateEffectsState();
      return;
    }

    const removeButton = e.target.closest(".effect-remove-instance");
    if (removeButton) {
      e.preventDefault();
      removeSecondEffectInstance(removeButton.dataset.effectKey);
      return;
    }

    const titleToggle = e.target.closest(".effect-title-toggle");
    if (titleToggle && !e.target.matches('input[type="checkbox"]')) {
      e.preventDefault();
      toggleEffectPanelCollapsed(titleToggle.closest(".effect-group"));
      return;
    }

    const linkButton = e.target.closest('[data-original-id="linkShadowSides"]');
    if (linkButton && linkButton.id !== "linkShadowSides") {
      e.preventDefault();
      linkButton.classList.toggle("active");
      return;
    }

    const overlayUploadButton = e.target.closest('[data-original-id="globalOverlayUploadBtn"]');
    if (overlayUploadButton && overlayUploadButton.id !== "globalOverlayUploadBtn") {
      e.preventDefault();
      const root = overlayUploadButton.closest(".effect-group");
      const input = getFieldElement(root, "globalOverlayUploadInput");
      if (input) input.click();
      return;
    }

    const clearOverlayButton = e.target.closest('[data-original-id="clearGlobalOverlayBtn"]');
    if (clearOverlayButton && clearOverlayButton.id !== "clearGlobalOverlayBtn") {
      e.preventDefault();
      const root = clearOverlayButton.closest(".effect-group");
      root.dataset.overlayImage = "";
      const name = getFieldElement(root, "globalOverlayName");
      if (name) {
        name.textContent = "No file";
        name.removeAttribute("title");
      }
      updateEffectsState();
      if (state.isPreviewingMockup) refreshPreviewMockup();
      return;
    }

    const btn = e.target.closest(".segment-btn");
    if (!btn) return;
    
    const ctrl = btn.closest(".segmented-control");
    if (!ctrl) return;
    
    ctrl.querySelectorAll(".segment-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    updateEffectsState();
  });

  document.addEventListener("input", (e) => {
    const root = e.target.closest('.effect-group[data-effect-instance="2"]');
    if (!root) return;
    const key = root.dataset.effectKey;
    const def = EFFECT_DOM[key];
    if (!def) return;
    const originalId = e.target.dataset.originalId;
    const field = def.fields.find((item) => item.id === originalId);
    if (!field) return;

    setEffectValueLabel(field, e.target.value, root);
    if (key === "inner_shadow" && ["shadowTop", "shadowBottom", "shadowLeft", "shadowRight"].includes(originalId)) {
      const linkButton = getFieldElement(root, "linkShadowSides");
      if (linkButton?.classList.contains("active")) {
        ["shadowTop", "shadowBottom", "shadowLeft", "shadowRight"].forEach((id) => {
          if (id === originalId) return;
          const input = getFieldElement(root, id);
          const sideField = def.fields.find((item) => item.id === id);
          if (input) input.value = e.target.value;
          if (sideField) setEffectValueLabel(sideField, e.target.value, root);
        });
      }
    }
    updateEffectsState();
  });

  document.addEventListener("change", async (e) => {
    const root = e.target.closest('.effect-group[data-effect-instance="2"]');
    if (!root) return;
    const key = root.dataset.effectKey;
    const def = EFFECT_DOM[key];
    if (!def) return;

    if (e.target.dataset.originalId === def.enabledId) {
      await updateEffectsState({ showLoading: true });
      return;
    }

    if (e.target.dataset.originalId === "globalOverlayUploadInput") {
      const file = e.target.files[0];
      if (file) {
        try {
          root.dataset.overlayImage = await fileToDataUrl(file);
          const name = getFieldElement(root, "globalOverlayName");
          if (name) {
            name.textContent = "Overlay loaded";
            name.setAttribute("title", file.name);
          }
          updateEffectsState();
          if (state.isPreviewingMockup) refreshPreviewMockup();
        } catch (err) {
          toast("Failed to read overlay image: " + err.message);
        }
      }
      e.target.value = "";
      return;
    }

    if (def.fields.some((field) => field.id === e.target.dataset.originalId)) {
      updateEffectsState();
    }
  });

  // Link button toggle
  $("linkShadowSides").onclick = (e) => {
    e.preventDefault();
    $("linkShadowSides").classList.toggle("active");
  };

  // Shadow enabled checkbox
  $("innerShadowEnabled").onchange = async (e) => {
    await updateEffectsState({ showLoading: true });
  };

  // Shadow Opacity slider
  $("shadowOpacity").oninput = (e) => {
    $("shadowOpacityVal").textContent = Math.round(Number(e.target.value) * 100) + "%";
    updateEffectsState();
  };

  // Shadow Blur slider
  $("shadowBlur").oninput = (e) => {
    $("shadowBlurVal").textContent = e.target.value + "px";
    updateEffectsState();
  };

  // Handle shadow side sliders with linking support
  const shadowSides = ["Top", "Bottom", "Left", "Right"];
  shadowSides.forEach(side => {
    $(`shadow${side}`).oninput = (e) => {
      const val = e.target.value;
      $(`shadow${side}Val`).textContent = val + "px";
      
      if ($("linkShadowSides").classList.contains("active")) {
        shadowSides.forEach(otherSide => {
          if (otherSide !== side) {
            $(`shadow${otherSide}`).value = val;
            $(`shadow${otherSide}Val`).textContent = val + "px";
          }
        });
      }
      updateEffectsState();
    };
  });

  // Glass enabled checkbox
  $("glassReflectionEnabled").onchange = async (e) => {
    await updateEffectsState({ showLoading: true });
  };

  // Glass reflection type select
  $("reflectionType").onchange = () => {
    updateEffectsState();
  };

  // Glass Opacity slider
  $("reflectionOpacity").oninput = (e) => {
    $("reflectionOpacityVal").textContent = Math.round(Number(e.target.value) * 100) + "%";
    updateEffectsState();
  };

  // Matte finish enabled checkbox
  $("matteFinishEnabled").onchange = async (e) => {
    await updateEffectsState({ showLoading: true });
  };
  // Matte Shadow Lift slider
  $("matteShadowLift").oninput = (e) => {
    $("matteShadowLiftVal").textContent = Math.round(Number(e.target.value) * 100) + "%";
    updateEffectsState();
  };
  // Matte Contrast slider
  $("matteContrast").oninput = (e) => {
    $("matteContrastVal").textContent = Math.round(Number(e.target.value) * 100) + "%";
    updateEffectsState();
  };

  // Color tint enabled checkbox
  $("colorTintEnabled").onchange = async (e) => {
    await updateEffectsState({ showLoading: true });
  };
  // Color Temperature slider
  $("tintTemperature").oninput = (e) => {
    const val = Number(e.target.value);
    const sign = val > 0 ? "+" : "";
    $("tintTemperatureVal").textContent = sign + val;
    updateEffectsState();
  };
  // Color tint intensity slider
  $("tintIntensity").oninput = (e) => {
    $("tintIntensityVal").textContent = Math.round(Number(e.target.value) * 100) + "%";
    updateEffectsState();
  };

  // Gobo shadow enabled checkbox
  $("goboShadowEnabled").onchange = async (e) => {
    await updateEffectsState({ showLoading: true });
  };
  // Gobo shadow Opacity slider
  $("goboOpacity").oninput = (e) => {
    $("goboOpacityVal").textContent = Math.round(Number(e.target.value) * 100) + "%";
    updateEffectsState();
  };
  // Gobo shadow Scale slider
  $("goboScale").oninput = (e) => {
    $("goboScaleVal").textContent = Number(e.target.value).toFixed(1) + "x";
    updateEffectsState();
  };

  // Photoshop adjustments enabled checkbox
  $("photoshopAdjustmentsEnabled").onchange = async (e) => {
    await updateEffectsState({ showLoading: true });
  };
  // Photoshop filter select
  $("photoshopColorFilter").onchange = () => {
    updateEffectsState();
  };
  // Photoshop brightness slider
  $("photoshopBrightness").oninput = (e) => {
    const val = Math.round(Number(e.target.value) * 100);
    $("photoshopBrightnessVal").textContent = (val >= 0 ? "+" : "") + val + "%";
    updateEffectsState();
  };
  // Photoshop contrast slider
  $("photoshopContrast").oninput = (e) => {
    const val = Math.round(Number(e.target.value) * 100);
    $("photoshopContrastVal").textContent = (val >= 0 ? "+" : "") + val + "%";
    updateEffectsState();
  };
  // Photoshop saturation slider
  $("photoshopSaturation").oninput = (e) => {
    const val = Math.round(Number(e.target.value) * 100);
    $("photoshopSaturationVal").textContent = (val >= 0 ? "+" : "") + val + "%";
    updateEffectsState();
  };

  // Global reflections enabled checkbox
  $("globalReflectionsEnabled").onchange = async (e) => {
    await updateEffectsState({ showLoading: true });
  };
  // Window shadow type select
  $("globalWindowType").onchange = () => {
    updateEffectsState();
  };
  // Window opacity slider
  $("globalWindowOpacity").oninput = (e) => {
    $("globalWindowOpacityVal").textContent = Math.round(Number(e.target.value) * 100) + "%";
    updateEffectsState();
  };
  // Window blur softness slider
  $("globalWindowBlur").oninput = (e) => {
    $("globalWindowBlurVal").textContent = e.target.value + "px";
    updateEffectsState();
  };
  // Rays type select
  $("globalRaysType").onchange = () => {
    updateEffectsState();
  };
  // Rays opacity slider
  $("globalRaysOpacity").oninput = (e) => {
    $("globalRaysOpacityVal").textContent = Math.round(Number(e.target.value) * 100) + "%";
    updateEffectsState();
  };
  // Rays angle slider
  $("globalRaysAngle").oninput = (e) => {
    $("globalRaysAngleVal").textContent = e.target.value + "°";
    updateEffectsState();
  };

  // Global PNG Overlay enabled checkbox
  $("globalPngOverlayEnabled").onchange = async (e) => {
    await updateEffectsState({ showLoading: true });
  };
  // Trigger file click
  $("globalOverlayUploadBtn").onclick = () => $("globalOverlayUploadInput").click();
  // Upload PNG overlay
  $("globalOverlayUploadInput").onchange = async (event) => {
    const file = event.target.files[0];
    if (file) {
      try {
        const dataUrl = await fileToDataUrl(file);
        if (state.selected) {
          if (!state.selected.effects) state.selected.effects = {};
          if (!state.selected.effects.global_png_overlay) {
            state.selected.effects.global_png_overlay = cloneObject(DEFAULT_EFFECTS.global_png_overlay);
          }
          const overlayRoot = effectGroupForKey("global_png_overlay", "1");
          if (overlayRoot) overlayRoot.dataset.overlayImage = dataUrl;
          if (Array.isArray(state.selected.effects.global_png_overlay)) {
            state.selected.effects.global_png_overlay[0].image = dataUrl;
            state.selected.effects.global_png_overlay[0].enabled = true;
          } else {
            state.selected.effects.global_png_overlay.image = dataUrl;
            state.selected.effects.global_png_overlay.enabled = true;
          }
          
          $("globalOverlayName").textContent = "Overlay loaded";
          $("globalOverlayName").setAttribute("title", file.name);
          
          updateEffectsState();
          renderGlobalOverlayPlacement();
          
          if (state.isPreviewingMockup) {
            refreshPreviewMockup();
          }
        }
      } catch (err) {
        toast("Failed to read overlay image: " + err.message);
      }
    }
    event.target.value = "";
  };
  // Clear PNG overlay
  $("clearGlobalOverlayBtn").onclick = (e) => {
    e.preventDefault();
    if (state.selected && state.selected.effects && state.selected.effects.global_png_overlay) {
      const overlayRoot = effectGroupForKey("global_png_overlay", "1");
      if (overlayRoot) overlayRoot.dataset.overlayImage = "";
      if (Array.isArray(state.selected.effects.global_png_overlay)) {
        state.selected.effects.global_png_overlay[0].image = "";
      } else {
        state.selected.effects.global_png_overlay.image = "";
      }
      $("globalOverlayName").textContent = "No file";
      $("globalOverlayName").removeAttribute("title");
      setGlobalOverlayPlacementActive(false);
      updateEffectsState();
      if (state.isPreviewingMockup) {
        refreshPreviewMockup();
      }
    }
  };
  // Global PNG overlay opacity slider
  EFFECT_DOM.global_png_overlay.fields.forEach((field) => {
    const element = $(field.id);
    if (!element) return;
    const eventName = field.type === "string" || field.type === "boolean" ? "change" : "input";
    element.addEventListener(eventName, async (event) => {
      if (field.valueId) setEffectValueLabel(field, field.type === "boolean" ? event.target.checked : event.target.value);
      await updateEffectsState(field.type === "boolean" ? { showLoading: true } : {});
      renderGlobalOverlayPlacement();
    });
  });

  if ($("globalOverlayPlaceBtn")) {
    $("globalOverlayPlaceBtn").onclick = (event) => {
      event.preventDefault();
      setGlobalOverlayPlacementActive(!state.globalOverlayPlacementActive);
    };
  }

  const placementLayer = $("globalOverlayPlacementLayer");
  if (placementLayer) {
    placementLayer.addEventListener("pointerdown", (event) => {
      if (!state.globalOverlayPlacementActive || !state.selected) return;
      event.preventDefault();
      const rect = getRenderedImageRect($("canvasImage"));
      if (!rect) return;
      state.globalOverlayDrag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startPositionX: Number($("globalOverlayPositionX").value || 0),
        startPositionY: Number($("globalOverlayPositionY").value || 0),
        rect
      };
      placementLayer.classList.add("dragging");
      placementLayer.setPointerCapture(event.pointerId);
    });

    placementLayer.addEventListener("pointermove", (event) => {
      const drag = state.globalOverlayDrag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      event.preventDefault();
      const nextX = Math.max(-1, Math.min(1, drag.startPositionX + ((event.clientX - drag.startX) / (drag.rect.width * state.zoom)) * 2));
      const nextY = Math.max(-1, Math.min(1, drag.startPositionY + ((event.clientY - drag.startY) / (drag.rect.height * state.zoom)) * 2));
      $("globalOverlayPositionX").value = nextX.toFixed(2);
      $("globalOverlayPositionY").value = nextY.toFixed(2);
      setEffectValueLabel(EFFECT_DOM.global_png_overlay.fields.find((field) => field.id === "globalOverlayPositionX"), nextX);
      setEffectValueLabel(EFFECT_DOM.global_png_overlay.fields.find((field) => field.id === "globalOverlayPositionY"), nextY);
      renderGlobalOverlayPlacement();
    });

    const endPlacementDrag = async (event) => {
      const drag = state.globalOverlayDrag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      state.globalOverlayDrag = null;
      placementLayer.classList.remove("dragging");
      try {
        placementLayer.releasePointerCapture(event.pointerId);
      } catch (_err) { }
      await updateEffectsState();
    };
    placementLayer.addEventListener("pointerup", endPlacementDrag);
    placementLayer.addEventListener("pointercancel", endPlacementDrag);
  }

  if ($("greenFramePlaceBtn")) {
    $("greenFramePlaceBtn").onclick = (event) => {
      event.preventDefault();
      setGreenFramePlacementActive(!state.greenFramePlacementActive);
    };
  }

  const greenFramePlacementLayer = $("greenFramePlacementLayer");
  if (greenFramePlacementLayer) {
    greenFramePlacementLayer.addEventListener("pointerdown", (event) => {
      if (!state.greenFramePlacementActive || !state.selected) return;
      event.preventDefault();
      const rect = getRenderedImageRect($("canvasImage"));
      if (!rect) return;
      
      const template = state.selected;
      const displayW = (template.artwork_area.width / template.canvas_width) * rect.width;
      const displayH = (template.artwork_area.height / template.canvas_height) * rect.height;
      
      state.greenFrameDrag = {
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        startOffsetX: Number($("greenOffsetX").value || 0),
        startOffsetY: Number($("greenOffsetY").value || 0),
        displayW,
        displayH
      };
      
      if ($("selectionRenderedMockup")) $("selectionRenderedMockup").classList.add("hidden");
      if ($("selectionImageOverlay")) $("selectionImageOverlay").classList.remove("hidden");
      
      greenFramePlacementLayer.classList.add("dragging");
      greenFramePlacementLayer.setPointerCapture(event.pointerId);
    });

    greenFramePlacementLayer.addEventListener("pointermove", (event) => {
      const drag = state.greenFrameDrag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      event.preventDefault();
      
      const dx = (event.clientX - drag.startX) / state.zoom;
      const dy = (event.clientY - drag.startY) / state.zoom;
      
      const deltaPercentX = (200 * dx) / drag.displayW;
      const deltaPercentY = (200 * dy) / drag.displayH;
      
      const nextX = Math.round(Math.max(-100, Math.min(100, drag.startOffsetX + deltaPercentX)));
      const nextY = Math.round(Math.max(-100, Math.min(100, drag.startOffsetY + deltaPercentY)));
      
      $("greenOffsetX").value = nextX;
      $("greenOffsetY").value = nextY;
      
      setGreenFrameLabel("greenOffsetXVal", nextX, "%");
      setGreenFrameLabel("greenOffsetYVal", nextY, "%");
      
      const overlayImg = $("selectionOverlayImg");
      if (overlayImg && state.selected) {
        const canvasW = state.selected.artwork_area.width;
        const canvasH = state.selected.artwork_area.height;
        const artworkScale = Number($("greenArtworkScale").value) / 100;
        const fitMode = $("greenFitMode").value || "cover";
        
        const naturalW = state.selectionStyle.overlayImageWidth || overlayImg.naturalWidth || 100;
        const naturalH = state.selectionStyle.overlayImageHeight || overlayImg.naturalHeight || 100;
        
        let baseW = canvasW;
        let baseH = canvasH;
        if (fitMode !== "stretch") {
          const imageRatio = naturalW / naturalH;
          const containerRatio = canvasW / canvasH;
          if (fitMode === "contain") {
            if (imageRatio > containerRatio) {
              baseW = canvasW;
              baseH = canvasW / imageRatio;
            } else {
              baseH = canvasH;
              baseW = canvasH * imageRatio;
            }
          } else {
            if (imageRatio > containerRatio) {
              baseH = canvasH;
              baseW = canvasH * imageRatio;
            } else {
              baseW = canvasW;
              baseH = canvasW / imageRatio;
            }
          }
        }
        
        const scaledW = baseW * artworkScale;
        const scaledH = baseH * artworkScale;
        
        overlayImg.style.width = `${scaledW}px`;
        overlayImg.style.height = `${scaledH}px`;
        overlayImg.style.left = `${(canvasW - scaledW) / 2 + (nextX / 100) * canvasW / 2}px`;
        overlayImg.style.top = `${(canvasH - scaledH) / 2 + (nextY / 100) * canvasH / 2}px`;
      }
    });

    const endGreenFramePlacementDrag = async (event) => {
      const drag = state.greenFrameDrag;
      if (!drag || drag.pointerId !== event.pointerId) return;
      state.greenFrameDrag = null;
      greenFramePlacementLayer.classList.remove("dragging");
      try {
        greenFramePlacementLayer.releasePointerCapture(event.pointerId);
      } catch (_err) { }
      updateGreenFrameSettingsFromControls();
    };
    
    greenFramePlacementLayer.addEventListener("pointerup", endGreenFramePlacementDrag);
    greenFramePlacementLayer.addEventListener("pointercancel", endGreenFramePlacementDrag);
  }

  // Apply Matte to all button event listener
  if ($("applyMatteToAllBtn")) {
    $("applyMatteToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects || !state.selected.effects.matte_finish) {
        toast("No matte finish settings to apply.");
        return;
      }
      
      const activeMatte = JSON.parse(JSON.stringify(state.selected.effects.matte_finish));
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying Faded Matte settings to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          if (!t.effects) t.effects = {};
          t.effects.matte_finish = JSON.parse(JSON.stringify(activeMatte));
          
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: t.effects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Faded Matte settings applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error(err);
        toast("Failed to apply Faded Matte settings.");
        setStatus("Ready");
      }
    };
  }

  // Apply Tint to all button event listener
  if ($("applyTintToAllBtn")) {
    $("applyTintToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects || !state.selected.effects.color_tint) {
        toast("No color warmth settings to apply.");
        return;
      }
      
      const activeTint = JSON.parse(JSON.stringify(state.selected.effects.color_tint));
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying Ambient Warmth settings to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          if (!t.effects) t.effects = {};
          t.effects.color_tint = JSON.parse(JSON.stringify(activeTint));
          
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: t.effects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Ambient Warmth settings applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error(err);
        toast("Failed to apply Ambient Warmth settings.");
        setStatus("Ready");
      }
    };
  }

  // Apply Gobo Shadow to all button event listener
  if ($("applyGoboToAllBtn")) {
    $("applyGoboToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects || !state.selected.effects.gobo_shadow) {
        toast("No sunlight shadow settings to apply.");
        return;
      }
      
      const activeGobo = JSON.parse(JSON.stringify(state.selected.effects.gobo_shadow));
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying Sunlight Blinds settings to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          if (!t.effects) t.effects = {};
          t.effects.gobo_shadow = JSON.parse(JSON.stringify(activeGobo));
          
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: t.effects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Sunlight Blinds settings applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error(err);
        toast("Failed to apply Sunlight Blinds settings.");
        setStatus("Ready");
      }
    };
  }

  // Apply Photoshop Adjustments to all button event listener
  if ($("applyPhotoshopToAllBtn")) {
    $("applyPhotoshopToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects || !state.selected.effects.photoshop_adjustments) {
        toast("No photoshop adjustment settings to apply.");
        return;
      }
      
      const activePS = JSON.parse(JSON.stringify(state.selected.effects.photoshop_adjustments));
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying Photoshop Color Filter settings to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          if (!t.effects) t.effects = {};
          t.effects.photoshop_adjustments = JSON.parse(JSON.stringify(activePS));
          
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: t.effects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Photoshop settings applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error(err);
        toast("Failed to apply Photoshop settings.");
        setStatus("Ready");
      }
    };
  }

  // Apply Global Reflections & Sun rays to all button event listener
  if ($("applyGlobalReflectionsToAllBtn")) {
    $("applyGlobalReflectionsToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects || !state.selected.effects.global_reflections) {
        toast("No global reflections settings to apply.");
        return;
      }
      
      const activeRef = JSON.parse(JSON.stringify(state.selected.effects.global_reflections));
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying Global Reflections & Rays settings to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          if (!t.effects) t.effects = {};
          t.effects.global_reflections = JSON.parse(JSON.stringify(activeRef));
          
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: t.effects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Global Reflections & Rays settings applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error(err);
        toast("Failed to apply Global Reflections & Rays settings.");
        setStatus("Ready");
      }
    };
  }

  // Apply Global PNG Overlay to all button event listener
  if ($("applyGlobalOverlayToAllBtn")) {
    $("applyGlobalOverlayToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects || !state.selected.effects.global_png_overlay) {
        toast("No global PNG overlay settings to apply.");
        return;
      }
      
      const activeOverlay = JSON.parse(JSON.stringify(state.selected.effects.global_png_overlay));
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying Global PNG Overlay settings to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          if (!t.effects) t.effects = {};
          t.effects.global_png_overlay = JSON.parse(JSON.stringify(activeOverlay));
          
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: t.effects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Global PNG Overlay settings applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error(err);
        toast("Failed to apply Global PNG Overlay settings.");
        setStatus("Ready");
      }
    };
  }

  // Apply to all button event listener
  if ($("applyEffectsToAllBtn")) {
    $("applyEffectsToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects) {
        toast("No realism effects to apply.");
        return;
      }
      
      const activeEffects = state.selected.effects;
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying realism effects to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: activeEffects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Realism effects applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error("Apply to all failed:", err);
        toast("Failed to apply realism effects to all mockups.");
        setStatus("Ready");
      }
    };
  }

  // Apply Inner Frame Shadow to all button event listener
  if ($("applyShadowToAllBtn")) {
    $("applyShadowToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects || !state.selected.effects.inner_shadow) {
        toast("No shadow settings to apply.");
        return;
      }
      
      const activeShadow = JSON.parse(JSON.stringify(state.selected.effects.inner_shadow));
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying shadow settings to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          if (!t.effects) {
            t.effects = {
              inner_shadow: { enabled: false, top: 10, right: 10, bottom: 10, left: 10, opacity: 0.4, blur: 15 },
              glass_reflection: { enabled: false, type: "diagonal", opacity: 0.15 }
            };
          }
          t.effects.inner_shadow = JSON.parse(JSON.stringify(activeShadow));
          
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: t.effects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Inner Shadow settings applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error("Apply shadow to all failed:", err);
        toast("Failed to apply shadow settings to all mockups.");
        setStatus("Ready");
      }
    };
  }

  // Apply Glass Reflection to all button event listener
  if ($("applyReflectionToAllBtn")) {
    $("applyReflectionToAllBtn").onclick = async (e) => {
      e.preventDefault();
      if (!state.selected || !state.selected.effects || !state.selected.effects.glass_reflection) {
        toast("No reflection settings to apply.");
        return;
      }
      
      const activeReflection = JSON.parse(JSON.stringify(state.selected.effects.glass_reflection));
      const otherTemplates = state.templates.filter(t => t.template_id !== state.selected.template_id);
      
      if (otherTemplates.length === 0) {
        toast("No other mockups in this category.");
        return;
      }
      
      setStatus("Applying reflection settings to all mockups...");
      try {
        await Promise.all(otherTemplates.map(async (t) => {
          if (!t.effects) {
            t.effects = {
              inner_shadow: { enabled: false, top: 10, right: 10, bottom: 10, left: 10, opacity: 0.4, blur: 15 },
              glass_reflection: { enabled: false, type: "diagonal", opacity: 0.15 }
            };
          }
          t.effects.glass_reflection = JSON.parse(JSON.stringify(activeReflection));
          
          const payload = await api(`/api/admin/templates/${t.template_id}`, {
            method: "PATCH",
            body: JSON.stringify({
              effects: t.effects
            })
          });
          t.effects = payload.template.effects;
        }));
        toast(`Glass Reflection settings applied to ${otherTemplates.length} other mockup(s).`);
        setStatus("Ready");
      } catch (err) {
        console.error("Apply reflection to all failed:", err);
        toast("Failed to apply reflection settings to all mockups.");
        setStatus("Ready");
      }
    };
  }

  applySelectionStyle();
  $("detectButton").onclick = detectFrame;

  if ($("maskDetectTolerance")) {
    $("maskDetectTolerance").oninput = () => {
      const val = $("maskDetectTolerance").value;
      if ($("maskDetectToleranceVal")) $("maskDetectToleranceVal").textContent = val;
    };
  }
  async function resetTemplateDetection() {
    if (!state.selected || state.busy) return;
    const templateName = state.selected.name || "this template";
    const confirmed = await appConfirm(
      `This will restore "${templateName}" to its initial detection state as when it was first imported into MockupGen.\n\nAll manual modifications will be reset. Are you sure you want to proceed?`,
      "Reset Detection",
      true,
      "Reset to initial state"
    );
    if (!confirmed) return;
    try {
      exitAllDetectionModes();
      setBusy(true);
      setStatus("Resetting detection to initial state...");
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/reset-detection`, { method: "POST" });
      state.selected = payload.template;
      greenRegularRenderUrlCache.clear();
      if ($("selectionRenderedMockup")) {
        $("selectionRenderedMockup").classList.add("hidden");
        $("selectionRenderedMockup").src = "";
      }
      await loadCategories(state.selected.category_id);
      await loadTemplates(state.selected.template_id);
      renderEditor();
      toast("Mockup restored to initial imported state");
      setStatus("Mockup reset to initial state");
    } catch (err) {
      toast(err.message || "Failed to reset detection");
      setStatus("Reset failed", true);
    } finally {
      setBusy(false);
    }
  }

  if ($("resetDetectionButton")) {
    $("resetDetectionButton").onclick = resetTemplateDetection;
  }

  $("saveButton").onclick = async () => {
    try {
      await saveTemplate();
      setStatus("Draft saved");
    } catch (error) {
      toast(error.message);
    }
  };
  $("approveButton").onclick = approveTemplate;
  $("publishButton").onclick = approveTemplate;

  $("openGuide").onclick = () => {
    if (state.busy) return;
    $("guideDrawer").classList.add("open");
  };
  $("closeGuide").onclick = () => $("guideDrawer").classList.remove("open");
  const openEngine = () => {
    if (state.busy) return;
    $("engineDrawer").classList.add("open");
  };
  $("openEngine").onclick = openEngine;
  $("engineButton").onclick = openEngine;
  $("editEngine").onclick = openEngine;
  $("closeEngine").onclick = () => $("engineDrawer").classList.remove("open");

  document.querySelectorAll(".provider-card").forEach((card) => {
    card.onclick = () => showProvider(card.dataset.provider);
  });
  document.querySelectorAll(".detection-mode-button").forEach((button) => {
    button.onclick = () => switchDetectionProvider(button.dataset.provider);
  });
  $("vertexModel").onchange = () => {
    if ($("vertexModel").value === "gemini-3-flash-preview") $("vertexLocation").value = "global";
    showProvider(state.settings.DETECTION_PROVIDER);
  };
  $("vertexLocation").onchange = () => showProvider(state.settings.DETECTION_PROVIDER);
  if ($("classicGreenFramesMode")) $("classicGreenFramesMode").onchange = () => showProvider(state.settings.DETECTION_PROVIDER);
  if ($("classicGreenEdgeExpand")) $("classicGreenEdgeExpand").oninput = () => showProvider(state.settings.DETECTION_PROVIDER);
  $("localUrl").oninput = () => showProvider(state.settings.DETECTION_PROVIDER);
  $("localModel").onchange = () => showProvider(state.settings.DETECTION_PROVIDER);
  $("refreshLocalModels").onclick = () => loadLocalModels(true);
  $("testEngine").onclick = testEngine;
  $("saveSettings").onclick = saveSettings;
  $("logoutButton").onclick = async () => {
    if (state.busy) return;
    await api("/api/admin/logout", { method: "POST" });
    window.location.href = "/admin/login";
  };
  window.addEventListener("resize", drawSelection);

  // Toggle Realistic Mockup Preview & Download Feature
  /** The pencil in the actions rail is the way back out of a preview.
   *
   * It marks itself unavailable the same way the other tools in that rail do
   * -- with .hidden, which the rail's stylesheet reads as "greyed out" rather
   * than "gone", so the row of icons never shifts under the hand.
   */
  function syncActionRail() {
    const edit = $("toolbarEditButton");
    if (edit) edit.classList.toggle("hidden", !state.isPreviewingMockup);
  }

  async function togglePreviewMode() {
    if (!state.selected || state.busy) return;

    closeSelectionStylePanel();

    if (state.isPreviewingMockup) {
      // Toggle back to Edit Mode instantly
      state.isPreviewingMockup = false;
      
      // Sync Header buttons
      if ($("previewMockupButton")) $("previewMockupButton").textContent = "Preview Mockup";
      if ($("downloadMockupButton")) $("downloadMockupButton").classList.add("hidden");

      // Sync Toolbar buttons
      if ($("toolbarPreviewButton")) $("toolbarPreviewButton").classList.remove("active");
      syncActionRail();
      if ($("toolbarDownloadButton")) $("toolbarDownloadButton").classList.add("hidden");

      // Leaving preview drops the render, so drop what was downloadable with
      // it -- and give the blob back to the browser.
      clearDownloadTarget("Preview the mockup to download it");

      // Hide rendered mockup preview
      if ($("selectionRenderedMockup")) {
        $("selectionRenderedMockup").classList.add("hidden");
        $("selectionRenderedMockup").src = "";
      }
      
      // Show editor visual layers and the live client-side warped image
      $("selectionSvg").classList.remove("hidden");
      const showOverlay = state.selectionStyle.overlayMode === "image" && Boolean(state.selectionStyle.overlayImage);
      $("selectionImageOverlay").classList.toggle("hidden", !showOverlay);
      
      drawSelection();
      setStatus("Edit mode active");
    } else {
      // Toggle to Preview Mode instantly (Zero Latency)
      const overlayImage = state.selectionStyle.overlayImage;
      if (!overlayImage) {
        toast("Please select an overlay image first.");
        return;
      }

      state.isPreviewingMockup = true;
      
      // 1. Instantly hide editor visual markers
      $("selectionSvg").classList.add("hidden");
      
      // 2. Ensure live warped image remains visible client-side
      $("selectionImageOverlay").classList.remove("hidden");
      
      // 3. Sync button UI states instantly
      if ($("previewMockupButton")) $("previewMockupButton").textContent = "Edit Template";
      if ($("toolbarPreviewButton")) $("toolbarPreviewButton").classList.add("active");
      syncActionRail();
      setStatus("High-fidelity mockup preview active");

      // 4. Show download buttons as "Generating..." (Disabled / Loading)
      if ($("downloadMockupButton")) $("downloadMockupButton").classList.remove("hidden");
      if ($("toolbarDownloadButton")) $("toolbarDownloadButton").classList.remove("hidden");
      clearDownloadTarget("Generating high-fidelity download...");

      // Lock input interaction and show custom realistic preview loading state
      setBusy(true);
      if ($("analysisLabel")) $("analysisLabel").textContent = "Generating realistic mockup";
      if ($("analysisSub")) $("analysisSub").textContent = "Optimizing boundary geometry and applying realism filters...";

      // 5. Trigger background high-fidelity rendering (asynchronous, non-blocking!)
      (async () => {
        try {
          // Auto-save coordinates to DB
          await saveTemplate(false, true);

          const file = dataURLtoFile(overlayImage, state.selectionStyle.overlayImageName || "artwork.png");
          const formData = new FormData();
          formData.append("mode", "simple");
          formData.append("template_id", state.selected.template_id);
          formData.append("artwork", file);
          formData.append("realism", "true");
          formData.append("preview", "true");

          let resolvedFitMode = state.selected.fit_mode;
          if (resolvedFitMode === "auto") {
            resolvedFitMode = resolveFitMode(
              "auto",
              state.selectionStyle.overlayImageWidth,
              state.selectionStyle.overlayImageHeight,
              state.selected.artwork_area.width,
              state.selected.artwork_area.height
            );
          }
          formData.append("fit_mode", resolvedFitMode);

          const response = await fetch("/api/mockups/render", {
            method: "POST",
            headers: csrfHeaders(),
            body: formData
          });

          const data = await response.json();
          if (!response.ok) throw new Error(data.error || "Rendering failed");

          // Ensure user is still in preview mode before applying the background render
          if (state.isPreviewingMockup) {
            // Show rendered high-fidelity mockup and hide the client-side warped div
            if ($("selectionRenderedMockup")) {
              $("selectionRenderedMockup").src = data.output_url;
              $("selectionRenderedMockup").classList.remove("hidden");
            }
            $("selectionImageOverlay").classList.add("hidden");

            setDownloadTarget(data.output_url);
          }
        } catch (err) {
          console.error("Background render failed:", err);
          toast(`Could not render this mockup: ${err.message || err}`);
          // The render is what the download is: with nothing to hand over, the
          // button stays disabled. Enabling it with an empty href downloaded
          // the page itself, saved under an image's name.
          clearDownloadTarget("The mockup could not be rendered");
        } finally {
          // Unlock the UI and restore default detection panel labels
          setBusy(false);
          if ($("analysisLabel")) $("analysisLabel").textContent = "Detection is analyzing the frame";
          if ($("analysisSub")) $("analysisSub").textContent = "This can take several seconds.";
        }
      })();
    }
  }

  // The sidebar sits at the left edge as a rail and slides open under the
  // pointer, over the page rather than pushing it -- opening a drawer should
  // not reflow the canvas. Locking it pins it open: it takes its column back
  // and stops sliding.
  // Short: while the drawer is open it covers the left of the page, so the
  // grace period on the way out is only long enough to survive a wobble on the
  // boundary -- not long enough to swallow a click meant for what is behind it.
  const SIDEBAR_SLIDE_AWAY_MS = 140;

  let sidebarLocked = false;
  let sidebarCloseTimer = null;

  function sidebarElements() {
    return {
      sidebar: document.querySelector(".sidebar"),
      collapseToggle: $("sidebarCollapseToggle"),
      lockToggle: $("sidebarLockToggle"),
    };
  }

  function openSidebar() {
    const { sidebar, collapseToggle } = sidebarElements();
    if (!sidebar) return;
    window.clearTimeout(sidebarCloseTimer);
    sidebar.classList.remove("is-narrow");
    if (collapseToggle) collapseToggle.setAttribute("aria-expanded", "true");
  }

  function closeSidebar({ immediate = false } = {}) {
    const { sidebar, collapseToggle } = sidebarElements();
    if (!sidebar || sidebarLocked) return;
    window.clearTimeout(sidebarCloseTimer);
    const shut = () => {
      sidebar.classList.add("is-narrow");
      if (collapseToggle) collapseToggle.setAttribute("aria-expanded", "false");
    };
    if (immediate) shut();
    // A moment's grace on the way out, so crossing a corner of the sidebar
    // does not slam it shut under the hand.
    else sidebarCloseTimer = window.setTimeout(shut, SIDEBAR_SLIDE_AWAY_MS);
  }

  function applySidebarLock({ persist = true } = {}) {
    const { sidebar, collapseToggle, lockToggle } = sidebarElements();
    if (!sidebar) return;
    document.body.classList.toggle("sidebar-auto", !sidebarLocked);
    if (sidebarLocked) {
      window.clearTimeout(sidebarCloseTimer);
      sidebar.classList.remove("is-narrow");
    } else {
      sidebar.classList.add("is-narrow");
    }
    if (collapseToggle) {
      collapseToggle.disabled = sidebarLocked;
      collapseToggle.title = sidebarLocked ? "The sidebar is locked open" : "Slide the sidebar shut";
      collapseToggle.setAttribute("aria-label", collapseToggle.title);
      collapseToggle.setAttribute("aria-expanded", String(!sidebar.classList.contains("is-narrow")));
    }
    if (lockToggle) {
      lockToggle.setAttribute("aria-pressed", String(sidebarLocked));
      lockToggle.title = sidebarLocked ? "Let the sidebar slide again" : "Lock the sidebar open";
      lockToggle.setAttribute("aria-label", lockToggle.title);
      const open = lockToggle.querySelector(".lock-icon-open");
      const closed = lockToggle.querySelector(".lock-icon-closed");
      if (open) open.classList.toggle("hidden", sidebarLocked);
      if (closed) closed.classList.toggle("hidden", !sidebarLocked);
    }
    if (persist) {
      try {
        writeBoolean(KEYS.sidebarLocked, sidebarLocked);
      } catch (_error) {
        // Remembering the lock is a convenience, not a requirement.
      }
    }
    // Only the lock changes how much room is left for the canvas; sliding
    // happens over the page and leaves the workspace alone.
    requestAnimationFrame(() => {
      if (state.selected) drawSelection();
    });
  }

  (() => {
    const { sidebar, collapseToggle, lockToggle } = sidebarElements();
    if (!sidebar) return;

    sidebar.addEventListener("pointerenter", () => {
      if (!sidebarLocked) openSidebar();
    });
    sidebar.addEventListener("pointerleave", () => closeSidebar());
    sidebar.addEventListener("focusin", () => {
      if (!sidebarLocked) openSidebar();
    });
    sidebar.addEventListener("focusout", (event) => {
      if (!sidebar.contains(event.relatedTarget)) closeSidebar();
    });

    if (collapseToggle) {
      collapseToggle.addEventListener("click", () => {
        if (sidebarLocked) return;
        closeSidebar({ immediate: true });
      });
    }

    if (lockToggle) {
      lockToggle.addEventListener("click", () => {
        sidebarLocked = !sidebarLocked;
        applySidebarLock();
      });
    }

    try {
      sidebarLocked = readBoolean(KEYS.sidebarLocked);
    } catch (_error) {
      // Unlocked by default.
    }
    applySidebarLock({ persist: false });
  })();

  // The queue has two densities: the full list, and a wall of thumbnails for
  // picking a mockup by its picture alone. The choice is remembered.

  // The list reserves a gutter for its scrollbar, and how wide that gutter is
  // belongs to the browser -- ten pixels here, none at all where scrollbars
  // overlay the content. It is taken from one side only, so the head above the
  // list has to know it to put its toggle on the same centre line as the
  // thumbnails. Measuring the list itself is the only honest way to ask.
  function syncQueueGutter() {
    const queue = $("queue");
    if (!queue) return;
    const gutter = Math.max(0, queue.offsetWidth - queue.clientWidth);
    document.documentElement.style.setProperty("--scrollbar-width", `${gutter}px`);
  }

  function applyQueueDensity(compact, { persist = true } = {}) {
    const toggle = $("queueDensityToggle");
    document.body.classList.toggle("queue-compact", compact);
    if (toggle) {
      toggle.setAttribute("aria-pressed", String(compact));
      toggle.title = compact ? "Show the full list" : "Show the mockups only";
      toggle.setAttribute("aria-label", toggle.title);
    }
    if (persist) {
      try {
        writeBoolean(KEYS.queueCompact, compact);
      } catch (_error) {
        // Remembering the density is a convenience, not a requirement.
      }
    }
    // The editor is measured against the space the queue leaves it.
    requestAnimationFrame(() => {
      syncQueueGutter();
      if (state.selected) drawSelection();
    });
  }

  if ($("queueDensityToggle")) {
    $("queueDensityToggle").addEventListener("click", () => {
      applyQueueDensity(!document.body.classList.contains("queue-compact"));
    });
    let startCompact = false;
    try {
      startCompact = readBoolean(KEYS.queueCompact);
    } catch (_error) {
      // Full list by default.
    }
    applyQueueDensity(startCompact, { persist: false });
    window.addEventListener("resize", syncQueueGutter);
    syncQueueGutter();
  }

  watchSliders();

  // The engine panel needs two things from the editor; this is where it gets
  // them, once, rather than reaching across for them.
  configureEngineSettings({ exitAllDetectionModes, renderEditor });

  makeToolbarDraggable($("zoomHud"), "zoomHud");
  makeToolbarDraggable($("selectionStyleToolbar"), "selectionStyleToolbar");
  makeToolbarDraggable($("actionRail"), "actionRail");
  makeToolbarDraggable($("coordsRail"), "coordsRail");

  if ($("previewMockupButton")) $("previewMockupButton").onclick = togglePreviewMode;
  if ($("toolbarPreviewButton")) $("toolbarPreviewButton").onclick = togglePreviewMode;
  if ($("toolbarEditButton")) {
    $("toolbarEditButton").onclick = () => {
      if (state.isPreviewingMockup) togglePreviewMode();
    };
  }
  syncActionRail();

  (async () => {
    try {
      await loadSettings();
      initClassicSubmodeButtons();
    } catch (err) {
      console.warn("Settings initialization warning (continuing to load templates):", err);
      initClassicSubmodeButtons();
    }

    try {
      await loadCategories();
      // Auto-select first non-empty category on initial load
      if (state.categories.length > 0) {
        const nonEmpty = state.categories.find((c) => c.template_count > 0);
        if (nonEmpty) {
          state.selectedCategory = nonEmpty;
          renderCategories();
        }
      }
      state.queueFilter = "all";
      await loadTemplates();
      setBusy(false);
      renderTestGallery();
      renderMockupGallery();
    } catch (error) {
      console.error("Workspace initialization error:", error);
      setStatus("Unable to load workspace", true);
      toast(error.message);
    }
  })();
})();
