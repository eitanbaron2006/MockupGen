/** Which engine finds the frames, and how it is set up.
 *
 * The three providers -- the cloud model, the classic local scan, the local
 * model -- their health, their settings panel, and the modes the classic scan
 * offers. Choosing an engine is a decision about the studio as a whole, not
 * about the template on screen, which is why it lives apart from the editor.
 *
 * Two things it does need from the editor, handed to it once rather than
 * assumed: leaving whatever detection mode is running, and redrawing the
 * editor when a change of engine changes what the panel should show.
 */
import { $, setStatus, toast } from "./dom.js";
import { api } from "./api.js";
import { state } from "./state.js";
import { escapeAttr, escapeHtml } from "./helpers.js";

let editor = { exitAllDetectionModes() {}, renderEditor() {} };

/** Tell this module how to reach the editor. Called once, as the studio starts. */
export function configureEngineSettings(hooks) {
  editor = { ...editor, ...hooks };
}

export function providerTitle(provider) {
  if (provider === "vertex") return "Vertex AI";
  if (provider === "local") return "Local AI";
  return "Classic edge detection";
}

async function checkProviderHealth() {
  try {
    const res = await api("/api/admin/providers/status");
    if (res && res.providers) {
      state.providerHealth = res.providers;
      applyProviderHealthUI();
    }
  } catch (err) {
    console.warn("Could not fetch provider status:", err);
  }
}

function applyProviderHealthUI() {
  if (!state.providerHealth) return;
  const vertex = state.providerHealth.vertex;
  const vertexButton = document.querySelector('.detection-mode-button[data-provider="vertex"]');

  if (vertex && !vertex.available) {
    if (vertexButton) {
      vertexButton.title = `Vertex AI unavailable: ${vertex.error || "Connection failed"}`;
      vertexButton.classList.add("provider-offline");
    }
    if ($("vertexModelNotice")) {
      $("vertexModelNotice").textContent = `⚠️ Vertex AI is unavailable: ${vertex.error || "Connection failed"}`;
      $("vertexModelNotice").style.color = "var(--error, #c84b3e)";
    }
    // If active provider is currently vertex but it's not operational, fallback to classic
    if (state.settings.DETECTION_PROVIDER === "vertex") {
      console.warn("Vertex AI is unavailable. Falling back to Classic detection.");
      showProvider("classic");
      toast("Vertex AI is unavailable; switched to Classic detection");
    }
  } else if (vertex && vertex.available) {
    if (vertexButton) {
      vertexButton.title = "Vertex AI (Connected)";
      vertexButton.classList.remove("provider-offline");
    }
    if ($("vertexModelNotice")) {
      $("vertexModelNotice").textContent = "✓ Vertex AI is connected and operational.";
      $("vertexModelNotice").style.color = "var(--success, #6e7448)";
    }
  }
}

export function updateDetectionModeSwitch() {
  const selectedProvider = state.settings.DETECTION_PROVIDER || "classic";
  document.querySelectorAll(".detection-mode-button").forEach((button) => {
    const isProvider = button.dataset.provider;
    const isActive = isProvider === selectedProvider;
    button.classList.toggle("active", isActive);
    button.setAttribute("aria-pressed", String(isActive));
    
    const isVertex = isProvider === "vertex";
    const vertexOffline = isVertex && state.providerHealth && state.providerHealth.vertex && !state.providerHealth.vertex.available;
    button.disabled = state.busy || state.switchingProvider || vertexOffline;
    if (vertexOffline) {
      button.style.opacity = "0.45";
      button.style.cursor = "not-allowed";
    } else {
      button.style.opacity = "";
      button.style.cursor = "";
    }
  });
  updateClassicSubmodes();
}

export function updateClassicSubmodes() {
  const provider = state.settings.DETECTION_PROVIDER || "classic";
  const submodeBar = $("classicSubmodesBar");
  if (submodeBar) {
    // Another engine greys these out; it does not take them off the bar.
    const isClassic = provider === "classic";
    submodeBar.classList.remove("hidden");
    submodeBar.classList.toggle("is-disabled", !isClassic);
    submodeBar.querySelectorAll(".submode-btn").forEach((button) => {
      button.disabled = !isClassic;
    });
  }
  const currentSubmode = state.settings.CLASSIC_SUBMODE || "auto";
  document.querySelectorAll(".submode-btn").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.submode === currentSubmode);
  });

}

