(() => {
  const csrf = document.querySelector('meta[name="csrf-token"]').content;
  const state = {
    categories: [],
    templates: [],
    selectedCategory: null,
    selected: null,
    settings: {},
    busy: false,
    drag: null,
    pendingDelete: null,
    queueFilter: "review",
    selectedForBatch: new Set()
  };
  const testState = {
    files: [],
    activeIndex: -1,
    templates: []
  };
  const $ = (id) => document.getElementById(id);

  async function api(url, options = {}) {
    const headers = {...(options.headers || {}), "X-CSRF-Token": csrf};
    if (options.body && !(options.body instanceof FormData)) headers["Content-Type"] = "application/json";
    const response = await fetch(url, {...options, headers});
    let payload;
    try {
      payload = await response.json();
    } catch (_error) {
      payload = {error: "The server returned an unreadable response."};
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
        state.selectedCategory = state.categories.find((category) => category.id === Number(button.dataset.category));
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
        <button class="queue-delete" type="button" data-template="${template.template_id}" aria-label="Delete ${escapeHtml(template.name)}" title="Delete mockup" ${state.busy ? "disabled" : ""}>&times;</button>
      </div>
    `).join("") || '<div class="empty">No templates match this filter.</div>';
    document.querySelectorAll(".queue-select").forEach((button) => {
      button.onclick = () => {
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

  function orientationTitle(value) {
    return value === "landscape" ? "Wide" : value.charAt(0).toUpperCase() + value.slice(1);
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
      $("currentTitle").textContent = "Select a mockup";
      $("confidence").textContent = "";
      $("coordX").textContent = "-";
      $("coordY").textContent = "-";
      $("coordW").textContent = "-";
      $("coordH").textContent = "-";
      return;
    }
    $("currentTitle").textContent = template.name;
    $("editorSub").textContent = template.status === "active"
      ? "Published template. Detection changes remain proposals until approved again."
      : "Draft template. Review the proposal before approval.";
    $("inspectorStatus").textContent = template.status === "active" ? "Approved template" : "Awaiting approval";
    $("proposalState").textContent = template.status === "active"
      ? "Approved rectangle is active. Run Detect frame to compare safely."
      : "Detect frame or adjust the artwork area before approval.";
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
    if (template.detection_provider) {
      const confidence = template.detection_confidence == null ? "" : ` / ${Math.round(template.detection_confidence * 100)}% confidence`;
      $("confidence").textContent = `Detection proposal${confidence}`;
    } else {
      $("confidence").textContent = "Manual selection";
    }
    updateCoordinateLabels();
    $("canvasImage").onload = drawSelection;
    drawSelection();
  }

  function updateCoordinateLabels() {
    const area = state.selected && state.selected.artwork_area;
    $("coordX").textContent = area ? area.x : "-";
    $("coordY").textContent = area ? area.y : "-";
    $("coordW").textContent = area ? area.width : "-";
    $("coordH").textContent = area ? area.height : "-";
  }

  function drawSelection() {
    const template = state.selected;
    const image = $("canvasImage");
    const selectionSvg = $("selectionSvg");
    if (!template || !template.artwork_area || !image.naturalWidth) {
      selectionSvg.classList.add("hidden");
      return;
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
    const clientWidth = image.clientWidth;
    const clientHeight = image.clientHeight;
    
    // Map canvas coordinates to client display coordinates
    const pointsStr = corners.map(p => {
      const cx = (p.x / template.canvas_width) * clientWidth;
      const cy = (p.y / template.canvas_height) * clientHeight;
      return `${cx},${cy}`;
    }).join(" ");
    
    $("selectionPolygon").setAttribute("points", pointsStr);
    
    // Position handles
    corners.forEach((p, idx) => {
      const cx = (p.x / template.canvas_width) * clientWidth;
      const cy = (p.y / template.canvas_height) * clientHeight;
      const handle = $(`handle_${idx}`);
      if (handle) {
        handle.setAttribute("cx", cx);
        handle.setAttribute("cy", cy);
      }
    });
    
    // Position the text tag slightly above or near the first corner
    if (corners.length > 0) {
      const tX = (corners[0].x / template.canvas_width) * clientWidth;
      const tY = (corners[0].y / template.canvas_height) * clientHeight - 10;
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
      corners: state.selected.artwork_area.corners.map(c => ({...c})),
      handle: handle,
      cornerIndex: index
    };
  }

  function continueDrag(event) {
    if (!state.drag || !state.selected) return;
    const template = state.selected;
    const image = $("canvasImage");
    const dx = Math.round((event.clientX - state.drag.startX) * template.canvas_width / image.clientWidth);
    const dy = Math.round((event.clientY - state.drag.startY) * template.canvas_height / image.clientHeight);
    
    let nextCorners = state.drag.corners.map(c => ({...c}));
    
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
      const payload = await api("/api/admin/templates/import", {method: "POST", body});
      await loadCategories(state.selectedCategory.id);
      await loadTemplates(payload.templates[0].template_id);
      toast(`${payload.templates.length} mockup images imported`);
      setStatus("Import complete");
    } catch (error) {
      setStatus("Import failed", true);
      toast(error.message);
    }
  }

  async function saveTemplate(showToast = true) {
    if (!state.selected || !state.selected.artwork_area) throw new Error("Define an artwork area first.");
    const payload = await api(`/api/admin/templates/${state.selected.template_id}`, {
      method: "PATCH",
      body: JSON.stringify({
        name: $("templateName").value,
        category_id: Number($("categorySelect").value),
        artwork_area: state.selected.artwork_area,
        fit_mode: $("fitMode").value
      })
    });
    state.selected = payload.template;
    await loadCategories(state.selected.category_id);
    await loadTemplates(state.selected.template_id);
    if (showToast) toast("Template draft saved");
    return state.selected;
  }

  async function approveTemplate() {
    if (!state.selected) return;
    try {
      setStatus("Publishing approved template...");
      await saveTemplate(false);
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/activate`, {method: "POST"});
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
      await api(`/api/admin/templates/${template.template_id}`, {method: "DELETE"});
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
    setBusy(true);
    const engineName = providerTitle(state.settings.DETECTION_PROVIDER || "classic");
    $("analysisLabel").textContent = `${engineName} is analyzing the frame`;
    setStatus(`${engineName} is detecting the artwork area...`);
    $("proposalState").textContent = "Analyzing the selected background image...";
    $("detectionResult").className = "rule result-rule";
    try {
      const payload = await api(`/api/admin/templates/${state.selected.template_id}/detect`, {method: "POST"});
      state.selected = payload.template;
      const confidence = payload.proposal.confidence == null ? "" : ` (${Math.round(payload.proposal.confidence * 100)}%)`;
      $("confidence").textContent = `Detection proposal / ${payload.proposal.provider}${confidence}`;
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

  function showProvider(provider) {
    state.settings.DETECTION_PROVIDER = provider;
    document.querySelectorAll(".provider-card").forEach((card) => {
      card.classList.toggle("selected", card.dataset.provider === provider);
    });
    $("vertexConfig").classList.toggle("hidden", provider !== "vertex");
    $("localConfig").classList.toggle("hidden", provider !== "local");
    $("classicConfig").classList.toggle("hidden", provider !== "classic");
    $("engineProvider").textContent = providerTitle(provider);
    $("engineModel").textContent = provider === "vertex"
      ? `${$("vertexModel").value || "gemini-2.5-flash"} / ${$("vertexLocation").value || "global"}`
      : provider === "local" ? ($("localModel").value || "Choose an installed model") : "No AI model";
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
    $("vertexAuth").value = state.settings.VERTEX_AUTH_MODE || "adc";
    $("refinementMode").value = state.settings.DETECTION_REFINEMENT || "ai_only";
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
          LOCAL_DETECTION_URL: $("localUrl").value,
          LOCAL_DETECTION_MODEL: $("localModel").value
        })
      });
      state.settings = {...state.settings, ...payload.settings};
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
        body: JSON.stringify({template_id: state.selected.template_id})
      });
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
      state.queueFilter = pill.dataset.filter;
      renderQueue();
    };
  });
  $("openCategory").onclick = () => $("categoryModal").classList.add("open");
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
        body: JSON.stringify({name: $("newCategory").value})
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
  $("browse").onclick = () => $("fileInput").click();
  $("fileInput").onchange = async (event) => {
    await importFiles(event.target.files);
    event.target.value = "";
  };
  ["dragenter", "dragover"].forEach((eventName) => $("dropzone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("dropzone").classList.add("drag");
  }));
  ["dragleave", "drop"].forEach((eventName) => $("dropzone").addEventListener(eventName, (event) => {
    event.preventDefault();
    $("dropzone").classList.remove("drag");
  }));
  $("dropzone").addEventListener("drop", (event) => importFiles(event.dataTransfer.files));
  $("selectionSvg").addEventListener("pointerdown", beginDrag);
  $("selectionSvg").addEventListener("pointermove", continueDrag);
  $("selectionSvg").addEventListener("pointerup", endDrag);
  $("selectionSvg").addEventListener("pointercancel", endDrag);
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
  $("openGuide").onclick = () => $("guideDrawer").classList.add("open");
  $("closeGuide").onclick = () => $("guideDrawer").classList.remove("open");
  const openEngine = () => $("engineDrawer").classList.add("open");
  $("openEngine").onclick = openEngine;
  $("engineButton").onclick = openEngine;
  $("editEngine").onclick = openEngine;
  $("closeEngine").onclick = () => $("engineDrawer").classList.remove("open");

  // Test Mockups Modal Logic
  $("openTestModal").onclick = () => {
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
      $("testArtworkPreview").src = "";
      $("testArtworkPreview").classList.add("hidden");
      $("testUploadPlaceholder").classList.remove("hidden");
      $("testOrientationLabel").textContent = "";
      $("testMockupGallery").innerHTML = `<div class="gallery-empty">Upload artwork to load matching templates</div>`;
      $("testTemplateSelect").innerHTML = `<option value="">Upload an image first</option>`;
      $("testTemplateSelect").disabled = true;
      $("testGenerateButton").disabled = true;
      
      // Reset mockup preview
      $("testMockupPreview").src = "";
      $("testMockupPreview").classList.add("hidden");
      $("testMockupPlaceholder").classList.remove("hidden");
      
      resetTestResult();
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
    
    const selectValue = $("testTemplateSelect").value;
    gallery.innerHTML = testState.templates.map((t) => {
      const previewUrl = t.preview_url || `/api/admin/templates/${t.template_id}/asset/preview.png`;
      const isActive = t.template_id === selectValue;
      return `
        <img src="${previewUrl}" class="test-mockup-card ${isActive ? 'active' : ''}" data-id="${t.template_id}" title="${escapeHtml(t.name)}" alt="${escapeHtml(t.name)}">
      `;
    }).join('');
    
    gallery.querySelectorAll('.test-mockup-card').forEach(img => {
      img.onclick = (e) => {
        e.stopPropagation();
        selectMockupTemplate(img.dataset.id);
      };
    });
  }

  function selectMockupTemplate(templateId) {
    const select = $("testTemplateSelect");
    select.value = templateId;
    
    const activeFile = testState.files[testState.activeIndex];
    if (activeFile && activeFile.orientation && templateId) {
      $("testGenerateButton").disabled = false;
    }
    
    // Render mockup preview
    const t = testState.templates.find(x => x.template_id === templateId);
    if (t) {
      const previewUrl = t.preview_url || `/api/admin/templates/${t.template_id}/asset/preview.png`;
      $("testMockupPreview").src = previewUrl;
      $("testMockupPreview").classList.remove("hidden");
      $("testMockupPlaceholder").classList.add("hidden");
    }
    
    renderMockupGallery();
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
    
    if (!activeFile.orientation) {
      $("testOrientationLabel").textContent = "Detecting orientation...";
      $("testTemplateSelect").innerHTML = `<option value="">Detecting orientation...</option>`;
      $("testTemplateSelect").disabled = true;
      $("testGenerateButton").disabled = true;
      $("testMockupGallery").innerHTML = `<div class="gallery-empty">Detecting orientation...</div>`;
      
      // Reset mockup preview
      $("testMockupPreview").src = "";
      $("testMockupPreview").classList.add("hidden");
      $("testMockupPlaceholder").classList.remove("hidden");
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
        
        select.value = activeTemplateId;
        select.disabled = false;
        $("testGenerateButton").disabled = false;
        
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
    if (testState.activeIndex === -1 || !$("testTemplateSelect").value) return;
    $("testGenerateButton").disabled = true;
    $("testGenerateButton").textContent = "Generating...";
    
    const activeFile = testState.files[testState.activeIndex];
    const formData = new FormData();
    formData.append("mode", "simple");
    formData.append("template_id", $("testTemplateSelect").value);
    formData.append("artwork", activeFile.file);
    
    const fitMode = $("testFitMode").value;
    if (fitMode) {
      formData.append("fit_mode", fitMode);
    }
    
    try {
      const response = await fetch("/api/mockups/render", {
        method: "POST",
        headers: { "X-CSRF-Token": csrf },
        body: formData
      });
      const data = await response.json();
      if (!response.ok) throw new Error(data.error || "Generation failed");
      
      $("testResultImage").src = data.output_url;
      $("testResultDownload").href = data.output_url;
      
      $("testResultPlaceholder").classList.add("hidden");
      $("testResultWrapper").classList.remove("hidden");
      $("testResultActions").classList.remove("hidden");
    } catch (err) {
      toast(err.message);
    } finally {
      $("testGenerateButton").disabled = false;
      $("testGenerateButton").textContent = "Generate";
    }
  };
  document.querySelectorAll(".provider-card").forEach((card) => {
    card.onclick = () => showProvider(card.dataset.provider);
  });
  $("vertexModel").onchange = () => {
    if ($("vertexModel").value === "gemini-3-flash-preview") $("vertexLocation").value = "global";
    showProvider(state.settings.DETECTION_PROVIDER);
  };
  $("vertexLocation").onchange = () => showProvider(state.settings.DETECTION_PROVIDER);
  $("localUrl").oninput = () => showProvider(state.settings.DETECTION_PROVIDER);
  $("localModel").onchange = () => showProvider(state.settings.DETECTION_PROVIDER);
  $("refreshLocalModels").onclick = () => loadLocalModels(true);
  $("testEngine").onclick = testEngine;
  $("saveSettings").onclick = saveSettings;
  $("logoutButton").onclick = async () => {
    await api("/api/admin/logout", {method: "POST"});
    window.location.href = "/admin/login";
  };
  window.addEventListener("resize", drawSelection);

  (async () => {
    try {
      await loadSettings();
      await loadCategories();
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
