/**
 * MockupGen · Server Pulse & Operations Dashboard Frontend Logic
 */

(function () {
  "use strict";

  let pollIntervalMs = 2000;
  let pollTimer = null;
  let currentFilter = "all";
  let activeTab = "stream";
  let cachedRequests = [];
  let cachedErrors = [];

  // DOM Elements
  const kpiUptime = document.getElementById("kpiUptime");
  const kpiThreads = document.getElementById("kpiThreads");
  const kpiPlatform = document.getElementById("kpiPlatform");
  const kpiRam = document.getElementById("kpiRam");
  const kpiCpu = document.getElementById("kpiCpu");
  const kpiRamBar = document.getElementById("kpiRamBar");
  const kpiTotalReq = document.getElementById("kpiTotalReq");
  const kpiReqPerMin = document.getElementById("kpiReqPerMin");
  const kpiSuccessRate = document.getElementById("kpiSuccessRate");
  const kpi2xx = document.getElementById("kpi2xx");
  const kpi4xx = document.getElementById("kpi4xx");
  const kpi5xx = document.getElementById("kpi5xx");
  const kpiAvgLatency = document.getElementById("kpiAvgLatency");
  const kpiAvgRender = document.getElementById("kpiAvgRender");
  const tabErrorCount = document.getElementById("tabErrorCount");

  const requestTableBody = document.getElementById("requestTableBody");
  const errorsFeed = document.getElementById("errorsFeed");
  const storageOutputs = document.getElementById("storageOutputs");
  const storageUploads = document.getElementById("storageUploads");
  const storageTemplates = document.getElementById("storageTemplates");
  const storageTotal = document.getElementById("storageTotal");
  const topTemplatesList = document.getElementById("topTemplatesList");
  const totalRendersCount = document.getElementById("totalRendersCount");

  // Modal Elements
  const errorModal = document.getElementById("errorModal");
  const modalBackdrop = document.getElementById("modalBackdrop");
  const modalCloseBtn = document.getElementById("modalCloseBtn");
  const modalErrorTitle = document.getElementById("modalErrorTitle");
  const modalErrorSub = document.getElementById("modalErrorSub");
  const modalErrorMessage = document.getElementById("modalErrorMessage");
  const modalTraceback = document.getElementById("modalTraceback");
  const copyTracebackBtn = document.getElementById("copyTracebackBtn");

  // Fetch telemetry summary
  async function fetchSummary() {
    try {
      const res = await fetch("/api/telemetry/summary");
      if (!res.ok) return;
      const json = await res.json();
      if (!json.success || !json.data) return;

      updateSummaryUI(json.data);
    } catch (err) {
      console.warn("Failed to fetch telemetry summary:", err);
    }
  }

  // Update KPI Cards UI
  function updateSummaryUI(data) {
    const sys = data.system || {};
    const reqs = data.requests || {};
    const rnd = data.rendering || {};
    const stor = data.storage || {};

    if (kpiUptime) kpiUptime.textContent = sys.uptime_formatted || "--";
    if (kpiThreads) kpiThreads.textContent = `${sys.active_threads || 8} Threads`;
    if (kpiPlatform) kpiPlatform.textContent = `Python ${sys.python_version || ""}`;

    if (kpiRam) kpiRam.textContent = `${sys.process_ram_mb || 0} MB`;
    if (kpiCpu) kpiCpu.textContent = `${sys.cpu_percent || 0}%`;
    if (kpiRamBar) {
      const memPercent = Math.min(100, Math.max(5, sys.system_ram_percent || 10));
      kpiRamBar.style.width = `${memPercent}%`;
    }

    if (kpiTotalReq) kpiTotalReq.textContent = Number(reqs.total || 0).toLocaleString();
    if (kpiReqPerMin) kpiReqPerMin.textContent = reqs.per_minute || 0;

    if (kpiSuccessRate) {
      const rate = reqs.success_rate !== undefined ? reqs.success_rate : 100;
      kpiSuccessRate.textContent = `${rate}%`;
      if (rate < 90) {
        kpiSuccessRate.style.color = "var(--status-5xx)";
      } else if (rate < 98) {
        kpiSuccessRate.style.color = "var(--status-4xx)";
      } else {
        kpiSuccessRate.style.color = "var(--success)";
      }
    }

    if (kpi2xx) kpi2xx.textContent = `${reqs.status_2xx || 0} 2xx`;
    if (kpi4xx) kpi4xx.textContent = `${reqs.status_4xx || 0} 4xx`;
    if (kpi5xx) kpi5xx.textContent = `${reqs.status_5xx || 0} 5xx`;

    if (kpiAvgLatency) kpiAvgLatency.textContent = `${reqs.avg_latency_ms || 0} ms`;
    if (kpiAvgRender) kpiAvgRender.textContent = `${rnd.avg_render_ms || 0} ms`;

    const errTotal = (reqs.status_4xx || 0) + (reqs.status_5xx || 0);
    if (tabErrorCount) {
      tabErrorCount.textContent = errTotal;
      tabErrorCount.style.display = errTotal > 0 ? "inline-block" : "none";
    }

    // Storage update
    if (storageOutputs) storageOutputs.textContent = `${stor.outputs_mb || 0} MB`;
    if (storageUploads) storageUploads.textContent = `${stor.uploads_mb || 0} MB`;
    if (storageTemplates) storageTemplates.textContent = `${stor.templates_mb || 0} MB`;
    if (storageTotal) storageTotal.textContent = `${stor.total_storage_mb || 0} MB`;

    // Analytics update
    if (totalRendersCount) totalRendersCount.textContent = `${rnd.total_renders || 0} total renders`;
    if (topTemplatesList && rnd.top_templates) {
      renderTopTemplates(rnd.top_templates);
    }
  }

  function renderTopTemplates(templates) {
    if (!templates || templates.length === 0) {
      topTemplatesList.innerHTML = '<div class="sub">No render metrics recorded yet. Generate mockups to populate analytics.</div>';
      return;
    }
    const maxCount = Math.max(...templates.map(t => t.count), 1);
    topTemplatesList.innerHTML = templates.map(t => {
      const percent = Math.round((t.count / maxCount) * 100);
      return `
        <div class="template-stat-item">
          <div class="template-stat-row">
            <span class="bold"><code>${escapeHtml(t.template_id)}</code></span>
            <span>${t.count} renders</span>
          </div>
          <div class="template-bar-bg">
            <div class="template-bar-fill" style="width: ${percent}%;"></div>
          </div>
        </div>
      `;
    }).join("");
  }

  // Fetch Requests Feed
  async function fetchRequests() {
    try {
      const res = await fetch(`/api/telemetry/requests?limit=80&filter=${currentFilter}`);
      if (!res.ok) return;
      const json = await res.json();
      if (!json.success || !json.requests) return;

      cachedRequests = json.requests;
      renderRequestsTable(cachedRequests);
    } catch (err) {
      console.warn("Failed to fetch requests feed:", err);
    }
  }

  function renderRequestsTable(requests) {
    if (!requestTableBody) return;
    if (!requests || requests.length === 0) {
      requestTableBody.innerHTML = `<tr><td colspan="7" class="empty-cell">No requests recorded matching filter "${currentFilter}".</td></tr>`;
      return;
    }

    requestTableBody.innerHTML = requests.map(r => {
      const timeStr = formatTime(r.timestamp);
      const methodClass = getMethodClass(r.method);
      const statusBadge = getStatusBadge(r.status);
      const latencyClass = r.is_slow ? "latency-val latency-slow" : "latency-val";
      const inspectBtn = r.status >= 400 || r.error
        ? `<button class="btn compact danger-outline inspect-btn" data-req-id="${r.id}">Inspect</button>`
        : `<span class="sub">&mdash;</span>`;

      return `
        <tr>
          <td><code class="sub">${timeStr}</code></td>
          <td><span class="method-tag ${methodClass}">${escapeHtml(r.method)}</span></td>
          <td><code>${escapeHtml(r.path)}</code>${r.error ? `<div class="sub text-error">${escapeHtml(r.error)}</div>` : ''}</td>
          <td>${statusBadge}</td>
          <td><span class="${latencyClass}">${r.duration_ms} ms</span></td>
          <td><code class="sub">${escapeHtml(r.client_ip || '127.0.0.1')}</code></td>
          <td>${inspectBtn}</td>
        </tr>
      `;
    }).join("");

    // Attach inspect click handlers
    document.querySelectorAll(".inspect-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        const reqId = btn.getAttribute("data-req-id");
        const found = cachedRequests.find(q => q.id === reqId);
        if (found) openErrorModalFromRequest(found);
      });
    });
  }

  // Fetch Error Diagnostics
  async function fetchErrors() {
    try {
      const res = await fetch("/api/telemetry/errors?limit=50");
      if (!res.ok) return;
      const json = await res.json();
      if (!json.success || !json.errors) return;

      cachedErrors = json.errors;
      renderErrorsFeed(cachedErrors);
    } catch (err) {
      console.warn("Failed to fetch error diagnostics:", err);
    }
  }

  function renderErrorsFeed(errors) {
    if (!errorsFeed) return;
    if (!errors || errors.length === 0) {
      errorsFeed.innerHTML = `
        <div class="empty-state">
          <div class="empty-icon">&#10004;</div>
          <div class="empty-title">All Systems Operational</div>
          <div class="empty-sub">No unhandled exceptions or 500 errors recorded in recent history.</div>
        </div>
      `;
      return;
    }

    errorsFeed.innerHTML = errors.map(e => {
      const timeStr = formatTime(e.timestamp);
      return `
        <div class="error-card" data-error-id="${e.id}">
          <div class="error-card-header">
            <span class="error-card-title">${escapeHtml(e.error_type || 'Error')} &bull; HTTP ${e.status}</span>
            <span class="sub">${timeStr}</span>
          </div>
          <div class="error-card-msg"><code>${escapeHtml(e.error_message || '')}</code></div>
          <div class="error-card-meta">
            Endpoint: <code>${escapeHtml(e.method)} ${escapeHtml(e.path)}</code> &bull; IP: ${escapeHtml(e.client_ip || '127.0.0.1')}
          </div>
        </div>
      `;
    }).join("");

    document.querySelectorAll(".error-card").forEach(card => {
      card.addEventListener("click", () => {
        const errId = card.getAttribute("data-error-id");
        const found = cachedErrors.find(e => e.id === errId);
        if (found) openErrorModal(found);
      });
    });
  }

  function openErrorModal(error) {
    if (!errorModal) return;
    modalErrorTitle.textContent = `${error.error_type || 'Error'} (HTTP ${error.status})`;
    modalErrorSub.textContent = `${error.method} ${error.path} • ${formatTime(error.timestamp)}`;
    modalErrorMessage.textContent = error.error_message || `HTTP ${error.status}`;
    modalTraceback.textContent = error.traceback || "No Python traceback available for this error.";
    errorModal.classList.remove("hidden");
  }

  function openErrorModalFromRequest(req) {
    if (!errorModal) return;
    modalErrorTitle.textContent = `HTTP ${req.status} Error`;
    modalErrorSub.textContent = `${req.method} ${req.path} • ${formatTime(req.timestamp)}`;
    modalErrorMessage.textContent = req.error || `Client error response HTTP ${req.status}`;
    
    // Check if we have a matching detailed error
    const matching = cachedErrors.find(e => e.id === req.id);
    if (matching && matching.traceback) {
      modalTraceback.textContent = matching.traceback;
    } else {
      modalTraceback.textContent = `Endpoint: ${req.method} ${req.path}\nClient IP: ${req.client_ip}\nDuration: ${req.duration_ms} ms\nStatus Code: ${req.status}\nError Note: ${req.error || 'No detailed server traceback recorded.'}`;
    }
    errorModal.classList.remove("hidden");
  }

  function closeModal() {
    if (errorModal) errorModal.classList.add("hidden");
  }

  // Polling Loop
  async function tick() {
    await Promise.all([fetchSummary(), activeTab === "stream" ? fetchRequests() : (activeTab === "errors" ? fetchErrors() : null)]);
  }

  function startPolling() {
    stopPolling();
    if (pollIntervalMs > 0) {
      pollTimer = setInterval(tick, pollIntervalMs);
    }
  }

  function stopPolling() {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  }

  // Event Listeners Initialization
  function initEvents() {
    // Refresh interval pills
    document.querySelectorAll(".refresh-pill-group .pill-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        document.querySelectorAll(".refresh-pill-group .pill-btn").forEach(b => b.classList.remove("active"));
        btn.classList.add("active");
        pollIntervalMs = parseInt(btn.getAttribute("data-interval"), 10);
        startPolling();
      });
    });

    // Manual Refresh
    const manualBtn = document.getElementById("manualRefreshBtn");
    if (manualBtn) {
      manualBtn.addEventListener("click", async () => {
        await Promise.all([fetchSummary(), fetchRequests(), fetchErrors()]);
      });
    }

    // Tabs switching
    document.querySelectorAll(".pulse-tab").forEach(tab => {
      tab.addEventListener("click", () => {
        document.querySelectorAll(".pulse-tab").forEach(t => t.classList.remove("active"));
        document.querySelectorAll(".tab-pane").forEach(p => p.classList.remove("active"));
        tab.classList.add("active");
        const tabName = tab.getAttribute("data-tab");
        activeTab = tabName;

        const pane = document.getElementById(`pane-${tabName}`);
        if (pane) pane.classList.add("active");

        const streamActions = document.getElementById("streamTabActions");
        if (streamActions) {
          streamActions.style.visibility = tabName === "stream" ? "visible" : "hidden";
        }

        if (tabName === "stream") fetchRequests();
        if (tabName === "errors") fetchErrors();
        if (tabName === "analytics") fetchSummary();
      });
    });

    // Filter pills
    document.querySelectorAll("#statusFilters .filter-pill").forEach(pill => {
      pill.addEventListener("click", () => {
        document.querySelectorAll("#statusFilters .filter-pill").forEach(p => p.classList.remove("active"));
        pill.classList.add("active");
        currentFilter = pill.getAttribute("data-filter");
        fetchRequests();
      });
    });

    // Purge Cache Buttons
    const purgeHandler = async () => {
      if (!confirm("Are you sure you want to delete temporary output files older than 24 hours?")) return;
      try {
        const res = await fetch("/api/telemetry/purge-temp?max_age_hours=24", { method: "POST" });
        const json = await res.json();
        if (json.success) {
          alert(`Cleaned ${json.deleted_count} files (Freed ${json.freed_mb} MB)`);
          fetchSummary();
        }
      } catch (err) {
        alert("Failed to purge temporary files: " + err);
      }
    };

    const purgeBtn1 = document.getElementById("purgeTempBtn");
    const purgeBtn2 = document.getElementById("purgeAnalyticsBtn");
    if (purgeBtn1) purgeBtn1.addEventListener("click", purgeHandler);
    if (purgeBtn2) purgeBtn2.addEventListener("click", purgeHandler);

    // Clear Logs Button
    const clearBtn = document.getElementById("clearLogsBtn");
    if (clearBtn) {
      clearBtn.addEventListener("click", async () => {
        if (!confirm("Clear all recorded request and error logs?")) return;
        try {
          await fetch("/api/telemetry/clear-logs", { method: "POST" });
          fetchSummary();
          fetchRequests();
          fetchErrors();
        } catch (err) {
          console.warn("Failed to clear logs:", err);
        }
      });
    }

    // Modal close
    if (modalCloseBtn) modalCloseBtn.addEventListener("click", closeModal);
    if (modalBackdrop) modalBackdrop.addEventListener("click", closeModal);
    document.addEventListener("keydown", (e) => {
      if (e.key === "Escape") closeModal();
    });

    // Copy Traceback
    if (copyTracebackBtn) {
      copyTracebackBtn.addEventListener("click", () => {
        const text = modalTraceback.textContent;
        navigator.clipboard.writeText(text).then(() => {
          copyTracebackBtn.textContent = "Copied!";
          setTimeout(() => { copyTracebackBtn.textContent = "Copy Traceback"; }, 1800);
        });
      });
    }
  }

  // Helpers
  function formatTime(isoStr) {
    if (!isoStr) return "--:--:--";
    const d = new Date(isoStr);
    return d.toTimeString().split(" ")[0];
  }

  function getMethodClass(method) {
    const m = (method || "").toUpperCase();
    if (m === "GET") return "method-get";
    if (m === "POST") return "method-post";
    if (m === "DELETE") return "method-delete";
    return "";
  }

  function getStatusBadge(status) {
    if (status >= 200 && status < 300) {
      return `<span class="status-badge bg-2xx">${status} OK</span>`;
    }
    if (status >= 300 && status < 400) {
      return `<span class="status-badge bg-2xx">${status}</span>`;
    }
    if (status >= 400 && status < 500) {
      return `<span class="status-badge bg-4xx">${status}</span>`;
    }
    return `<span class="status-badge bg-5xx">${status} ERR</span>`;
  }

  function escapeHtml(str) {
    if (!str) return "";
    return String(str)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;")
      .replace(/'/g, "&#039;");
  }

  // Start
  document.addEventListener("DOMContentLoaded", () => {
    initEvents();
    tick();
    startPolling();
  });
})();