export function initClassicSubmodeButtons() {
  document.querySelectorAll(".submode-btn").forEach((btn) => {
    btn.onclick = async () => {
      if (state.busy) return;
      editor.exitAllDetectionModes();
      const submode = btn.dataset.submode;
      state.settings.CLASSIC_SUBMODE = submode;
      updateClassicSubmodes();
      showProvider("classic");
      try {
        await api("/api/admin/settings/detection", {
          method: "PUT",
          body: JSON.stringify({
            DETECTION_PROVIDER: "classic",
            CLASSIC_SUBMODE: submode
          })
        });
      } catch (_e) {}
    };
  });
}

export function showProvider(provider) {
  state.settings.DETECTION_PROVIDER = provider;
  document.querySelectorAll(".provider-card").forEach((card) => {
    card.classList.toggle("selected", card.dataset.provider === provider);
  });
  updateDetectionModeSwitch();
  if ($("vertexConfig")) $("vertexConfig").classList.toggle("hidden", provider !== "vertex");
  if ($("localConfig")) $("localConfig").classList.toggle("hidden", provider !== "local");
  if ($("classicConfig")) $("classicConfig").classList.toggle("hidden", provider !== "classic");
  if ($("engineProvider")) $("engineProvider").textContent = providerTitle(provider);
  if ($("engineModel")) {
    const vModel = ($("vertexModel") && $("vertexModel").value) || "gemini-2.5-flash";
    const vLoc = ($("vertexLocation") && $("vertexLocation").value) || "global";
    const lModel = ($("localModel") && $("localModel").value) || "Choose an installed model";
    const sub = state.settings.CLASSIC_SUBMODE || "auto";
    const subTitle = sub === "frame_points" ? "Frame Points"
      : sub === "green_frames" ? "Green Frames"
      : sub === "color_pick" ? "Color Pick"
      : sub === "none" ? "None" : "Auto Detect";
    $("engineModel").textContent = provider === "vertex"
      ? `${vModel} / ${vLoc}`
      : provider === "local" ? lModel
      : `Classic (${subTitle})`;
  }
}

