/** The realism effects: what they are, and how their panels behave.
 *
 * Two things live here. The definitions -- every effect's default settings and
 * the ids of the controls that edit it -- and the plumbing that turns those
 * definitions into a working panel: reading a panel into an effect, writing an
 * effect back into a panel, and the second copy of an effect that a panel can
 * grow, whose controls are the same ones with new ids.
 *
 * What is *done* with an effect once it is read -- saving it, redrawing the
 * canvas, applying it to every template -- stays with the studio.
 */
import { $ } from "./dom.js";
import { cloneObject } from "./helpers.js";

export const DEFAULT_EFFECTS = {
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

export const EFFECT_DOM = {
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

export function defaultEffects() {
  return cloneObject(DEFAULT_EFFECTS);
}

export function effectInstances(effects, key) {
  const value = effects && effects[key];
  if (Array.isArray(value)) {
    return value.filter((item) => item && typeof item === "object").slice(0, 2);
  }
  if (value && typeof value === "object") return [value];
  return [cloneObject(DEFAULT_EFFECTS[key])];
}

export function primaryEffect(effects, key) {
  const instances = effectInstances(effects, key);
  return { ...cloneObject(DEFAULT_EFFECTS[key]), ...(instances[0] || {}) };
}

export function setEffectValueLabel(field, value, root = document) {
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

export function getFieldElement(root, id) {
  return root.querySelector(`#${CSS.escape(id)}`) || root.querySelector(`[data-original-id="${id}"]`);
}

export function setEffectInstanceValues(root, key, config) {
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

export function readEffectInstanceValues(root, key) {
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

export function effectGroupForKey(key, instance = "1") {
  return document.querySelector(`.effect-group[data-effect-key="${key}"][data-effect-instance="${instance}"]`);
}

export function syncEffectAddButton(key) {
  const group = effectGroupForKey(key, "1");
  if (!group) return;
  const button = group.querySelector(".effect-add-instance");
  if (button) button.classList.toggle("hidden", Boolean(effectGroupForKey(key, "2")));
}

export function updateEffectPanelCollapsed(group) {
  const controls = group && getFieldElement(group, EFFECT_DOM[group.dataset.effectKey]?.controlsId || "");
  const collapsed = group?.dataset.effectCollapsed === "true";
  if (controls) controls.classList.toggle("hidden", collapsed);
}

export function toggleEffectPanelCollapsed(group) {
  if (!group) return;
  group.dataset.effectCollapsed = String(group.dataset.effectCollapsed !== "true");
  updateEffectPanelCollapsed(group);
}

export function prepareEffectGroupControls(group, key, instance) {
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
