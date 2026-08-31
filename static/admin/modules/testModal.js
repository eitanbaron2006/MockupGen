/** The Test mockups window: try artwork against real templates before publishing.
 *
 * It keeps its own state -- the images dropped into it, which mockups are
 * ticked, and the renders it has already produced -- and shares only the
 * studio's own state object with the editor behind it. Importing this file
 * wires the window up; the two render functions are exported because the
 * editor redraws its lists when a template changes underneath them.
 */
import { escapeAttr, escapeHtml } from "./helpers.js";
import { $, toast } from "./dom.js";
import { api, csrfHeaders } from "./api.js";
import { state, testState } from "./state.js";
import { availableListingSets } from "./listingSets.js";

// Test Mockups Modal Logic
$("openTestModal").onclick = () => {
  if (state.busy) return;
  $("testModal").classList.add("open");
  renderTestGallery();
  renderMockupGallery();
  loadListingSetChoices();
  if (testState.activeIndex === -1) {
    resetTestResult();
  }
};
$("closeTestModal").onclick = () => $("testModal").classList.remove("open");
$("testModal").onclick = (event) => {
  if (event.target === $("testModal")) $("testModal").classList.remove("open");
};

// Lightbox Preview Handlers
// The lightbox holds a gallery of the completed results currently on screen,
// so the user can browse between them with arrows without closing it.
const lightboxState = { items: [], index: 0 };

