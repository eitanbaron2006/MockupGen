(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const SELECTION_STYLE_STORAGE_KEY = "mockupStudio.selectionStyle";
  const DEFAULT_SELECTION_STYLE = {
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

  function clampStyleNumber(value, min, max, fallback) {
    const number = Number(value);
    if (!Number.isFinite(number)) return fallback;
    return Math.min(max, Math.max(min, number));
  }

  function dataURLtoFile(dataurl, filename) {
    const arr = dataurl.split(",");
    const mime = arr[0].match(/:(.*?);/)[1];
    const bstr = atob(arr[1]);
    let n = bstr.length;
    const u8arr = new Uint8Array(n);
    while (n--) {
      u8arr[n] = bstr.charCodeAt(n);
    }
    return new File([u8arr], filename, { type: mime });
  }

  function resolveFitMode(fitMode, artworkWidth, artworkHeight, frameWidth, frameHeight) {
    if (fitMode !== "auto") return fitMode;
    if (!artworkWidth || !artworkHeight || !frameWidth || !frameHeight) {
      return "cover";
    }
    const artworkRatio = artworkWidth / artworkHeight;
    const frameRatio = frameWidth / frameHeight;
    
    // Within 3% aspect ratio difference, use stretch
    if (Math.abs(artworkRatio - frameRatio) < 0.03) {
      return "stretch";
    }
    
    const getOrientation = (ratio) => {
      if (ratio > 1.15) return "landscape";
      if (ratio < 0.85) return "portrait";
      return "square";
    };
    
    const artOrientation = getOrientation(artworkRatio);
    const frameOrientation = getOrientation(frameRatio);
    
    if (artOrientation === frameOrientation) {
      return "cover";
    } else {
      return "stretch";
    }
  }

  function getMatrix3d(w, h, p0, p1, p2, p3) {
    const x0 = p0.x, y0 = p0.y;
    const x1 = p1.x, y1 = p1.y;
    const x2 = p2.x, y2 = p2.y;
    const x3 = p3.x, y3 = p3.y;

    const dx1 = x1 - x2;
    const dx2 = x3 - x2;
    const dy1 = y1 - y2;
    const dy2 = y3 - y2;
    const dx3 = x0 - x1 + x2 - x3;
    const dy3 = y0 - y1 + y2 - y3;

    let a, b, c, d, e, f, g, h_coeff;

    const det = dx1 * dy2 - dx2 * dy1;
    if (Math.abs(det) < 1e-9) {
      a = x1 - x0;
      b = x3 - x0;
      c = x0;
      d = y1 - y0;
      e = y3 - y0;
      f = y0;
      g = 0;
      h_coeff = 0;
    } else {
      g = (dx3 * dy2 - dx2 * dy3) / det;
      h_coeff = (dx1 * dy3 - dx3 * dy1) / det;
      a = x1 - x0 + g * x1;
      b = x3 - x0 + h_coeff * x3;
      c = x0;
      d = y1 - y0 + g * y1;
      e = y3 - y0 + h_coeff * y3;
      f = y0;
    }

    const a_prime = a / w;
    const b_prime = b / h;
    const c_prime = c;
    const d_prime = d / w;
    const e_prime = e / h;
    const f_prime = f;
    const g_prime = g / w;
    const h_prime = h_coeff / h;

    return [
      a_prime, d_prime, 0, g_prime,
      b_prime, e_prime, 0, h_prime,
      0,       0,       1, 0,
      c_prime, f_prime, 0, 1
    ];
  }

  function loadSelectionStylePreference() {
    try {
      const saved = JSON.parse(localStorage.getItem(SELECTION_STYLE_STORAGE_KEY) || "{}");
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

  const state = {
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
    panStart: { x: 0, y: 0 },
    spacePressed: false,
    lastSelectedTemplateId: null,
    isPreviewingMockup: false,
    globalOverlayPlacementActive: false,
    globalOverlayDrag: null
  };
  const wizardState = {
    active: false,
    step: 1,
    layers: [],
    layerIndex: 0,
    proposedCorners: null,
    clickListener: null
  };
  const testState = {
    files: [],
    activeIndex: -1,
    templates: [],
    selectedTemplates: new Set()
  };
  const $ = (id) => document.getElementById(id);
  const DEFAULT_EFFECTS = {
    inner_shadow: { enabled: false, top: 10, right: 10, bottom: 10, left: 10, opacity: 0.4, blur: 15, target: "artwork" },
    glass_reflection: { enabled: false, type: "diagonal", opacity: 0.15, target: "artwork" },
    matte_finish: { enabled: false, shadow_lift: 0.08, contrast: -0.15, target: "artwork" },
    color_tint: { enabled: false, temperature: 25, intensity: 0.2, target: "artwork" },
    gobo_shadow: { enabled: false, opacity: 0.3, scale: 1.0, target: "artwork" },
    photoshop_adjustments: { enabled: false, brightness: 0.0, contrast: 0.0, saturation: 0.0, color_filter: "none", target: "all" },
    global_png_overlay: {
      enabled: false,
      image: "",
      opacity: 0.5,
      scale: 1,
      position_x: 0,
      position_y: 0,
      rotation: 0,
      anchor: "center",
      blend_mode: "normal",
      tint_color: "#ffffff",
      tint_strength: 0,
      blur: 0,
      flip_x: false,
      flip_y: false,
      repeat: false,
      target: "all"
    },
    global_reflections: { enabled: false, window_type: "none", window_opacity: 0.2, window_blur: 20.0, rays_type: "none", rays_opacity: 0.2, rays_angle: 0.0, target: "all" }
  };
  const EFFECT_DOM = {
    inner_shadow: {
      enabledId: "innerShadowEnabled",
      controlsId: "innerShadowControls",
      label: "Inner Frame Shadow",
      fields: [
        { id: "shadowOpacity", valueId: "shadowOpacityVal", prop: "opacity", type: "number", format: "percent" },
        { id: "shadowBlur", valueId: "shadowBlurVal", prop: "blur", type: "number", suffix: "px" },
        { id: "shadowTop", valueId: "shadowTopVal", prop: "top", type: "number", suffix: "px" },
        { id: "shadowBottom", valueId: "shadowBottomVal", prop: "bottom", type: "number", suffix: "px" },
        { id: "shadowLeft", valueId: "shadowLeftVal", prop: "left", type: "number", suffix: "px" },
        { id: "shadowRight", valueId: "shadowRightVal", prop: "right", type: "number", suffix: "px" }
      ]
    },
    glass_reflection: {
      enabledId: "glassReflectionEnabled",
      controlsId: "glassReflectionControls",
      label: "Glass Reflection Cover",
      fields: [
        { id: "reflectionType", prop: "type", type: "string" },
        { id: "reflectionOpacity", valueId: "reflectionOpacityVal", prop: "opacity", type: "number", format: "percent" }
      ]
    },
    matte_finish: {
      enabledId: "matteFinishEnabled",
      controlsId: "matteFinishControls",
      label: "Faded Matte Paper",
      fields: [
        { id: "matteShadowLift", valueId: "matteShadowLiftVal", prop: "shadow_lift", type: "number", format: "percent" },
        { id: "matteContrast", valueId: "matteContrastVal", prop: "contrast", type: "number", format: "signedPercent" }
      ]
    },
    color_tint: {
      enabledId: "colorTintEnabled",
      controlsId: "colorTintControls",
      label: "Ambient Light Warmth",
      fields: [
        { id: "tintTemperature", valueId: "tintTemperatureVal", prop: "temperature", type: "number", format: "signedNumber" },
        { id: "tintIntensity", valueId: "tintIntensityVal", prop: "intensity", type: "number", format: "percent" }
      ]
    },
    gobo_shadow: {
      enabledId: "goboShadowEnabled",
      controlsId: "goboShadowControls",
      label: "Sunlight Blinds Shadow",
      fields: [
        { id: "goboOpacity", valueId: "goboOpacityVal", prop: "opacity", type: "number", format: "percent" },
        { id: "goboScale", valueId: "goboScaleVal", prop: "scale", type: "number", format: "scale" }
      ]
    },
    photoshop_adjustments: {
      enabledId: "photoshopAdjustmentsEnabled",
      controlsId: "photoshopAdjustmentsControls",
      label: "Photoshop Color Filters",
      fields: [
        { id: "photoshopColorFilter", prop: "color_filter", type: "string" },
        { id: "photoshopBrightness", valueId: "photoshopBrightnessVal", prop: "brightness", type: "number", format: "signedPercent" },
        { id: "photoshopContrast", valueId: "photoshopContrastVal", prop: "contrast", type: "number", format: "signedPercent" },
        { id: "photoshopSaturation", valueId: "photoshopSaturationVal", prop: "saturation", type: "number", format: "signedPercent" }
      ]
    },
    global_reflections: {
      enabledId: "globalReflectionsEnabled",
      controlsId: "globalReflectionsControls",
      label: "Global Scene Reflections & Rays",
      fields: [
        { id: "globalWindowType", prop: "window_type", type: "string" },
        { id: "globalWindowOpacity", valueId: "globalWindowOpacityVal", prop: "window_opacity", type: "number", format: "percent" },
        { id: "globalWindowBlur", valueId: "globalWindowBlurVal", prop: "window_blur", type: "number", suffix: "px" },
        { id: "globalRaysType", prop: "rays_type", type: "string" },
        { id: "globalRaysOpacity", valueId: "globalRaysOpacityVal", prop: "rays_opacity", type: "number", format: "percent" },
        { id: "globalRaysAngle", valueId: "globalRaysAngleVal", prop: "rays_angle", type: "number", suffix: "°" }
      ]
    },
    global_png_overlay: {
      enabledId: "globalPngOverlayEnabled",
      controlsId: "globalPngOverlayControls",
      label: "Global Custom PNG Overlay",
      fields: [
        { id: "globalOverlayOpacity", valueId: "globalOverlayOpacityVal", prop: "opacity", type: "number", format: "percent" },
        { id: "globalOverlayScale", valueId: "globalOverlayScaleVal", prop: "scale", type: "number", format: "percent" },
        { id: "globalOverlayPositionX", valueId: "globalOverlayPositionXVal", prop: "position_x", type: "number", format: "signedPercent" },
        { id: "globalOverlayPositionY", valueId: "globalOverlayPositionYVal", prop: "position_y", type: "number", format: "signedPercent" },
        { id: "globalOverlayRotation", valueId: "globalOverlayRotationVal", prop: "rotation", type: "number", suffix: "°" },
        { id: "globalOverlayAnchor", prop: "anchor", type: "string" },
        { id: "globalOverlayBlendMode", prop: "blend_mode", type: "string" },
        { id: "globalOverlayTintColor", prop: "tint_color", type: "string" },
        { id: "globalOverlayTintStrength", valueId: "globalOverlayTintStrengthVal", prop: "tint_strength", type: "number", format: "percent" },
        { id: "globalOverlayBlur", valueId: "globalOverlayBlurVal", prop: "blur", type: "number", suffix: "px" },
        { id: "globalOverlayFlipX", prop: "flip_x", type: "boolean" },
        { id: "globalOverlayFlipY", prop: "flip_y", type: "boolean" },
        { id: "globalOverlayRepeat", prop: "repeat", type: "boolean" }
      ]
    }
  };

  function cloneObject(value) {
    return JSON.parse(JSON.stringify(value));
  }

  function defaultEffects() {
    return cloneObject(DEFAULT_EFFECTS);
  }

  const DEFAULT_GREEN_FRAME_SETTINGS = {
    use_perspective: true,
    use_vector_clip: true,
    fit_mode: "cover",
    artwork_scale: 1,
    offset_x: 0,
    offset_y: 0,
    edge_expand: 0,
    mask_build_quality: 2,
    feather_radius: 2,
    edge_aa_radius: 0,
    aa_scale: 1,
    enable_inner_shadow: false,
    inner_shadow_strength: 0.35,
    inner_shadow_size: 10,
    contain_bg_color: "#ffffff"
  };

  function isGreenFrameTemplate(template = state.selected) {
    return Boolean(
      template
      && template.raw_artwork_area
      && template.raw_artwork_area.mode === "green_frames_mockups"
      && (template.mask_name === "mask.png" || template.mask === "mask.png")
    );
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
    setGreenFrameLabel("greenMaskBuildQualityVal", $("greenMaskBuildQuality").value, "x");
    setGreenFrameLabel("greenFeatherRadiusVal", $("greenFeatherRadius").value, "px");
    setGreenFrameLabel("greenEdgeAARadiusVal", $("greenEdgeAARadius").value, "px");
    setGreenFrameLabel("greenAAScaleVal", $("greenAAScale").value, "x");
    setGreenFrameLabel("greenInnerShadowStrengthVal", $("greenInnerShadowStrength").value, "%");
    setGreenFrameLabel("greenInnerShadowSizeVal", $("greenInnerShadowSize").value, "px");
  }

  function populateGreenFrameControls(template, effects) {
    const panel = $("greenFramePanel");
    if (!panel) return;
    panel.classList.toggle("hidden", !isGreenFrameTemplate(template));
    const settings = greenFrameSettings(effects);
    $("greenUsePerspective").checked = settings.use_perspective;
    $("greenUseVectorClip").checked = settings.use_vector_clip;
    $("greenFitMode").value = settings.fit_mode;
    $("greenArtworkScale").value = Math.round(Number(settings.artwork_scale || 1) * 100);
    $("greenOffsetX").value = Math.round(Number(settings.offset_x || 0) * 100);
    $("greenOffsetY").value = Math.round(Number(settings.offset_y || 0) * 100);
    $("greenEdgeExpand").value = settings.edge_expand;
    $("greenMaskBuildQuality").value = settings.mask_build_quality;
    $("greenFeatherRadius").value = settings.feather_radius;
    $("greenEdgeAARadius").value = settings.edge_aa_radius;
    $("greenAAScale").value = settings.aa_scale;
    $("greenInnerShadowEnabled").checked = settings.enable_inner_shadow;
    $("greenInnerShadowStrength").value = Math.round(Number(settings.inner_shadow_strength || 0) * 100);
    $("greenInnerShadowSize").value = settings.inner_shadow_size;
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
      mask_build_quality: Number($("greenMaskBuildQuality").value),
      feather_radius: Number($("greenFeatherRadius").value),
      edge_aa_radius: Number($("greenEdgeAARadius").value),
      aa_scale: Number($("greenAAScale").value),
      enable_inner_shadow: $("greenInnerShadowEnabled").checked,
      inner_shadow_strength: Number($("greenInnerShadowStrength").value) / 100,
      inner_shadow_size: Number($("greenInnerShadowSize").value),
      contain_bg_color: $("greenContainBgColor").value || "#ffffff"
    };
  }

  function effectInstances(effects, key) {
    const value = effects && effects[key];
    if (Array.isArray(value)) {
      return value.filter((item) => item && typeof item === "object").slice(0, 2);
    }
    if (value && typeof value === "object") return [value];
    return [cloneObject(DEFAULT_EFFECTS[key])];
  }

  function primaryEffect(effects, key) {
    const instances = effectInstances(effects, key);
    return { ...cloneObject(DEFAULT_EFFECTS[key]), ...(instances[0] || {}) };
  }

  function setEffectValueLabel(field, value, root = document) {
    if (!field.valueId) return;
    const element = root.querySelector(`#${CSS.escape(field.valueId)}`) || root.querySelector(`[data-original-id="${field.valueId}"]`);
    if (!element) return;
    const number = Number(value);
    if (field.format === "percent") {
      element.textContent = Math.round(number * 100) + "%";
    } else if (field.format === "signedPercent") {
      const percent = Math.round(number * 100);
      element.textContent = (percent >= 0 ? "+" : "") + percent + "%";
    } else if (field.format === "signedNumber") {
      element.textContent = (number > 0 ? "+" : "") + number;
    } else if (field.format === "scale") {
      element.textContent = number.toFixed(1) + "x";
    } else {
      element.textContent = value + (field.suffix || "");
    }
  }

  function getFieldElement(root, id) {
    return root.querySelector(`#${CSS.escape(id)}`) || root.querySelector(`[data-original-id="${id}"]`);
  }

  function setEffectInstanceValues(root, key, config) {
    const def = EFFECT_DOM[key];
    const enabled = getFieldElement(root, def.enabledId);
    const controls = getFieldElement(root, def.controlsId);
    if (enabled) enabled.checked = Boolean(config.enabled);
    if (root.dataset.effectInstance === "2" || !root.dataset.effectCollapsed) {
      root.dataset.effectCollapsed = Boolean(!config.enabled).toString();
    }
    updateEffectPanelCollapsed(root);
    def.fields.forEach((field) => {
      const element = getFieldElement(root, field.id);
      if (!element) return;
      const value = config[field.prop] ?? DEFAULT_EFFECTS[key][field.prop];
      if (field.type === "boolean") element.checked = Boolean(value);
      else element.value = value;
      setEffectValueLabel(field, value, root);
    });
    const target = config.target || DEFAULT_EFFECTS[key].target;
    root.querySelectorAll(".segmented-control[data-effect-key] .segment-btn").forEach((btn) => {
      btn.classList.toggle("active", btn.getAttribute("data-target-val") === target);
    });
    if (key === "global_png_overlay") {
      root.dataset.overlayImage = config.image || "";
      const name = getFieldElement(root, "globalOverlayName");
      if (name) {
        name.textContent = config.image ? "Overlay loaded" : "No file";
        if (config.image) {
          name.setAttribute("title", "PNG Overlay base64 encoded");
        } else {
          name.removeAttribute("title");
        }
      }
    }
  }

  function readEffectInstanceValues(root, key) {
    const def = EFFECT_DOM[key];
    const config = cloneObject(DEFAULT_EFFECTS[key]);
    const enabled = getFieldElement(root, def.enabledId);
    if (enabled) config.enabled = enabled.checked;
    def.fields.forEach((field) => {
      const element = getFieldElement(root, field.id);
      if (!element) return;
      if (field.type === "number") config[field.prop] = Number(element.value);
      else if (field.type === "boolean") config[field.prop] = element.checked;
      else config[field.prop] = element.value;
    });
    const activeTarget = root.querySelector(".segmented-control[data-effect-key] .segment-btn.active");
    if (activeTarget) config.target = activeTarget.getAttribute("data-target-val");
    if (key === "global_png_overlay") {
      config.image = root.dataset.overlayImage || "";
    }
    return config;
  }

  function effectGroupForKey(key, instance = "1") {
    return document.querySelector(`.effect-group[data-effect-key="${key}"][data-effect-instance="${instance}"]`);
  }

  function syncEffectAddButton(key) {
    const group = effectGroupForKey(key, "1");
    if (!group) return;
    const button = group.querySelector(".effect-add-instance");
    if (button) button.classList.toggle("hidden", Boolean(effectGroupForKey(key, "2")));
  }

  function updateEffectPanelCollapsed(group) {
    const controls = group && getFieldElement(group, EFFECT_DOM[group.dataset.effectKey]?.controlsId || "");
    const collapsed = group?.dataset.effectCollapsed === "true";
    if (controls) controls.classList.toggle("hidden", collapsed);
  }

  function toggleEffectPanelCollapsed(group) {
    if (!group) return;
    group.dataset.effectCollapsed = String(group.dataset.effectCollapsed !== "true");
    updateEffectPanelCollapsed(group);
  }

  function prepareEffectGroupControls(group, key, instance) {
    group.dataset.effectKey = key;
    group.dataset.effectInstance = instance;
    group.querySelectorAll("[id]").forEach((element) => {
      const originalId = element.dataset.originalId || element.id;
      element.dataset.originalId = originalId;
      if (instance === "2") element.id = `${originalId}Instance2`;
    });
    group.querySelectorAll("label[for]").forEach((label) => {
      const originalFor = label.dataset.originalFor || label.getAttribute("for");
      label.dataset.originalFor = originalFor;
      if (instance === "2") label.setAttribute("for", `${originalFor}Instance2`);
    });
    group.querySelectorAll(".segmented-control[data-effect-key]").forEach((ctrl) => {
      ctrl.setAttribute("data-effect-key", key);
    });
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

  async function api(url, options = {}) {
    const headers = { ...(options.headers || {}), "X-CSRF-Token": csrf };
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(url, { ...options, headers });
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = { error: "The server returned an unreadable response." };
    }
    if (response.status === 401) window.location.href = "/admin/login";
    if (!response.ok) throw new Error(payload.error || "Request failed");
    return payload;
  }

  function escapeHtml(value) {
    const element = document.createElement("span");
    element.textContent = String(value || "");
    return element.innerHTML;
  }

  let toastTimer;
  function toast(message) {
    $("toast").textContent = message;
    $("toast").classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => $("toast").classList.remove("show"), 3200);
  }

  function setStatus(message, failure = false) {
    $("status").textContent = message;
    $("status").style.color = failure ? "var(--accent)" : "var(--success)";
  }

  function statusClass(template) {
    return template.status === "active" ? "approved" : "review";
  }

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
      <button class="category ${state.selectedCategory && state.selectedCategory.id === category.id ? "selected" : ""}" data-category="${category.id}">
        <span>${escapeHtml(category.name)}</span><span class="count">${category.template_count}</span>
      </button>
    `).join("") || '<div class="empty">Create a product category to begin.</div>';
    $("breadcrumb").textContent = state.selectedCategory ? state.selectedCategory.name : "Select category";
    document.querySelectorAll(".category").forEach((button) => {
      button.onclick = async () => {
        if (state.busy) return;
        autoSaveCurrent();
        state.selectedCategory = state.categories.find((category) => category.id === Number(button.dataset.category));
        state.queueFilter = "all";
        renderCategories();
        await loadTemplates();
      };
    });
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
    const desiredId = preferredTemplateId || (state.selected && state.selected.template_id);
    state.selected =
      state.templates.find((template) => template.template_id === desiredId) ||
      state.templates[0] ||
      null;
    renderQueue();
    renderEditor();
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
        <button class="queue-select" type="button" data-template="${template.template_id}" ${state.busy ? "disabled" : ""}>
          <img class="thumb" src="/api/admin/templates/${template.template_id}/asset/preview.png" alt="">
          <span>
            <span class="file-title">${escapeHtml(template.name)}</span>
            <span class="meta">${escapeHtml(template.orientation)} <span class="pill ${statusClass(template)}">${template.status === "active" ? "Approved" : "Review"}</span></span>
          </span>
        </button>
        <button class="queue-delete" type="button" data-template="${template.template_id}" aria-label="Delete ${escapeHtml(template.name)}" title="Delete mockup" ${state.busy ? "disabled" : ""}>
          <svg class="trash-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="width: 14px; height: 14px;"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path><line x1="10" y1="11" x2="10" y2="17"></line><line x1="14" y1="11" x2="14" y2="17"></line></svg>
        </button>
      </div>
    `).join("") || '<div class="empty">No templates match this filter.</div>';
    document.querySelectorAll(".queue-select").forEach((button) => {
      button.onclick = () => {
        autoSaveCurrent();
        state.selected = state.templates.find((template) => template.template_id === button.dataset.template);
        renderQueue();
        renderEditor();
      };
    });
    document.querySelectorAll(".queue-checkbox").forEach((box) => {
      box.onchange = (e) => {
        if (e.target.checked) {
          state.selectedForBatch.add(e.target.dataset.template);
        } else {
          state.selectedForBatch.delete(e.target.dataset.template);
        }
        updateBatchControls();
      };
    });
    document.querySelectorAll(".queue-delete").forEach((button) => {
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
          alert(`Batch detection finished, but ${errorCount} template(s) failed to detect properly.`);
        }
      }
    } catch (error) {
      alert("Batch detection failed: " + error.message);
    } finally {
      state.busy = false;
      renderQueue();
      renderEditor();
    }
  };

  $("batchDeleteButton").onclick = async () => {
    if (state.selectedForBatch.size === 0 || state.busy) return;
    const ids = Array.from(state.selectedForBatch);
    const confirmed = window.confirm(`Delete ${ids.length} selected mockup${ids.length === 1 ? "" : "s"}?`);
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

  function confidenceLabel(value) {
    return value == null ? "" : `Confidence ${Math.round(value * 100)}%`;
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
      $("currentTitle").textContent = "Select a mockup";
      $("confidence").textContent = "";
      $("coordX").textContent = "-";
      $("coordY").textContent = "-";
      $("coordW").textContent = "-";
      $("coordH").textContent = "-";
      $("zoomHud").classList.add("hidden");
      $("selectionStyleToolbar").classList.add("hidden");
      closeSelectionStylePanel();
      return;
    }

    // Reset preview mode on template switch/re-render
    state.isPreviewingMockup = false;
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
    if ($("toolbarDownloadButton")) {
      $("toolbarDownloadButton").classList.add("hidden");
    }

    // Reset zoom and pan if template has changed
    if (state.lastSelectedTemplateId !== template.template_id) {
      state.zoom = 1;
      state.pan = { x: 0, y: 0 };
      state.lastSelectedTemplateId = template.template_id;
      applyZoomPan();
    }
    $("zoomHud").classList.remove("hidden");
    $("selectionStyleToolbar").classList.remove("hidden");

    $("currentTitle").textContent = template.name;
    $("editorSub").textContent = template.status === "active"
      ? "Published template. Detection changes remain proposals until approved again."
      : "Draft template. Review the proposal before approval.";
    $("inspectorStatus").textContent = template.status === "active" ? "Approved template" : "Awaiting approval";
    $("proposalState").textContent = template.status === "active"
      ? "Approved rectangle is active. Run Detect frame to compare safely."
      : "Detect frame or adjust the artwork area before approval.";
    $("selectionSvg").classList.add("hidden");
    $("canvasImage").onload = () => {
      requestAnimationFrame(drawSelection);
    };
    $("canvasImage").src = `/api/admin/templates/${template.template_id}/asset/background.png`;
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
        state.isPreviewingMockup = false;
        if ($("selectionRenderedMockup")) $("selectionRenderedMockup").classList.add("hidden");
        $("selectionSvg").classList.add("hidden");
        $("selectionImageOverlay").classList.add("hidden");
      }
    }
    renderGlobalOverlayPlacement();
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

  function saveSelectionStylePreference() {
    try {
      localStorage.setItem(SELECTION_STYLE_STORAGE_KEY, JSON.stringify(state.selectionStyle));
    } catch (_error) {
      // Style preferences are non-critical UI state.
    }
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

  function drawSelection() {
    if (state.globalOverlayPlacementActive) {
      $("selectionSvg").classList.add("hidden");
      $("selectionImageOverlay").classList.add("hidden");
      renderGlobalOverlayPlacement();
      return;
    }
    if (state.isPreviewingMockup) {
      $("selectionSvg").classList.add("hidden");
      $("selectionImageOverlay").classList.add("hidden");
      renderGlobalOverlayPlacement();
      return;
    }
    const template = state.selected;
    const image = $("canvasImage");
    const selectionSvg = $("selectionSvg");
    if (!template || !template.artwork_area || !image.naturalWidth) {
      selectionSvg.classList.add("hidden");
      return;
    }

    if (isGreenFrameTemplate(template)) {
      selectionSvg.classList.add("hidden");
      $("selectionImageOverlay").classList.add("hidden");
      if (state.selectionStyle.overlayImage) {
        if (greenFramePreviewTimeout) clearTimeout(greenFramePreviewTimeout);
        greenFramePreviewTimeout = setTimeout(() => {
          refreshGreenFrameMockupPreview();
        }, 120);
      } else if ($("selectionRenderedMockup")) {
        $("selectionRenderedMockup").classList.add("hidden");
        $("selectionRenderedMockup").src = "";
      }
      return;
    }

    if ($("selectionRenderedMockup")) {
      $("selectionRenderedMockup").classList.add("hidden");
    }

    // Ensure template.artwork_area has corners. If not, generate them on the fly!
    if (!template.artwork_area.corners) {
      const area = template.artwork_area;
      template.artwork_area.corners = [
        { x: area.x, y: area.y },
        { x: area.x + area.width, y: area.y },
        { x: area.x + area.width, y: area.y + area.height },
        { x: area.x, y: area.y + area.height }
      ];
    }

    const corners = template.artwork_area.corners;
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

    // Inject the current zoom factor as a CSS custom property for non-scaling stroke effect calculations
    selectionSvg.style.setProperty("--zoom", state.zoom);
    applySelectionStyle();

    // Map canvas coordinates to client display coordinates inside the rendered rect
    const displayPoints = corners.map(p => {
      const cx = (p.x / template.canvas_width) * rect.width;
      const cy = (p.y / template.canvas_height) * rect.height;
      return { x: cx, y: cy };
    });
    const pointsStr = displayPoints.map((p) => `${p.x},${p.y}`).join(" ");

    $("selectionPolygon").setAttribute("points", pointsStr);

    const overlayDiv = $("selectionImageOverlay");
    const overlayImg = $("selectionOverlayImg");
    if (overlayDiv && overlayImg) {
      const style = state.selectionStyle;
      const showOverlay = style.overlayMode === "image" && Boolean(style.overlayImage);
      overlayDiv.classList.toggle("hidden", !showOverlay);

      if (!showOverlay) {
        overlayImg.src = "";
      }

      if (showOverlay && displayPoints.length >= 4) {
        const p0 = displayPoints[0];
        const p1 = displayPoints[1];
        const p2 = displayPoints[2];
        const p3 = displayPoints[3];

        const canvasW = template.artwork_area.width;
        const canvasH = template.artwork_area.height;
        overlayDiv.style.width = `${canvasW}px`;
        overlayDiv.style.height = `${canvasH}px`;
        overlayDiv.style.left = `${rect.left}px`;
        overlayDiv.style.top = `${rect.top}px`;

        const matrix = getMatrix3d(canvasW, canvasH, p0, p1, p2, p3);
        overlayDiv.style.transform = `matrix3d(${matrix.join(",")})`;

        overlayImg.src = style.overlayImage;

        let resolvedFitMode = template.fit_mode;
        if (resolvedFitMode === "auto") {
          resolvedFitMode = resolveFitMode(
            "auto",
            state.selectionStyle.overlayImageWidth,
            state.selectionStyle.overlayImageHeight,
            canvasW,
            canvasH
          );
        }

        if (resolvedFitMode === "contain") {
          overlayImg.style.objectFit = "contain";
        } else if (resolvedFitMode === "cover") {
          overlayImg.style.objectFit = "cover";
        } else {
          overlayImg.style.objectFit = "fill"; // stretch
        }
      }
    }

    // Position handles (crosshairs)
    corners.forEach((p, idx) => {
      const cx = (p.x / template.canvas_width) * rect.width;
      const cy = (p.y / template.canvas_height) * rect.height;
      const handle = $(`handle_${idx}`);
      if (handle) {
        handle.setAttribute("cx", cx);
        handle.setAttribute("cy", cy);
        handle.setAttribute("r", 14 / state.zoom); // Dynamic interactive hitbox radius
      }

      // Draw crosshair lines centered at (cx, cy)
      const hLine = $(`h_line_${idx}`);
      const vLine = $(`v_line_${idx}`);
      if (hLine && vLine) {
        const halfSize = 12 / state.zoom; // Crosshair size on screen will always be 24px

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

    // Map raw_artwork_area canvas coordinates to client display coordinates inside the rendered rect
    const rawPolygon = $("rawSelectionPolygon");
    const rawTag = $("svgRawZoneTag");
    if (template.raw_artwork_area && rawPolygon) {
      if (!template.raw_artwork_area.corners) {
        const area = template.raw_artwork_area;
        template.raw_artwork_area.corners = [
          { x: area.x, y: area.y },
          { x: area.x + area.width, y: area.y },
          { x: area.x + area.width, y: area.y + area.height },
          { x: area.x, y: area.y + area.height }
        ];
      }
      const rawCorners = template.raw_artwork_area.corners;
      const rawPointsStr = rawCorners.map(p => {
        const cx = (p.x / template.canvas_width) * rect.width;
        const cy = (p.y / template.canvas_height) * rect.height;
        return `${cx},${cy}`;
      }).join(" ");
      rawPolygon.setAttribute("points", rawPointsStr);
      rawPolygon.classList.remove("hidden");

      if (rawTag && rawCorners.length > 0) {
        const tRawX = (rawCorners[0].x / template.canvas_width) * rect.width;
        const tRawY = (rawCorners[0].y / template.canvas_height) * rect.height - 25; // slightly above the main tag
        rawTag.setAttribute("x", tRawX);
        rawTag.setAttribute("y", tRawY);
        rawTag.classList.remove("hidden");
      }

      // Position and show raw handles
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

    // Position the text tag slightly above or near the first corner
    if (corners.length > 0) {
      const tX = (corners[0].x / template.canvas_width) * rect.width;
      const tY = (corners[0].y / template.canvas_height) * rect.height - 10;
      const tag = $("svgZoneTag");
      if (tag) {
        tag.setAttribute("x", tX);
        tag.setAttribute("y", tY);
      }
    }

    selectionSvg.classList.remove("hidden");
  }

  function beginDrag(event) {
    if (!state.selected || !state.selected.artwork_area || state.busy) return;
    if (isGreenFrameTemplate()) return;
    event.preventDefault();

    const target = event.target;
    let handle = "move";
    let index = -1;

    if (target.classList.contains("svg-handle")) {
      handle = "corner";
      index = Number(target.dataset.index);
    } else if (target.id !== "selectionPolygon") {
      // Don't drag if it's clicking outside
      return;
    }

    target.setPointerCapture(event.pointerId);

    // Ensure corners are populated
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

    let nextCorners = state.drag.corners.map(c => ({ ...c }));

    if (state.drag.handle === "move") {
      // Move all corners by dx, dy, ensuring they remain inside the canvas bounds
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

    // Update active area and template details
    template.artwork_area.corners = nextCorners;

    // Update bbox x, y, width, height for backward-compatible rendering/labels
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

  async function persistTemplateState(template) {
    if (!template || !template.artwork_area || state.busy) return;
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

    // If classic detection is active, run the selected internal classic mode.
    if ((state.settings.DETECTION_PROVIDER || "classic") === "classic") {
      if ((state.settings.CLASSIC_INTERNAL_MODE || "auto") === "green_frames_mockups") {
        await runClassicGreenFramesDetection();
        return;
      }
      await startDetectionWizard();
      return;
    }

    setBusy(true);
    const engineName = providerTitle(state.settings.DETECTION_PROVIDER || "classic");
    $("analysisLabel").textContent = `${engineName} is analyzing the frame`;
    setStatus(`${engineName} is detecting the artwork area...`);
    $("proposalState").textContent = "Analyzing the selected background image...";
    $("detectionResult").className = "rule result-rule";
    try {
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, { method: "POST" });
      state.selected = payload.template;
      if (payload.proposal && payload.proposal.raw_artwork_area) {
        state.selected.raw_artwork_area = payload.proposal.raw_artwork_area;
      }
      const confidence = payload.proposal.confidence == null ? "" : ` (${Math.round(payload.proposal.confidence * 100)}%)`;
      $("confidence").textContent = confidenceLabel(payload.proposal.confidence);
      $("detectionResult").classList.add("success");
      $("detectionResult").textContent = `${payload.proposal.provider}: ${payload.proposal.reason || "Artwork area proposed."}${confidence}`;
      $("proposalState").textContent = "Detection proposal displayed. Drag handles to refine it, then approve.";
      updateTemplateInQueue(state.selected);
      renderEditor();
      toast("Detection proposal ready for review");
      setStatus("Detection proposal ready. Review before approving.");
    } catch (error) {
      $("detectionResult").classList.add("error");
      $("detectionResult").textContent = error.message;
      $("proposalState").textContent = "Detection failed. Open detection settings or retry.";
      toast(error.message);
      setStatus("Detection failed", true);
    } finally {
      setBusy(false);
    }
  }

  async function runClassicGreenFramesDetection() {
    setBusy(true);
    $("analysisLabel").textContent = "Classic green frames is analyzing the mockup";
    if ($("analysisSub")) $("analysisSub").textContent = "Building a green-screen mask and perspective corners.";
    setStatus("Detecting green frame mockup area...");
    $("proposalState").textContent = "Analyzing green frame mask...";
    $("detectionResult").className = "rule result-rule";
    try {
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, {
        method: "POST",
        body: JSON.stringify({ mode: "green_frames_mockups" })
      });
      state.selected = payload.template;
      if (payload.proposal && payload.proposal.raw_artwork_area) {
        state.selected.raw_artwork_area = payload.proposal.raw_artwork_area;
      }
      const confidence = payload.proposal.confidence == null ? "" : ` (${Math.round(payload.proposal.confidence * 100)}%)`;
      $("confidence").textContent = confidenceLabel(payload.proposal.confidence);
      $("detectionResult").classList.add("success");
      $("detectionResult").textContent = `${payload.proposal.provider}: ${payload.proposal.reason || "Green frame area proposed."}${confidence}`;
      $("proposalState").textContent = "Green frame detection proposal displayed. Drag handles to refine it, then approve.";
      updateTemplateInQueue(state.selected);
      renderEditor();
      toast("Green frame detection proposal ready");
      setStatus("Green frame proposal ready. Review before approving.");
    } catch (error) {
      $("detectionResult").classList.add("error");
      $("detectionResult").textContent = error.message;
      $("proposalState").textContent = "Green frame detection failed. Try the standard classic mode or review manually.";
      toast(error.message);
      setStatus("Green frame detection failed", true);
    } finally {
      setBusy(false);
      if ($("analysisSub")) $("analysisSub").textContent = "This can take several seconds.";
    }
  }

  // --- PREMIUM 4-STAGE GUIDED DETECTION WIZARD ---
  async function startDetectionWizard() {
    if (!state.selected) {
      toast("Select a mockup before running detection.");
      return;
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
    updateWizardUI("STAGE 1", "Automatic Geometry", "Searching for sharp nesting mockup borders in the mockup image...", []);

    try {
      // Call the detect API with mode="geometry"
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, {
        method: "POST",
        body: JSON.stringify({ mode: "geometry" })
      });

      const layers = (payload.proposal && payload.proposal.raw_artwork_area && payload.proposal.raw_artwork_area.layers) || [];
      if (layers.length > 0) {
        wizardState.layers = layers;
        wizardState.layerIndex = layers.length - 1; // Default to the innermost (smallest) layer
        showWizardLayer();
      } else {
        await runStage2SamCenter();
      }
    } catch (error) {
      console.warn("Stage 1 Geometry failed:", error);
      await runStage2SamCenter();
    }
  }

  function showWizardLayer() {
    const currentLayer = wizardState.layers[wizardState.layerIndex];
    if (!currentLayer) return;

    // Dynamically apply selected layer to state so user sees it drawn on the SVG overlay
    if (!state.selected.artwork_area) {
      state.selected.artwork_area = {};
    }
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
      { text: "Approve", class: "primary", onclick: () => approveWizardSelection() },
      {
        text: "Next", class: "secondary", onclick: () => {
          wizardState.layerIndex = (wizardState.layerIndex - 1 + wizardState.layers.length) % wizardState.layers.length;
          showWizardLayer();
        }
      },
      { text: "Skip", class: "danger", onclick: () => runStage2SamCenter() }
    ];

    const filteredActions = wizardState.layers.length > 1 ? actions : [actions[0], actions[2]];

    updateWizardUI(
      "STAGE 1",
      "Automatic Geometry",
      `Found ${wizardState.layers.length} nesting border layers. Currently showing innermost (Layer ${wizardState.layerIndex + 1}/${wizardState.layers.length}).`,
      filteredActions
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
        state.selected.artwork_area = proposal.artwork_area;
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

    // Hide current polygon handles so user knows we are waiting for a click
    $("selectionSvg").classList.add("hidden");

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
          state.selected.artwork_area = proposal.artwork_area;
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
    closeWizard();

    try {
      await saveTemplate();

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

  function closeWizard() {
    disableCanvasClickListener();
    wizardState.active = false;
    $("detectionWizardHud").classList.add("hidden");
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

  function providerTitle(provider) {
    if (provider === "vertex") return "Vertex AI";
    if (provider === "local") return "Local AI";
    return "Classic edge detection";
  }

  function updateDetectionModeSwitch() {
    const selectedProvider = state.settings.DETECTION_PROVIDER || "classic";
    document.querySelectorAll(".detection-mode-button").forEach((button) => {
      const isActive = button.dataset.provider === selectedProvider;
      button.classList.toggle("active", isActive);
      button.setAttribute("aria-pressed", String(isActive));
      button.disabled = state.busy || state.switchingProvider;
    });
  }

  function showProvider(provider) {
    state.settings.DETECTION_PROVIDER = provider;
    document.querySelectorAll(".provider-card").forEach((card) => {
      card.classList.toggle("selected", card.dataset.provider === provider);
    });
    updateDetectionModeSwitch();
    $("vertexConfig").classList.toggle("hidden", provider !== "vertex");
    $("localConfig").classList.toggle("hidden", provider !== "local");
    $("classicConfig").classList.toggle("hidden", provider !== "classic");
    $("engineProvider").textContent = providerTitle(provider);
    $("engineModel").textContent = provider === "vertex"
      ? `${$("vertexModel").value || "gemini-2.5-flash"} / ${$("vertexLocation").value || "global"}`
      : provider === "local" ? ($("localModel").value || "Choose an installed model")
      : ($("classicGreenFramesMode") && $("classicGreenFramesMode").checked ? "Green frames mockups" : "Standard geometric wizard");
    if ($("classicGreenOptions")) {
      $("classicGreenOptions").classList.toggle("hidden", provider !== "classic" || !$("classicGreenFramesMode") || !$("classicGreenFramesMode").checked);
    }
  }

  async function switchDetectionProvider(provider) {
    if (state.busy || state.switchingProvider || provider === (state.settings.DETECTION_PROVIDER || "classic")) return;
    const previousProvider = state.settings.DETECTION_PROVIDER || "classic";
    state.switchingProvider = true;
    showProvider(provider);
    updateDetectionModeSwitch();
    try {
      const payload = await api("/api/admin/settings/detection", {
        method: "PUT",
        body: JSON.stringify({ DETECTION_PROVIDER: provider })
      });
      state.settings = { ...state.settings, ...payload.settings };
      showProvider(state.settings.DETECTION_PROVIDER || provider);
      toast(`${providerTitle(state.settings.DETECTION_PROVIDER || provider)} selected`);
      setStatus(`Detection engine set to ${providerTitle(state.settings.DETECTION_PROVIDER || provider)}`);
    } catch (error) {
      showProvider(previousProvider);
      toast(error.message);
      setStatus("Detection engine switch failed", true);
    } finally {
      state.switchingProvider = false;
      updateDetectionModeSwitch();
    }
  }

  function fillModels(select, models, selected, placeholder) {
    select.innerHTML = models.map((model) =>
      `<option value="${escapeHtml(model.id)}">${escapeHtml(model.label)}${model.stage ? ` / ${escapeHtml(model.stage)}` : ""}</option>`
    ).join("");
    if (!models.length) {
      select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>`;
    } else if (selected && !models.some((model) => model.id === selected)) {
      select.insertAdjacentHTML("beforeend", `<option value="${escapeHtml(selected)}">${escapeHtml(selected)} / previously configured</option>`);
    }
    select.value = selected && [...select.options].some((option) => option.value === selected)
      ? selected
      : (select.options[0] ? select.options[0].value : "");
  }

  async function loadVertexModels(selected) {
    const payload = await api("/api/admin/settings/detection/models?provider=vertex");
    fillModels($("vertexModel"), payload.models, selected, "No compatible Vertex models found");
    $("settingsNotice").textContent = payload.source === "fallback"
      ? "Live Vertex model catalog unavailable; showing compatible fallback models."
      : `${payload.models.length} compatible Vertex model(s) loaded from Model Garden.`;
  }

  async function loadLocalModels(showFeedback = false) {
    const endpoint = $("localUrl").value.trim();
    if (!endpoint) {
      fillModels($("localModel"), [], "", "Enter an endpoint to list models");
      $("localModelNotice").textContent = "Enter the local detector endpoint, then load models installed on that service.";
      return;
    }
    try {
      const query = `/api/admin/settings/detection/models?provider=local&endpoint=${encodeURIComponent(endpoint)}`;
      const payload = await api(query);
      fillModels(
        $("localModel"),
        payload.models,
        state.settings.LOCAL_DETECTION_MODEL || "",
        "No installed models reported by this endpoint"
      );
      $("localModelNotice").textContent = payload.models.length
        ? `${payload.models.length} installed model(s) reported by the local service.`
        : "The endpoint did not expose an OpenAI-compatible or Ollama model list.";
      if (showFeedback) toast(payload.models.length ? "Installed local models loaded" : "No local models were reported");
      showProvider("local");
    } catch (error) {
      $("localModelNotice").textContent = error.message;
      if (showFeedback) toast(error.message);
    }
  }

  async function loadSettings() {
    state.settings = (await api("/api/admin/settings/detection")).settings;
    $("vertexProject").value = state.settings.VERTEX_PROJECT_ID || "";
    await loadVertexModels(state.settings.VERTEX_MODEL || "gemini-2.5-flash");
    $("vertexLocation").value = state.settings.VERTEX_LOCATION || "global";
    $("vertexResolution").value = state.settings.VERTEX_MEDIA_RESOLUTION || "high";
    $("refinementMode").value = state.settings.DETECTION_REFINEMENT || "ai_only";
    if ($("classicBlurSize")) $("classicBlurSize").value = state.settings.CLASSIC_BLUR_SIZE || "3";
    if ($("classicSearchRadius")) $("classicSearchRadius").value = state.settings.CLASSIC_SEARCH_RADIUS || "20";
    if ($("classicGreenFramesMode")) $("classicGreenFramesMode").checked = (state.settings.CLASSIC_INTERNAL_MODE || "auto") === "green_frames_mockups";
    if ($("classicGreenEdgeExpand")) $("classicGreenEdgeExpand").value = state.settings.CLASSIC_GREEN_EDGE_EXPAND || "1";
    $("localUrl").value = state.settings.LOCAL_DETECTION_URL || "";
    await loadLocalModels(false);
    showProvider(state.settings.DETECTION_PROVIDER || "classic");
  }

  async function saveSettings(showFeedback = true) {
    try {
      if ($("vertexModel").value === "gemini-3-flash-preview") $("vertexLocation").value = "global";
      const payload = await api("/api/admin/settings/detection", {
        method: "PUT",
        body: JSON.stringify({
          DETECTION_PROVIDER: state.settings.DETECTION_PROVIDER,
          VERTEX_PROJECT_ID: $("vertexProject").value,
          VERTEX_LOCATION: $("vertexLocation").value,
          VERTEX_MODEL: $("vertexModel").value,
          VERTEX_MEDIA_RESOLUTION: $("vertexResolution").value,
          VERTEX_AUTH_MODE: $("vertexAuth").value,
          DETECTION_REFINEMENT: $("refinementMode").value,
          CLASSIC_BLUR_SIZE: $("classicBlurSize") ? $("classicBlurSize").value : "3",
          CLASSIC_SEARCH_RADIUS: $("classicSearchRadius") ? $("classicSearchRadius").value : "20",
          CLASSIC_INTERNAL_MODE: $("classicGreenFramesMode") && $("classicGreenFramesMode").checked ? "green_frames_mockups" : "auto",
          CLASSIC_GREEN_EDGE_EXPAND: $("classicGreenEdgeExpand") ? $("classicGreenEdgeExpand").value : "1",
          LOCAL_DETECTION_URL: $("localUrl").value,
          LOCAL_DETECTION_MODEL: $("localModel").value
        })
      });
      state.settings = { ...state.settings, ...payload.settings };
      showProvider(state.settings.DETECTION_PROVIDER);
      $("settingsNotice").textContent = "Saved. Close this panel and run Detect frame.";
      if (showFeedback) {
        $("engineDrawer").classList.remove("open");
        toast("Detection settings saved");
      }
      return true;
    } catch (error) {
      $("settingsNotice").textContent = error.message;
      return false;
    }
  }

  async function testEngine() {
    if (!state.selected) {
      toast("Select a mockup before testing the detector.");
      return;
    }
    $("testEngine").disabled = true;
    $("testEngine").textContent = "Testing...";
    $("settingsNotice").textContent = "Saving settings and testing against the selected mockup...";
    try {
      if (!(await saveSettings(false))) return;
      const payload = await api("/api/admin/settings/detection/test", {
        method: "POST",
        body: JSON.stringify({ template_id: state.selected.template_id })
      });
      if (payload.proposal) {
        state.selected.artwork_area = payload.proposal.artwork_area;
        if (payload.proposal.raw_artwork_area) {
          state.selected.raw_artwork_area = payload.proposal.raw_artwork_area;
        } else {
          delete state.selected.raw_artwork_area;
        }
        renderEditor();
      }
      const confidence = payload.proposal.confidence == null
        ? ""
        : ` (${Math.round(payload.proposal.confidence * 100)}%)`;
      $("settingsNotice").textContent = `${payload.proposal.provider} connected successfully${confidence}.`;
      toast("Detector connection test completed");
    } catch (error) {
      $("settingsNotice").textContent = error.message;
      toast(error.message);
    } finally {
      $("testEngine").disabled = false;
      $("testEngine").textContent = "Test connection";
    }
  }

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
        if (ws) ws.classList.add("panning-mode");
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
      if (!state.selected) return;

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
    resetZoomPan();
  };

  function zoomIncrementally(direction) {
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
        drawSelection();
        persistTemplateState(state.selected);
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
        drawSelection();
        persistTemplateState(state.selected);
      }
    };
  }

  let greenFrameSettingsSaveTimeout = null;
  function updateGreenFrameSettingsFromControls() {
    if (!state.selected || !$("greenFramePanel")) return;
    if (!state.selected.effects) state.selected.effects = defaultEffects();
    state.selected.effects.green_frame_mockups = readGreenFrameControls();
    updateGreenFrameControlLabels();
    if (greenFrameSettingsSaveTimeout) clearTimeout(greenFrameSettingsSaveTimeout);
    greenFrameSettingsSaveTimeout = setTimeout(async () => {
      await persistTemplateState(state.selected);
      if (isGreenFrameTemplate() && state.selectionStyle.overlayImage) {
        refreshGreenFrameMockupPreview();
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
    "greenMaskBuildQuality",
    "greenFeatherRadius",
    "greenEdgeAARadius",
    "greenAAScale",
    "greenInnerShadowEnabled",
    "greenInnerShadowStrength",
    "greenInnerShadowSize",
    "greenContainBgColor"
  ].forEach((id) => {
    const element = $(id);
    if (!element) return;
    element.addEventListener("input", updateGreenFrameSettingsFromControls);
    element.addEventListener("change", updateGreenFrameSettingsFromControls);
  });

  // Realism Effects Event Listeners & Live Preview Updates
  let refreshPreviewTimeout = null;
  let greenFramePreviewTimeout = null;

  async function renderMockupPreviewImage() {
    if (!state.selected) return null;
    
    // Disable download interactions temporarily to show rendering state
    if ($("downloadMockupButton")) {
      $("downloadMockupButton").style.pointerEvents = "none";
      $("downloadMockupButton").style.opacity = "0.5";
    }
    if ($("toolbarDownloadButton")) {
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
      formData.append("template_id", state.selected.template_id);
      formData.append("artwork", file);
      formData.append("realism", "true");
      
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
        headers: { "X-CSRF-Token": csrf },
        body: formData
      });

      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Rendering failed");

      if ($("downloadMockupButton")) {
        $("downloadMockupButton").href = data.output_url;
        $("downloadMockupButton").style.pointerEvents = "auto";
        $("downloadMockupButton").style.opacity = "1";
      }
      if ($("toolbarDownloadButton")) {
        $("toolbarDownloadButton").href = data.output_url;
        $("toolbarDownloadButton").style.pointerEvents = "auto";
        $("toolbarDownloadButton").style.opacity = "1";
        $("toolbarDownloadButton").setAttribute("title", "Download realistic mockup");
      }
      return data.output_url;
    } catch (err) {
      console.error("Live-preview refresh render failed:", err);
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
      await persistTemplateState(state.selected);

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

  // Test Mockups Modal Logic
  $("openTestModal").onclick = () => {
    if (state.busy) return;
    $("testModal").classList.add("open");
    renderTestGallery();
    renderMockupGallery();
    if (testState.activeIndex === -1) {
      resetTestResult();
    }
  };
  $("closeTestModal").onclick = () => $("testModal").classList.remove("open");
  $("testModal").onclick = (event) => {
    if (event.target === $("testModal")) $("testModal").classList.remove("open");
  };

  // Lightbox Preview Handlers
  function showLightbox(src, title) {
    const overlay = $("lightboxOverlay");
    const img = $("lightboxImage");
    const caption = $("lightboxCaption");
    if (!overlay || !img) return;
    
    img.src = src;
    if (caption) {
      caption.textContent = title || "Mockup Preview";
    }
    overlay.classList.remove("hidden");
  }

  function hideLightbox() {
    const overlay = $("lightboxOverlay");
    if (overlay) {
      overlay.classList.add("hidden");
    }
  }

  if ($("closeLightboxBtn")) {
    $("closeLightboxBtn").onclick = hideLightbox;
  }
  if ($("lightboxOverlay")) {
    $("lightboxOverlay").onclick = (e) => {
      if (e.target === $("lightboxOverlay") || e.target === $("closeLightboxBtn")) {
        hideLightbox();
      }
    };
  }

  // Hook single preview click
  if ($("testResultImage")) {
    $("testResultImage").onclick = () => {
      const src = $("testResultImage").src;
      const templateSelect = $("testTemplateSelect");
      const title = templateSelect ? templateSelect.options[templateSelect.selectedIndex]?.text : "Generated Mockup";
      showLightbox(src, title);
    };
  }

  // Hook batch previews using event delegation on the grid container
  const batchContainer = $("testBatchResults");
  if (batchContainer) {
    batchContainer.addEventListener("click", (e) => {
      if (e.target.classList.contains("batch-card-img")) {
        const src = e.target.src;
        const card = e.target.closest(".batch-result-card");
        const title = card ? card.querySelector(".batch-card-title")?.textContent : "Generated Mockup";
        showLightbox(src, title);
      }
    });
  }

  // Bind upload triggers on the active artwork container (Click & Drag-and-drop)
  const artworkContainer = $("testArtworkPreviewContainer");
  if (artworkContainer) {
    artworkContainer.onclick = () => $("testArtworkFile").click();

    ["dragenter", "dragover"].forEach((eventName) => {
      artworkContainer.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        artworkContainer.classList.add("drag");
      });
    });

    ["dragleave", "drop"].forEach((eventName) => {
      artworkContainer.addEventListener(eventName, (event) => {
        event.preventDefault();
        event.stopPropagation();
        artworkContainer.classList.remove("drag");
      });
    });

    artworkContainer.addEventListener("drop", (event) => {
      const files = event.dataTransfer.files;
      if (files.length > 0) {
        handleTestArtworkUpload(files);
      }
    });
  }

  function resetTestResult() {
    $("testResultPlaceholder").classList.remove("hidden");
    $("testResultWrapper").classList.add("hidden");
    $("testResultActions").classList.add("hidden");
    if ($("testResultLoading")) $("testResultLoading").classList.add("hidden");
    $("testResultImage").src = "";
    $("testResultDownload").href = "";
  }

  $("testArtworkFile").onchange = (e) => {
    handleTestArtworkUpload(e.target.files);
    e.target.value = "";
  };

  function handleTestArtworkUpload(filesList) {
    const newFiles = Array.from(filesList);
    if (newFiles.length === 0) return;

    // Add new files to the list
    testState.files = testState.files.concat(newFiles.map(file => ({
      file,
      url: URL.createObjectURL(file),
      orientation: null
    })));

    // Pre-calculate orientation for new files
    testState.files.forEach(f => {
      if (!f.orientation) {
        const img = new Image();
        img.onload = () => {
          if (img.width === img.height) f.orientation = "square";
          else if (img.width > img.height) f.orientation = "landscape";
          else f.orientation = "portrait";

          // If this is the active file and we just computed it, trigger select
          if (testState.activeIndex !== -1 && testState.files[testState.activeIndex] === f) {
            selectTestImage(testState.activeIndex);
          }
        };
        img.src = f.url;
      }
    });

    renderTestGallery();

    if (testState.activeIndex === -1) {
      selectTestImage(0);
    }
  }

  function renderTestGallery() {
    const gallery = $("testGallery");
    if (testState.files.length === 0) {
      gallery.innerHTML = `<div class="gallery-empty">No artworks uploaded</div>`;
      return;
    }

    gallery.innerHTML = testState.files.map((f, i) => `
      <div class="test-gallery-item-wrapper ${i === testState.activeIndex ? 'active' : ''}" data-index="${i}">
        <img src="${f.url}" class="test-gallery-item-img">
        <button class="test-gallery-item-delete" data-index="${i}">&times;</button>
      </div>
    `).join('');

    gallery.querySelectorAll('.test-gallery-item-wrapper').forEach(item => {
      item.onclick = (e) => {
        if (e.target.classList.contains('test-gallery-item-delete')) {
          e.stopPropagation();
          deleteTestImage(Number(item.dataset.index));
          return;
        }
        selectTestImage(Number(item.dataset.index));
      };
    });
  }

  function deleteTestImage(index) {
    URL.revokeObjectURL(testState.files[index].url);
    testState.files.splice(index, 1);

    if (testState.files.length === 0) {
      testState.activeIndex = -1;
      testState.templates = [];
      testState.selectedTemplates.clear();
      $("testArtworkPreview").src = "";
      $("testArtworkPreview").classList.add("hidden");
      $("testUploadPlaceholder").classList.remove("hidden");
      $("testOrientationLabel").textContent = "";
      $("testMockupGallery").innerHTML = `<div class="gallery-empty">Upload artwork to load matching templates</div>`;
      $("testTemplateSelect").innerHTML = `<option value="">Upload an image first</option>`;
      $("testTemplateSelect").disabled = true;
      $("testGenerateButton").disabled = true;
      $("testGenerateButton").textContent = "Generate";

      // Reset mockup preview
      $("testMockupPreview").src = "";
      $("testMockupPreview").classList.add("hidden");
      $("testMockupPlaceholder").classList.remove("hidden");

      resetTestResult();
      $("testBatchResults").classList.add("hidden");
      $("testBatchResults").innerHTML = "";
    } else {
      if (testState.activeIndex === index) {
        const nextActive = Math.max(0, index - 1);
        selectTestImage(nextActive);
      } else if (testState.activeIndex > index) {
        testState.activeIndex--;
      }
    }
    renderTestGallery();
  }

  function renderMockupGallery() {
    const gallery = $("testMockupGallery");
    if (testState.templates.length === 0) {
      gallery.innerHTML = `<div class="gallery-empty">No matching mockups found</div>`;
      return;
    }

    gallery.innerHTML = testState.templates.map((t) => {
      const previewUrl = t.preview_url || `/api/admin/templates/${t.template_id}/asset/preview.png`;
      const isSelected = testState.selectedTemplates.has(t.template_id);
      return `
        <div class="test-mockup-card-wrapper ${isSelected ? 'selected' : ''}" data-id="${t.template_id}" title="${escapeHtml(t.name)}">
          <img src="${previewUrl}" class="test-mockup-card-img" alt="${escapeHtml(t.name)}">
          <div class="test-mockup-card-checkbox">
            ${isSelected ? '&#10004;' : ''}
          </div>
          <div class="test-mockup-card-title">${escapeHtml(t.name)}</div>
        </div>
      `;
    }).join('');

    gallery.querySelectorAll('.test-mockup-card-wrapper').forEach(card => {
      card.onclick = (e) => {
        e.stopPropagation();
        toggleMockupSelection(card.dataset.id);
      };
    });
  }

  function toggleMockupSelection(templateId) {
    if (testState.selectedTemplates.has(templateId)) {
      testState.selectedTemplates.delete(templateId);
    } else {
      testState.selectedTemplates.add(templateId);
    }

    const activeFile = testState.files[testState.activeIndex];
    const hasSelection = testState.selectedTemplates.size > 0;
    $("testGenerateButton").disabled = !activeFile || !activeFile.orientation || !hasSelection;

    // Update generate button text based on selection count
    if (testState.selectedTemplates.size > 1) {
      $("testGenerateButton").textContent = `Generate (${testState.selectedTemplates.size} mockups)`;
    } else {
      $("testGenerateButton").textContent = "Generate";
    }

    // Synchronize select element and active mockup preview
    const select = $("testTemplateSelect");
    if (hasSelection) {
      const firstId = Array.from(testState.selectedTemplates)[0];
      select.value = firstId;

      const t = testState.templates.find(x => x.template_id === templateId);
      if (t && testState.selectedTemplates.has(templateId)) {
        const previewUrl = t.preview_url || `/api/admin/templates/${t.template_id}/asset/preview.png`;
        $("testMockupPreview").src = previewUrl;
        $("testMockupPreview").classList.remove("hidden");
        $("testMockupPlaceholder").classList.add("hidden");
      }
    } else {
      select.value = "";
      $("testMockupPreview").src = "";
      $("testMockupPreview").classList.add("hidden");
      $("testMockupPlaceholder").classList.remove("hidden");
    }

    renderMockupGallery();
  }

  function selectMockupTemplate(templateId) {
    // Legacy support: redirects to toggle selection
    toggleMockupSelection(templateId);
  }

  async function selectTestImage(index) {
    if (index < 0 || index >= testState.files.length) return;
    testState.activeIndex = index;
    const activeFile = testState.files[index];

    $("testArtworkPreview").src = activeFile.url;
    $("testArtworkPreview").classList.remove("hidden");
    $("testUploadPlaceholder").classList.add("hidden");

    renderTestGallery(); // Update active state class
    resetTestResult();   // Switch active image resets result state
    $("testBatchResults").classList.add("hidden");
    $("testBatchResults").innerHTML = "";

    if (!activeFile.orientation) {
      $("testOrientationLabel").textContent = "Detecting orientation...";
      $("testTemplateSelect").innerHTML = `<option value="">Detecting orientation...</option>`;
      $("testTemplateSelect").disabled = true;
      $("testGenerateButton").disabled = true;
      $("testGenerateButton").textContent = "Generate";
      $("testMockupGallery").innerHTML = `<div class="gallery-empty">Detecting orientation...</div>`;

      // Reset mockup preview
      $("testMockupPreview").src = "";
      $("testMockupPreview").classList.add("hidden");
      $("testMockupPlaceholder").classList.remove("hidden");
      testState.selectedTemplates.clear();
      return; // Will be called again by the onload handler
    }

    $("testOrientationLabel").textContent = `Detected orientation: ${activeFile.orientation}`;

    // Fetch templates
    try {
      const payload = await api("/api/mockups/templates");
      testState.templates = payload.filter(t => t.orientation === activeFile.orientation);

      const select = $("testTemplateSelect");
      const currentSelectedTemplateId = select.value;
      select.innerHTML = "";
      if (testState.templates.length === 0) {
        select.innerHTML = `<option value="">No matching mockups found</option>`;
        select.disabled = true;
        $("testGenerateButton").disabled = true;
        $("testGenerateButton").textContent = "Generate";
        testState.selectedTemplates.clear();

        // Reset mockup preview
        $("testMockupPreview").src = "";
        $("testMockupPreview").classList.add("hidden");
        $("testMockupPlaceholder").classList.remove("hidden");
      } else {
        testState.templates.forEach(t => {
          const opt = document.createElement("option");
          opt.value = t.template_id;
          opt.textContent = `${t.name || t.template_id}`;
          select.appendChild(opt);
        });

        // Preserve template selection if the newly filtered list contains the same template ID
        const hasSameTemplate = currentSelectedTemplateId && testState.templates.some(t => t.template_id === currentSelectedTemplateId);
        const activeTemplateId = hasSameTemplate ? currentSelectedTemplateId : testState.templates[0].template_id;

        testState.selectedTemplates.clear();
        testState.selectedTemplates.add(activeTemplateId);

        select.value = activeTemplateId;
        select.disabled = false;
        $("testGenerateButton").disabled = false;
        $("testGenerateButton").textContent = "Generate";

        // Render mockup preview
        const activeT = testState.templates.find(t => t.template_id === activeTemplateId);
        const previewUrl = activeT.preview_url || `/api/admin/templates/${activeT.template_id}/asset/preview.png`;
        $("testMockupPreview").src = previewUrl;
        $("testMockupPreview").classList.remove("hidden");
        $("testMockupPlaceholder").classList.add("hidden");
      }
      renderMockupGallery(); // Render visual gallery
    } catch (err) {
      toast("Failed to load templates");
    }
  }

  $("testGenerateButton").onclick = async () => {
    if (testState.activeIndex === -1 || testState.selectedTemplates.size === 0) return;

    const activeFile = testState.files[testState.activeIndex];
    const renderMode = $("testRenderMode").value;
    const aiModel = $("testAiModel").value;
    const fitMode = $("testFitMode").value;
    const selectedIds = Array.from(testState.selectedTemplates);

    // Single Mockup Generation Workflow
    if (selectedIds.length === 1) {
      const templateId = selectedIds[0];
      $("testGenerateButton").disabled = true;
      $("testGenerateButton").textContent = "Generating...";

      // Show loading card and hide result states
      $("testResultPlaceholder").classList.add("hidden");
      $("testResultWrapper").classList.add("hidden");
      $("testResultActions").classList.add("hidden");
      $("testResultLoading").classList.remove("hidden");

      const formData = new FormData();
      formData.append("mode", renderMode);
      formData.append("template_id", templateId);
      formData.append("artwork", activeFile.file);

      if (renderMode === "simple" && fitMode) {
        formData.append("fit_mode", fitMode);
      } else if (renderMode === "ai" && aiModel) {
        formData.append("model", aiModel);
      }

      // Safe request timeout of 120 seconds
      const controller = new AbortController();
      const timeoutId = setTimeout(() => controller.abort(), 120000);

      try {
        const response = await fetch("/api/mockups/render", {
          method: "POST",
          headers: { "X-CSRF-Token": csrf },
          body: formData,
          signal: controller.signal
        });
        clearTimeout(timeoutId);

        const data = await response.json();
        if (!response.ok) throw new Error(data.error || "Generation failed");

        $("testResultImage").src = data.output_url;
        $("testResultDownload").href = data.output_url;

        // Switch view to single result
        $("testBatchResults").classList.add("hidden");
        $("testResultLoading").classList.add("hidden");
        $("testResultPlaceholder").classList.add("hidden");
        $("testResultWrapper").classList.remove("hidden");
        $("testResultActions").classList.remove("hidden");
      } catch (err) {
        clearTimeout(timeoutId);
        if (err.name === "AbortError") {
          toast("Request timed out (120s limit)");
        } else {
          toast(err.message);
        }
        // Restore placeholder on failure
        $("testResultLoading").classList.add("hidden");
        $("testResultPlaceholder").classList.remove("hidden");
      } finally {
        $("testGenerateButton").disabled = false;
        $("testGenerateButton").textContent = "Generate";
      }
    }
    // Multi-Mockup Batch Generation Workflow
    else {
      $("testGenerateButton").disabled = true;
      $("testGenerateButton").textContent = "Generating batch...";

      // Hide single preview, show batch container
      $("testResultPlaceholder").classList.add("hidden");
      $("testResultWrapper").classList.add("hidden");
      $("testResultActions").classList.add("hidden");

      const batchContainer = $("testBatchResults");
      batchContainer.classList.remove("hidden");

      // Populate placeholder loader cards for each selected mockup
      batchContainer.innerHTML = selectedIds.map(templateId => {
        const t = testState.templates.find(x => x.template_id === templateId);
        const name = t ? (t.name || templateId) : templateId;
        return `
          <div class="batch-result-card" id="batch-card-${templateId}">
            <div class="batch-card-header">
              <span class="batch-card-title" title="${escapeHtml(name)}">${escapeHtml(name)}</span>
              <span class="batch-card-status" id="batch-status-${templateId}">Pending...</span>
            </div>
            <div class="batch-card-body">
              <div class="batch-card-spinner" id="batch-spinner-${templateId}"></div>
              <img class="batch-card-img hidden" id="batch-img-${templateId}" alt="${escapeHtml(name)}">
            </div>
            <div class="batch-card-actions hidden" id="batch-actions-${templateId}">
              <a class="btn primary" id="batch-download-${templateId}" download="mockup_${templateId}.png" href="#">Download</a>
            </div>
          </div>
        `;
      }).join('');

      // Helper function to render a single batch mockup item (with retry support)
      const renderBatchItem = async (templateId, retryCount = 0) => {
        // Wrap everything in a top-level try-catch block to guarantee no hanging promise
        try {
          const cardElement = $(`batch-card-${templateId}`);
          const statusElement = $(`batch-status-${templateId}`);
          const imgElement = $(`batch-img-${templateId}`);
          const actionsElement = $(`batch-actions-${templateId}`);

          if (!cardElement || !statusElement || !imgElement || !actionsElement) {
            console.error(`UI elements not found for mockup: ${templateId}`);
            return;
          }

          // Clear any previous error elements from prior retries
          const prevError = cardElement.querySelector('.batch-error-message');
          if (prevError) prevError.remove();
          cardElement.classList.remove("success", "error");

          // Hide actions during rendering
          actionsElement.classList.add("hidden");

          // Restore / ensure spinner exists for retries
          let spinnerElement = cardElement.querySelector('.batch-card-spinner');
          if (!spinnerElement) {
            spinnerElement = document.createElement("div");
            spinnerElement.className = "batch-card-spinner";
            spinnerElement.id = `batch-spinner-${templateId}`;

            const body = cardElement.querySelector('.batch-card-body');
            if (body) {
              // Hide image during retry
              imgElement.classList.add("hidden");
              body.appendChild(spinnerElement);
            }
          }

          statusElement.textContent = retryCount > 0 ? `Retrying (${retryCount}/2)...` : "Generating...";

          const formData = new FormData();
          formData.append("mode", renderMode);
          formData.append("template_id", templateId);
          formData.append("artwork", activeFile.file);

          if (renderMode === "simple" && fitMode) {
            formData.append("fit_mode", fitMode);
          } else if (renderMode === "ai" && aiModel) {
            formData.append("model", aiModel);
          }

          // Safe request timeout of 120 seconds
          const controller = new AbortController();
          const timeoutId = setTimeout(() => controller.abort(), 120000);

          try {
            const response = await fetch("/api/mockups/render", {
              method: "POST",
              headers: { "X-CSRF-Token": csrf },
              body: formData,
              signal: controller.signal
            });
            clearTimeout(timeoutId);

            const data = await response.json();

            if (!response.ok) {
              // Check if rate limited / resource exhausted
              const isRateLimited = response.status === 429 ||
                (data.error && data.error.toLowerCase().includes("resource")) ||
                (data.error && data.error.toLowerCase().includes("exhausted")) ||
                (data.error && data.error.toLowerCase().includes("quota"));

              if (isRateLimited && retryCount < 2) {
                statusElement.textContent = "Rate limited. Waiting...";
                // Wait for 3.5s on first retry, 7s on second retry
                const delay = (retryCount + 1) * 3500;
                await new Promise(resolve => setTimeout(resolve, delay));
                return await renderBatchItem(templateId, retryCount + 1);
              }
              throw new Error(data.error || "Failed");
            }

            // Render success details
            cardElement.classList.add("success");
            statusElement.textContent = "Ready";
            if (spinnerElement) spinnerElement.remove();

            imgElement.src = data.output_url;
            imgElement.classList.remove("hidden");

            // Dynamically inject the Download button
            actionsElement.innerHTML = `<a class="btn primary" id="batch-download-${templateId}" download="mockup_${templateId}.png" href="${data.output_url}">Download</a>`;
            actionsElement.classList.remove("hidden");
          } catch (err) {
            clearTimeout(timeoutId);
            cardElement.classList.add("error");

            if (err.name === "AbortError") {
              statusElement.textContent = "Timeout";
            } else {
              statusElement.textContent = "Failed";
            }

            if (spinnerElement) spinnerElement.remove();

            // Display elegant error label
            const errorDiv = document.createElement("div");
            errorDiv.className = "sub batch-error-message";
            errorDiv.style.color = "var(--accent)";
            errorDiv.style.textAlign = "center";
            errorDiv.style.padding = "10px";
            errorDiv.textContent = err.name === "AbortError"
              ? "Request timed out (120s limit)"
              : (err.message || "Rendering failed");
            imgElement.parentNode.appendChild(errorDiv);

            // Dynamically inject the Retry button
            actionsElement.innerHTML = `<button class="btn accent batch-retry-btn" style="width: 100%; height: 28px; font-size: 11px; padding: 0 8px;" type="button">Retry</button>`;
            actionsElement.classList.remove("hidden");

            const retryBtn = actionsElement.querySelector('.batch-retry-btn');
            retryBtn.onclick = (e) => {
              e.stopPropagation();
              renderBatchItem(templateId);
            };
          }
        } catch (globalErr) {
          console.error("Global promise exception in batch item:", globalErr);
          const cardElement = $(`batch-card-${templateId}`);
          if (cardElement) {
            cardElement.classList.add("error");
            const statusElement = $(`batch-status-${templateId}`);
            if (statusElement) statusElement.textContent = "Failed";
            const spinnerElement = $(`batch-spinner-${templateId}`);
            if (spinnerElement) spinnerElement.remove();

            const actionsElement = $(`batch-actions-${templateId}`);
            if (actionsElement) {
              actionsElement.innerHTML = `<button class="btn accent batch-retry-btn" style="width: 100%; height: 28px; font-size: 11px; padding: 0 8px;" type="button">Retry</button>`;
              actionsElement.classList.remove("hidden");

              const retryBtn = actionsElement.querySelector('.batch-retry-btn');
              retryBtn.onclick = (e) => {
                e.stopPropagation();
                renderBatchItem(templateId);
              };
            }
          }
        }
      };

      if (renderMode === "ai") {
        // AI Mode: Process sequentially to completely avoid concurrent rate limits on Vertex AI (QPM limits)
        for (const templateId of selectedIds) {
          await renderBatchItem(templateId);
        }
      } else {
        // Simple Mode: Process concurrently in parallel for maximum local CPU speed
        const promises = selectedIds.map(templateId => renderBatchItem(templateId));
        await Promise.allSettled(promises);
      }

      $("testGenerateButton").disabled = false;
      $("testGenerateButton").textContent = `Generate (${testState.selectedTemplates.size} mockups)`;
    }
  };

  const testRenderMode = $("testRenderMode");
  if (testRenderMode) {
    testRenderMode.onchange = () => {
      const isAI = testRenderMode.value === "ai";
      const fitContainer = $("testFitModeContainer");
      if (fitContainer) {
        fitContainer.classList.toggle("hidden", isAI);
      }
      const aiModelContainer = $("testAiModelContainer");
      if (aiModelContainer) {
        aiModelContainer.classList.toggle("hidden", !isAI);
      }
    };
  }
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
  async function togglePreviewMode() {
    if (!state.selected || state.busy) return;

    closeSelectionStylePanel();

    if (state.isPreviewingMockup) {
      // Toggle back to Edit Mode instantly
      state.isPreviewingMockup = false;
      
      // Sync Header buttons
      if ($("previewMockupButton")) $("previewMockupButton").textContent = "Preview Mockup";
      if ($("downloadMockupButton")) {
        $("downloadMockupButton").classList.add("hidden");
        $("downloadMockupButton").href = "";
      }
      
      // Sync Toolbar buttons
      if ($("toolbarPreviewButton")) $("toolbarPreviewButton").classList.remove("active");
      if ($("toolbarDownloadButton")) {
        $("toolbarDownloadButton").classList.add("hidden");
        $("toolbarDownloadButton").href = "";
        $("toolbarDownloadButton").style.pointerEvents = "auto";
        $("toolbarDownloadButton").style.opacity = "1";
        $("toolbarDownloadButton").removeAttribute("title");
      }

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
      setStatus("High-fidelity mockup preview active");

      // 4. Show download buttons as "Generating..." (Disabled / Loading)
      if ($("downloadMockupButton")) {
        $("downloadMockupButton").classList.remove("hidden");
        $("downloadMockupButton").style.pointerEvents = "none";
        $("downloadMockupButton").style.opacity = "0.5";
      }
      if ($("toolbarDownloadButton")) {
        $("toolbarDownloadButton").classList.remove("hidden");
        $("toolbarDownloadButton").style.pointerEvents = "none";
        $("toolbarDownloadButton").style.opacity = "0.5";
        $("toolbarDownloadButton").setAttribute("title", "Generating high-fidelity download...");
      }

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
            headers: { "X-CSRF-Token": csrf },
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

            // Enable download buttons
            if ($("downloadMockupButton")) {
              $("downloadMockupButton").href = data.output_url;
              $("downloadMockupButton").style.pointerEvents = "auto";
              $("downloadMockupButton").style.opacity = "1";
            }
            if ($("toolbarDownloadButton")) {
              $("toolbarDownloadButton").href = data.output_url;
              $("toolbarDownloadButton").style.pointerEvents = "auto";
              $("toolbarDownloadButton").style.opacity = "1";
              $("toolbarDownloadButton").setAttribute("title", "Download realistic mockup");
            }
          }
        } catch (err) {
          console.error("Background render failed:", err);
          toast("Realistic background optimization failed, standard download is active");
          
          // Fallback to enabled download buttons
          if (state.isPreviewingMockup) {
            if ($("toolbarDownloadButton")) {
              $("toolbarDownloadButton").style.pointerEvents = "auto";
              $("toolbarDownloadButton").style.opacity = "1";
              $("toolbarDownloadButton").setAttribute("title", "Download mockup");
            }
          }
        } finally {
          // Unlock the UI and restore default detection panel labels
          setBusy(false);
          if ($("analysisLabel")) $("analysisLabel").textContent = "Detection is analyzing the frame";
          if ($("analysisSub")) $("analysisSub").textContent = "This can take several seconds.";
        }
      })();
    }
  }

  if ($("previewMockupButton")) $("previewMockupButton").onclick = togglePreviewMode;
  if ($("toolbarPreviewButton")) $("toolbarPreviewButton").onclick = togglePreviewMode;

  (async () => {
    try {
      await loadSettings();
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
      setStatus("Unable to load workspace", true);
      toast(error.message);
    }
  })();
})();