export async function switchDetectionProvider(provider) {
  if (state.busy || state.switchingProvider) return;
  if (provider === "vertex" && state.providerHealth && state.providerHealth.vertex && !state.providerHealth.vertex.available) {
    const err = state.providerHealth.vertex.error || "Vertex AI is not configured or unreachable.";
    toast(`Cannot select Vertex AI: ${err}`);
    setStatus("Vertex AI is unavailable", true);
    return;
  }
  if (provider === (state.settings.DETECTION_PROVIDER || "classic")) return;
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
    `<option value="${escapeAttr(model.id)}">${escapeHtml(model.label)}${model.stage ? ` / ${escapeHtml(model.stage)}` : ""}</option>`
  ).join("");
  if (!models.length) {
    select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>`;
  } else if (selected && !models.some((model) => model.id === selected)) {
    select.insertAdjacentHTML("beforeend", `<option value="${escapeAttr(selected)}">${escapeHtml(selected)} / previously configured</option>`);
  }
  if (selected) select.value = selected;
}

async function loadVertexModels(selected) {
  try {
    const payload = await api("/api/admin/settings/detection/models?provider=vertex");
    if ($("vertexModel")) fillModels($("vertexModel"), payload.models, selected, "Select a detection model");
    if ($("vertexModelNotice")) {
      $("vertexModelNotice").textContent = payload.source === "fallback"
        ? "Using recommended Gemini detection models."
        : "Live Gemini detection models loaded.";
    }
  } catch (error) {
    if ($("vertexModel")) fillModels($("vertexModel"), [], selected, "Unavailable");
    if ($("vertexModelNotice")) $("vertexModelNotice").textContent = error.message;
  }
}

export async function loadLocalModels(showFeedback = false) {
  const endpoint = $("localUrl") ? $("localUrl").value.trim() : "";
  if (!endpoint) {
    if ($("localModel")) fillModels($("localModel"), [], "", "Enter an endpoint to list models");
    if ($("localModelNotice")) $("localModelNotice").textContent = "Enter the local detector endpoint, then load models installed on that service.";
    return;
  }
  try {
    const query = `/api/admin/settings/detection/models?provider=local&endpoint=${encodeURIComponent(endpoint)}`;
    const payload = await api(query);
    if ($("localModel")) {
      fillModels(
        $("localModel"),
        payload.models,
        state.settings.LOCAL_DETECTION_MODEL || "",
        "No installed models reported by this endpoint"
      );
    }
    if ($("localModelNotice")) {
      $("localModelNotice").textContent = payload.models.length
        ? `${payload.models.length} installed model(s) reported by the local service.`
        : "The endpoint did not expose an OpenAI-compatible or Ollama model list.";
    }
    if (showFeedback) toast(payload.models.length ? "Installed local models loaded" : "No local models were reported");
  } catch (error) {
    if ($("localModelNotice")) $("localModelNotice").textContent = error.message;
    if (showFeedback) toast(error.message);
  }
}

export async function loadSettings() {
  try {
    const payload = await api("/api/admin/settings/detection");
    state.settings = (payload && payload.settings) || {};
  } catch (e) {
    console.warn("Could not fetch detection settings, using defaults:", e);
    state.settings = state.settings || {};
  }

  if ($("vertexProject")) $("vertexProject").value = state.settings.VERTEX_PROJECT_ID || "";
  if ($("vertexLocation")) $("vertexLocation").value = state.settings.VERTEX_LOCATION || "global";
  if ($("vertexResolution")) $("vertexResolution").value = state.settings.VERTEX_MEDIA_RESOLUTION || "high";
  if ($("refinementMode")) $("refinementMode").value = state.settings.DETECTION_REFINEMENT || "ai_only";
  if ($("classicBlurSize")) $("classicBlurSize").value = state.settings.CLASSIC_BLUR_SIZE || "3";
  if ($("classicSearchRadius")) $("classicSearchRadius").value = state.settings.CLASSIC_SEARCH_RADIUS || "20";
  if ($("classicImportMode")) $("classicImportMode").value = state.settings.CLASSIC_IMPORT_MODE || "auto";
  if ($("classicGreenEdgeExpand")) $("classicGreenEdgeExpand").value = state.settings.CLASSIC_GREEN_EDGE_EXPAND || "0";
  if ($("classicGreenTolerance")) $("classicGreenTolerance").value = state.settings.CLASSIC_GREEN_TOLERANCE || "130";
  if ($("localUrl")) $("localUrl").value = state.settings.LOCAL_DETECTION_URL || "";
  
  showProvider(state.settings.DETECTION_PROVIDER || "classic");

  // Asynchronous background calls that don't block workspace loading
  loadVertexModels(state.settings.VERTEX_MODEL || "gemini-2.5-flash").catch(console.warn);
  loadLocalModels(false).catch(console.warn);
  checkProviderHealth().catch(console.warn);
}

export async function saveSettings(showFeedback = true) {
  try {
    if ($("vertexModel").value === "gemini-3-flash-preview") $("vertexLocation").value = "global";
    // What runs when a mockup is added, which is not the mode the studio is
    // working in: the top bar owns that, and this panel must not move it.
    const importMode = ($("classicImportMode") && $("classicImportMode").value) || "auto";
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
        CLASSIC_IMPORT_MODE: importMode,
        CLASSIC_GREEN_EDGE_EXPAND: $("classicGreenEdgeExpand") ? $("classicGreenEdgeExpand").value : "0",
        CLASSIC_GREEN_TOLERANCE: $("classicGreenTolerance") ? $("classicGreenTolerance").value : "130",
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

export async function testEngine() {
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
      editor.renderEditor();
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