function renderLightboxItem() {
  const img = $("lightboxImage");
  const caption = $("lightboxCaption");
  const counter = $("lightboxCounter");
  const item = lightboxState.items[lightboxState.index];
  if (!img || !item) return;
  img.src = item.src;
  if (caption) caption.textContent = item.title || "Mockup Preview";
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

function showLightbox(src, title, items) {
  const overlay = $("lightboxOverlay");
  if (!overlay || !$("lightboxImage")) return;
  lightboxState.items = (items && items.length ? items : [{ src, title }]);
  const startIndex = lightboxState.items.findIndex((item) => item.src === src);
  lightboxState.index = startIndex === -1 ? 0 : startIndex;
  renderLightboxItem();
  overlay.classList.remove("hidden");
}

function hideLightbox() {
  const overlay = $("lightboxOverlay");
  if (overlay) {
    overlay.classList.add("hidden");
  }
}

// All currently visible completed results, in display order (batch grid
// cards that succeeded, or the single result preview).
function collectLightboxItems() {
  const items = [];
  document.querySelectorAll("#testBatchResults .batch-result-card.success .batch-card-img:not(.hidden)").forEach((img) => {
    if (!img.src) return;
    const card = img.closest(".batch-result-card");
    items.push({
      src: img.src,
      title: card?.querySelector(".batch-card-title")?.textContent || "Generated Mockup"
    });
  });
  const singleImg = $("testResultImage");
  const singleVisible = singleImg && singleImg.src && !$("testResultWrapper").classList.contains("hidden");
  if (singleVisible) {
    const templateSelect = $("testTemplateSelect");
    items.push({
      src: singleImg.src,
      title: templateSelect ? templateSelect.options[templateSelect.selectedIndex]?.text : "Generated Mockup"
    });
  }
  return items;
}

if ($("closeLightboxBtn")) {
  $("closeLightboxBtn").onclick = hideLightbox;
}
if ($("lightboxPrevBtn")) {
  $("lightboxPrevBtn").onclick = (e) => {
    e.stopPropagation();
    stepLightbox(-1);
  };
}
if ($("lightboxNextBtn")) {
  $("lightboxNextBtn").onclick = (e) => {
    e.stopPropagation();
    stepLightbox(1);
  };
}
if ($("lightboxOverlay")) {
  $("lightboxOverlay").onclick = (e) => {
    if (e.target === $("lightboxOverlay") || e.target === $("closeLightboxBtn")) {
      hideLightbox();
    }
  };
}
document.addEventListener("keydown", (e) => {
  const overlay = $("lightboxOverlay");
  if (!overlay || overlay.classList.contains("hidden")) return;
  if (e.key === "ArrowLeft") {
    e.preventDefault();
    stepLightbox(-1);
  } else if (e.key === "ArrowRight") {
    e.preventDefault();
    stepLightbox(1);
  } else if (e.key === "Escape") {
    e.preventDefault();
    hideLightbox();
  }
});

// Hook single preview click
if ($("testResultImage")) {
  $("testResultImage").onclick = () => {
    const src = $("testResultImage").src;
    const templateSelect = $("testTemplateSelect");
    const title = templateSelect ? templateSelect.options[templateSelect.selectedIndex]?.text : "Generated Mockup";
    showLightbox(src, title, collectLightboxItems());
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
      showLightbox(src, title, collectLightboxItems());
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

  // A freshly added artwork becomes the active one, which also resets any
  // previous results so the screen behaves like a first-time generation.
  selectTestImage(testState.files.length - newFiles.length);
}

export function renderTestGallery() {
  const gallery = $("testGallery");
  // A listing set needs artwork and nothing else, so its button follows the
  // gallery rather than the template ticks that gate Generate.
  syncListingSetButton(false);
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
  const removedUrl = testState.files[index].url;
  URL.revokeObjectURL(removedUrl);
  testState.files.splice(index, 1);
  for (const key of Array.from(testState.renderCache.keys())) {
    if (key.startsWith(`${removedUrl}|`)) testState.renderCache.delete(key);
  }

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

export function renderMockupGallery() {
  const gallery = $("testMockupGallery");
  if (testState.templates.length === 0) {
    gallery.innerHTML = `<div class="gallery-empty">No matching mockups found</div>`;
    return;
  }

  gallery.innerHTML = testState.templates.map((t) => {
    const previewUrl = t.preview_url || `/api/admin/templates/${t.template_id}/asset/preview.png`;
    const isSelected = testState.selectedTemplates.has(t.template_id);
    return `
      <div class="test-mockup-card-wrapper ${isSelected ? 'selected' : ''}" data-id="${t.template_id}" title="${escapeAttr(t.name)}">
        <img src="${previewUrl}" class="test-mockup-card-img" alt="${escapeAttr(t.name)}">
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

function testRenderCacheKey(activeFile, templateId, renderMode, aiModel, fitMode) {
  const variant = renderMode === "ai" ? (aiModel || "") : (fitMode || "");
  return `${activeFile.url}|${templateId}|${renderMode}|${variant}`;
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

    // Stale batch results from a previous run must not linger below the
    // single-result area while (or after) generating.
    $("testBatchResults").classList.add("hidden");
    $("testBatchResults").innerHTML = "";

    const cacheKey = testRenderCacheKey(activeFile, templateId, renderMode, aiModel, fitMode);
    const cachedUrl = testState.renderCache.get(cacheKey);
    if (cachedUrl) {
      $("testResultImage").src = cachedUrl;
      $("testResultDownload").href = cachedUrl;
      $("testResultLoading").classList.add("hidden");
      $("testResultPlaceholder").classList.add("hidden");
      $("testResultWrapper").classList.remove("hidden");
      $("testResultActions").classList.remove("hidden");
      return;
    }

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
        headers: csrfHeaders(),
        body: formData,
        signal: controller.signal
      });
      clearTimeout(timeoutId);

      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Generation failed");

      testState.renderCache.set(cacheKey, data.output_url);
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
    syncDownloadAllButton();

    // Populate placeholder loader cards for each selected mockup
    batchContainer.innerHTML = selectedIds.map(templateId => {
      const t = testState.templates.find(x => x.template_id === templateId);
      const name = t ? (t.name || templateId) : templateId;
      return `
        <div class="batch-result-card" id="batch-card-${templateId}">
          <div class="batch-card-header">
            <span class="batch-card-title" title="${escapeAttr(name)}">${escapeHtml(name)}</span>
            <span class="batch-card-status" id="batch-status-${templateId}">Pending...</span>
          </div>
          <div class="batch-card-body">
            <div class="batch-card-spinner" id="batch-spinner-${templateId}"></div>
            <img class="batch-card-img hidden" id="batch-img-${templateId}" alt="${escapeAttr(name)}">
          </div>
          <div class="batch-card-actions hidden" id="batch-actions-${templateId}">
            <a class="btn primary" id="batch-download-${templateId}" download="mockup_${templateId}.png" href="#">Download</a>
          </div>
        </div>
      `;
    }).join('');

    const markBatchCardReady = (templateId, outputUrl) => {
      const cardElement = $(`batch-card-${templateId}`);
      if (!cardElement) return;
      cardElement.classList.add("success");
      const statusElement = $(`batch-status-${templateId}`);
      if (statusElement) statusElement.textContent = "Ready";
      const spinnerElement = $(`batch-spinner-${templateId}`);
      if (spinnerElement) spinnerElement.remove();
      const imgElement = $(`batch-img-${templateId}`);
      if (imgElement) {
        imgElement.src = outputUrl;
        imgElement.classList.remove("hidden");
      }
      const actionsElement = $(`batch-actions-${templateId}`);
      if (actionsElement) {
        actionsElement.innerHTML = `<a class="btn primary" id="batch-download-${templateId}" download="mockup_${templateId}.png" href="${outputUrl}">Download</a>`;
        actionsElement.classList.remove("hidden");
      }
      // A batch made entirely of renders already in hand still gets the offer.
      syncDownloadAllButton();
    };

    // Reuse results that were already generated for this artwork+settings;
    // only the newly added/missing mockups are actually rendered.
    const pendingIds = [];
    selectedIds.forEach(templateId => {
      const cachedUrl = testState.renderCache.get(
        testRenderCacheKey(activeFile, templateId, renderMode, aiModel, fitMode)
      );
      if (cachedUrl) {
        markBatchCardReady(templateId, cachedUrl);
      } else {
        pendingIds.push(templateId);
      }
    });

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
            headers: csrfHeaders(),
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
          testState.renderCache.set(
            testRenderCacheKey(activeFile, templateId, renderMode, aiModel, fitMode),
            data.output_url
          );
          cardElement.classList.add("success");
          statusElement.textContent = "Ready";
          if (spinnerElement) spinnerElement.remove();

          imgElement.src = data.output_url;
          imgElement.classList.remove("hidden");

          // Dynamically inject the Download button
          actionsElement.innerHTML = `<a class="btn primary" id="batch-download-${templateId}" download="mockup_${templateId}.png" href="${data.output_url}">Download</a>`;
          actionsElement.classList.remove("hidden");
          // One more finished render: offer to take them all.
          syncDownloadAllButton();
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
      for (const templateId of pendingIds) {
        await renderBatchItem(templateId);
      }
    } else {
      // Simple Mode: Process concurrently in parallel for maximum local CPU speed
      const promises = pendingIds.map(templateId => renderBatchItem(templateId));
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


/** Every finished mockup in the result column, as one .zip.
 *
 * The server holds the renders already, so the browser asks it to bundle them
 * rather than fetching each one back and zipping them here. Only the renders
 * it can name are sent: a preview that never reached disk has no file to
 * archive.
 */
function readyRenderNames() {
  return [...document.querySelectorAll("#testBatchResults .batch-card-img")]
    .map((image) => image.getAttribute("src") || "")
    .filter((source) => source && !source.startsWith("data:"))
    .map((source) => source.split("?")[0]);
}

export function syncDownloadAllButton() {
  const button = $("testDownloadAll");
  if (!button) return;
  button.classList.toggle("hidden", readyRenderNames().length < 2);
}

if ($("testDownloadAll")) {
  $("testDownloadAll").onclick = async () => {
    const outputs = readyRenderNames();
    if (!outputs.length) return;
    const button = $("testDownloadAll");
    const label = button.textContent;
    button.disabled = true;
    button.textContent = "Preparing...";
    try {
      const response = await fetch("/api/mockups/outputs/archive", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ outputs, name: "mockups.zip" }),
      });
      if (!response.ok) {
        const problem = await response.json().catch(() => ({}));
        throw new Error(problem.error || "The archive could not be built");
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = "mockups.zip";
      document.body.appendChild(link);
      link.click();
      link.remove();
      // The browser has the bytes now; the object URL has nothing left to hold.
      setTimeout(() => URL.revokeObjectURL(url), 10000);
    } catch (error) {
      toast(error.message);
    } finally {
      button.disabled = false;
      button.textContent = label;
    }
  };
}


/** A whole listing's worth of images, from the artwork already on screen.
 *
 * Everything else in this window renders templates the user ticked by hand.
 * This asks the server for the set a shop listing needs -- the hero shot, a
 * close-up of it, the piece in a second room, and a size chart -- and lets it
 * choose the templates, so nothing has to be ticked at all. The results land
 * in the same grid as a batch, which means the lightbox and "Download all"
 * pick them up without knowing anything about listing sets.
 */
// Four pictures is what the automatic listing comes to when no set is named.
const AUTOMATIC_PICTURES = 4;

/** The saved sets that suit the artwork on screen, offered beside the button. */
async function loadListingSetChoices() {
  const select = $("testListingSetSelect");
  if (!select) return;
  const activeFile = testState.files[testState.activeIndex];
  const sets = await availableListingSets(activeFile?.orientation || "");
  const current = select.value;
  select.innerHTML = ['<option value="">Automatic set</option>']
    .concat(sets.map((entry) => `<option value="${entry.id}">${escapeHtml(entry.name)}</option>`))
    .join("");
  select.value = sets.some((entry) => String(entry.id) === current) ? current : "";
}

function syncListingSetButton(busy) {
  const button = $("testListingSetButton");
  if (!button) return;
  button.disabled = Boolean(busy) || !testState.files[testState.activeIndex];
}

function placeholderCards(titles) {
  return titles.map((title, index) => `
    <div class="batch-result-card" id="listing-card-${index}">
      <div class="batch-card-header">
        <span class="batch-card-title">${escapeHtml(title)}</span>
        <span class="batch-card-status">Pending...</span>
      </div>
      <div class="batch-card-body"><div class="batch-card-spinner"></div></div>
    </div>
  `).join("");
}

function renderListingCard(item, position) {
  const card = $(`listing-card-${position}`);
  if (!card) return;
  const title = item.label || item.kind;
  if (!item.success) {
    card.classList.add("error");
    card.innerHTML = `
      <div class="batch-card-header">
        <span class="batch-card-title">${escapeHtml(title)}</span>
        <span class="batch-card-status">Failed</span>
      </div>
      <div class="batch-card-body">
        <div class="batch-error-message">${escapeHtml(item.error || "Could not be built")}</div>
      </div>
    `;
    return;
  }
  // The status line carries what the picture is of: which template it used, or
  // which chart the library answered with. The server sends template ids; the
  // name is what the user recognises, so it is used wherever it is known.
  const template = testState.templates.find((entry) => entry.template_id === item.template_id);
  const status = item.kind === "size_guide"
    ? (item.guide_name || item.size_family || "Ready")
    : (template?.name || item.template_id || "Ready");
  card.classList.add("success");
  card.innerHTML = `
    <div class="batch-card-header">
      <span class="batch-card-title">${escapeHtml(title)}</span>
      <span class="batch-card-status" title="${escapeAttr(status)}">${escapeHtml(status)}</span>
    </div>
    <div class="batch-card-body">
      <img class="batch-card-img" src="${escapeAttr(item.output_url)}" alt="${escapeAttr(title)}">
    </div>
    <div class="batch-card-actions">
      <a class="btn primary" download="listing-${position + 1}.jpg"
         href="${escapeAttr(item.output_url)}">Download</a>
    </div>
  `;
}

if ($("testListingSetButton")) {
  $("testListingSetButton").onclick = async () => {
    const activeFile = testState.files[testState.activeIndex];
    if (!activeFile) return;
    const button = $("testListingSetButton");
    const label = button.textContent;
    syncListingSetButton(true);
    button.textContent = "Building set...";

    // The set takes over the result column the way a batch does.
    $("testResultPlaceholder").classList.add("hidden");
    $("testResultWrapper").classList.add("hidden");
    $("testResultActions").classList.add("hidden");
    $("testResultLoading").classList.add("hidden");
    const container = $("testBatchResults");
    container.classList.remove("hidden");
    // A saved set decides how many pictures there are and what they are
    // called; the automatic fallback is the four-picture listing.
    const chosenSet = $("testListingSetSelect")?.value || "";
    const planned = chosenSet
      ? (await availableListingSets()).find((entry) => String(entry.id) === chosenSet)?.items || []
      : [];
    // Each picture is named after the mockup it comes from, which only the
    // answer knows; until then the cards are numbered.
    const pending = chosenSet ? Math.max(planned.length, 1) : AUTOMATIC_PICTURES;
    container.innerHTML = placeholderCards(
      Array.from({ length: pending }, (_value, index) => `Image ${index + 1}`)
    );
    syncDownloadAllButton();

    const formData = new FormData();
    formData.append("artwork", activeFile.file);
    // JPEG at 92: these are photographs headed for a shop listing, where a
    // lossless copy only costs the seller upload time.
    const spec = { format: "jpeg", quality: 92 };
    if (chosenSet) spec.set = Number(chosenSet);
    formData.append("spec", JSON.stringify(spec));

    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 120000);
    try {
      const response = await fetch("/api/mockups/listing-bundle", {
        method: "POST",
        headers: csrfHeaders(),
        body: formData,
        signal: controller.signal,
      });
      clearTimeout(timeoutId);
      const data = await response.json();
      // 207 means some of the set came back, which is still worth showing.
      if (!response.ok && response.status !== 207) {
        throw new Error(data.error || "The listing set could not be built");
      }
      const items = data.items || [];
      // A set that draws several mockups from one category answers with more
      // pictures than it has rows, so the cards follow the answer.
      // A set that draws several mockups from one category answers with more
      // pictures than it has rows, so the cards follow the answer.
      if (items.length !== pending) {
        container.innerHTML = placeholderCards(items.map((item, index) => item.label || `Image ${index + 1}`));
      }
      items.forEach((item, index) => renderListingCard(item, index));
      const failed = items.filter((item) => !item.success).length;
      if (failed) toast(`${failed} of ${items.length} images could not be built`);
      syncDownloadAllButton();
    } catch (error) {
      clearTimeout(timeoutId);
      toast(error.name === "AbortError" ? "Request timed out (120s limit)" : error.message);
      container.classList.add("hidden");
      container.innerHTML = "";
      $("testResultPlaceholder").classList.remove("hidden");
      syncDownloadAllButton();
    } finally {
      button.textContent = label;
      syncListingSetButton(false);
    }
  };
}
