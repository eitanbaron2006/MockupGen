/* The print page: ratios, sets, and the export that turns one artwork into the
   files a buyer downloads. It talks only to /api/print/..., which is the same
   API the shop-side application calls, so nothing here is a private path. */

const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';

const state = {
  ratios: [],
  qualities: [],
  modes: [],
  sets: [],
  settings: {},
  artwork: null,
  artworkSize: null,
  artworkUrl: null,
  lastExport: null,
  running: null,
  previews: [],
  previewScope: null,
  previewAt: 0,
  view: 'results',
  history: [],
  editingSet: null,
  editingRatio: null,
  confirmAction: null,
};

const el = (id) => document.getElementById(id);

const escapeHtml = (value) => String(value ?? '').replace(/[&<>"']/g, (character) => (
  { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[character]
));

function toast(message, bad = false) {
  const node = el('printToast');
  node.textContent = message;
  node.classList.toggle('is-bad', Boolean(bad));
  node.classList.add('is-on');
  window.clearTimeout(toast.timer);
  toast.timer = window.setTimeout(() => node.classList.remove('is-on'), 3200);
}

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (options.body && !(options.body instanceof FormData)) headers['Content-Type'] = 'application/json';
  if (options.method && options.method !== 'GET') headers['X-CSRF-Token'] = csrf;
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `Request failed (${response.status})`);
  return payload;
}

/* --------------------------------------------------------------- drawing */

/** One ratio as a rectangle at its true proportion -- the shape is the label.
    It is fitted inside a box rather than hung from its height, because a 3:1
    panoramic drawn to a 46px height is 138px wide and shoves the card's text
    out of the column. */
function shapeHtml(ratio, height = 28, on = false, maxWidth = Math.round(height * 1.55)) {
  const fit = Math.min(height / ratio.height, maxWidth / ratio.width);
  const width = Math.max(6, Math.round(ratio.width * fit));
  const drawn = Math.max(6, Math.round(ratio.height * fit));
  return `<i class="ratio-shape${on ? ' is-on' : ''}" style="width:${width}px;height:${drawn}px" title="${escapeHtml(ratio.key)}"></i>`;
}

const modeLabel = (set) => (set.mode === 'chosen' ? plural((set.ratio_keys || []).length, 'ratio') : 'Its own ratio');

const qualityName = (key) => state.qualities.find((quality) => quality.key === key)?.name || key;

const modeName = (key) => state.modes.find((mode) => mode.key === key)?.name || key;

/** How long one file took, in the shortest form that stays honest. */
function took(ms) {
  if (!ms && ms !== 0) return '';
  if (ms < 1000) return `${ms}ms`;
  const seconds = ms / 1000;
  if (seconds < 60) return `${seconds.toFixed(seconds < 10 ? 1 : 0)}s`;
  return `${Math.floor(seconds / 60)}m ${String(Math.round(seconds % 60)).padStart(2, '0')}s`;
}

/* The batch id keeps one export apart from another on disk. It is not part of
   what the buyer receives -- the archive strips it -- so the screen does too. */
const buyerName = (file) => (file.includes('_') ? file.slice(file.indexOf('_') + 1) : file);

/* ----------------------------------------------------------------- panels */

function renderSets() {
  const list = el('setList');
  if (!state.sets.length) {
    list.innerHTML = '<div class="empty">No print sets yet. The + adds one.</div>';
    return;
  }
  list.innerHTML = state.sets.map((set) => {
    const keys = (set.ratio_keys || []).map((key) => key.toLowerCase());
    const shapes = set.mode === 'chosen'
      ? state.ratios.filter((ratio) => keys.includes(ratio.key.toLowerCase()))
      : state.ratios.slice(0, 0);
    const drawn = set.mode === 'chosen'
      ? shapes.map((ratio) => shapeHtml(ratio, 34, true)).join('')
      : '<span class="set-card-foot" style="margin:0">Follows the artwork</span>';
    return `
      <div class="set-card" data-set="${set.id}" role="button" tabindex="0">
        <button class="set-trash" data-trash="${set.id}" title="Delete this set">&#128465;</button>
        <div class="set-card-top">
          <span class="set-card-name">${escapeHtml(set.name)}</span>
          <span class="set-card-mode">${escapeHtml(modeLabel(set))}</span>
        </div>
        <div class="ratio-shapes">${drawn}</div>
        <div class="set-card-foot">
          ${escapeHtml(modeName(set.output_mode))} &middot; ${escapeHtml(qualityName(set.quality))}${set.include_guide ? ' &middot; with guide' : ''}
        </div>
      </div>`;
  }).join('');
}

function renderRatios() {
  const list = el('ratioList');
  if (!state.ratios.length) {
    list.innerHTML = '<div class="empty">No ratios yet.</div>';
    return;
  }
  list.innerHTML = state.ratios.map((ratio) => `
    <div class="ratio-card${ratio.active ? '' : ' is-off'}" data-ratio="${ratio.id}" role="button" tabindex="0">
      ${shapeHtml(ratio, 46, Boolean(ratio.active))}
      <div class="ratio-card-body">
        <div class="ratio-card-name">${escapeHtml(ratio.name || ratio.key)}</div>
        <div class="ratio-card-px">${ratio.width} &times; ${ratio.height} px</div>
        <div class="ratio-card-sizes" title="${escapeHtml(ratio.sizes || '')}">${escapeHtml(ratio.sizes || 'No sizes listed')}</div>
        ${ratio.default_set_id
          ? `<div class="ratio-card-set">&#8594; ${escapeHtml(state.sets.find((set) => set.id === ratio.default_set_id)?.name || 'a package')}</div>`
          : ''}
      </div>
      <div class="ratio-actions">
        <button class="ratio-toggle${ratio.active ? ' is-on' : ''}" data-toggle="${ratio.id}" title="${ratio.active ? 'Stop offering this ratio' : 'Offer this ratio again'}">${ratio.active ? 'ON' : 'OFF'}</button>
        ${ratio.builtin
          ? ''
          : `<button class="ratio-drop" data-drop="${ratio.id}" title="Delete this ratio">&#128465;</button>`}
      </div>
    </div>`).join('');
}

function renderQualityOptions(select, chosen) {
  select.innerHTML = state.qualities.map((quality) => `
    <option value="${quality.key}"${quality.available ? '' : ' disabled'}${quality.key === chosen ? ' selected' : ''}>
      ${escapeHtml(quality.name)}${quality.available ? '' : ' (not installed)'}
    </option>`).join('');
}

function renderModeOptions(select, chosen) {
  select.innerHTML = state.modes.map((mode) => `
    <option value="${mode.key}"${mode.key === chosen ? ' selected' : ''}>${escapeHtml(mode.name)}</option>`).join('');
}

function renderExportControls() {
  const select = el('exportSet');
  const current = select.value;
  select.innerHTML = [
    '<option value="">Its own ratio only</option>',
    ...state.sets.map((set) => `<option value="${set.id}">${escapeHtml(set.name)}</option>`),
  ].join('');
  if (current) select.value = current;
  renderQualityOptions(el('exportQuality'), el('exportQuality').value || 'bicubic');
  renderModeOptions(el('exportMode'), el('exportMode').value || 'safe_fit');
  syncQualityForSet();
}

/** A saved set carries its own quality and output mode, so the dock follows it
    rather than silently overriding what the admin stored. */
function syncQualityForSet() {
  const set = state.sets.find((entry) => String(entry.id) === el('exportSet').value);
  for (const [node, field] of [[el('exportQuality'), 'quality'], [el('exportMode'), 'output_mode']]) {
    if (set) {
      node.value = set[field];
      node.disabled = true;
      node.title = `Set by "${set.name}"`;
    } else {
      node.disabled = false;
      node.title = '';
    }
  }
}

const plural = (count, word) => `${count} ${word}${count === 1 ? '' : 's'}`;

function renderSummary() {
  const active = state.ratios.filter((ratio) => ratio.active).length;
  el('catalogSummary').textContent = `${plural(active, 'ratio')} active · ${plural(state.sets.length, 'set')}`;
}

/* ----------------------------------------------------------------- export */

/** One finished file as a card. Used by the export and by the history. */
function resultCard(entry) {
  if (!entry.success) {
    return `<div class="print-result is-failed">
        <div class="print-result-top">
          <span class="print-result-ratio">${escapeHtml(entry.ratio)}</span>
          ${entry.ms ? `<span class="print-result-took">${escapeHtml(took(entry.ms))}</span>` : ''}
        </div>
        <div class="print-result-error">${escapeHtml(entry.error)}</div>
      </div>`;
  }
  return `<div class="print-result" data-preview="${escapeHtml(entry.url)}" data-caption="${escapeHtml(buyerName(entry.file))}" role="button" tabindex="0">
      <img src="${escapeHtml(entry.url)}" alt="${escapeHtml(entry.ratio)}" loading="lazy">
      <div class="print-result-body">
        <div class="print-result-top">
          <span class="print-result-ratio">${escapeHtml(entry.ratio)}</span>
          ${entry.ms ? `<span class="print-result-took" title="How long this file took to render">${escapeHtml(took(entry.ms))}</span>` : ''}
        </div>
        <div class="print-result-px">${entry.width} &times; ${entry.height} px &middot; 300 DPI</div>
        <div class="print-result-sizes">${escapeHtml(entry.prints_at || '')}</div>
      </div>
    </div>`;
}

/** A card for a ratio that has been asked for but has not arrived yet. */
function pendingCard(key) {
  return `<div class="print-result is-pending">
      <div class="pending-shade"></div>
      <div class="print-result-body">
        <div class="print-result-ratio">${escapeHtml(key)}</div>
        <div class="print-result-px">rendering&hellip;</div>
      </div>
    </div>`;
}

function renderResults() {
  const results = el('exportResults');
  const made = state.lastExport;
  if (!made) {
    results.innerHTML = '<div class="empty">Nothing exported yet.</div>';
    el('downloadZip').disabled = true;
    return;
  }
  const cards = made.files.map(resultCard);
  // While the export is still running, the ratios yet to come hold their place
  // so the grid does not jump about as each one lands.
  for (const key of (made.awaiting || [])) cards.push(pendingCard(key));
  if (made.guide) {
    cards.push(`<a class="print-result guide-result" href="${escapeHtml(made.guide.url)}" target="_blank" rel="noopener">
        <div class="print-result-body">
          <div class="print-result-ratio">Printing guide</div>
          <div class="print-result-sizes">The note that ships with the files</div>
        </div>
      </a>`);
  }
  results.innerHTML = cards.join('');
  // A half-made set of files is not a package. The archive waits for the last
  // one -- a buyer sent four files out of six has been sent the wrong thing.
  el('downloadZip').disabled = !made.complete || !made.files.some((entry) => entry.success);
  if (state.previewScope === el('exportResults') || !state.previewScope) collectPreviews(el('exportResults'));
}

/* Every file currently on show, in the order it is shown. The full-screen
   view walks this list, so comparing two ratios does not mean closing the
   view, finding the next card and opening it again. */
/* What the full-screen view walks is whatever the clicked card belongs to:
   the results of this export, or the one past export whose files were opened.
   Scrolling from a history entry into an unrelated one is not "the next
   result" -- it is a different piece of work. */
function collectPreviews(scope) {
  state.previewScope = scope || state.previewScope;
  const within = state.previewScope && document.contains(state.previewScope)
    ? state.previewScope
    : el('exportResults');
  state.previews = [...within.querySelectorAll('[data-preview]')]
    .map((node) => ({ url: node.dataset.preview, caption: node.dataset.caption }));
  // Files keep arriving while the view is open, so the count it shows follows
  // them rather than waiting for the next scroll to catch up.
  if (el('printPreview').classList.contains('is-open')) {
    const showing = state.previews.findIndex((entry) => entry.url === el('previewImage').getAttribute('src'));
    if (showing >= 0) openPreview(showing);
  }
}

function openPreview(index) {
  if (!state.previews.length) return;
  const total = state.previews.length;
  state.previewAt = ((index % total) + total) % total;
  const shown = state.previews[state.previewAt];
  el('previewImage').src = shown.url;
  el('previewCaption').textContent = total > 1
    ? `${shown.caption}   ·   ${state.previewAt + 1} of ${total}`
    : shown.caption;
  el('previewPrev').hidden = total < 2;
  el('previewNext').hidden = total < 2;
  openModal('printPreview');
}

const stepPreview = (by) => openPreview(state.previewAt + by);

/* A trackpad reports a flick as a run of events; one step per gesture. */
let wheelReady = true;
function onPreviewWheel(event) {
  if (!state.previews.length) return;
  event.preventDefault();
  if (!wheelReady || Math.abs(event.deltaY) < 4) return;
  wheelReady = false;
  window.setTimeout(() => { wheelReady = true; }, 180);
  stepPreview(event.deltaY > 0 ? 1 : -1);
}

/* ---------------------------------------------------------------- history */

const shortDate = (value) => {
  const at = new Date(value);
  return Number.isNaN(at.getTime()) ? '' : at.toLocaleString(undefined, {
    month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit',
  });
};

function renderHistory() {
  const panel = el('exportHistory');
  if (!state.history.length) {
    panel.innerHTML = '<div class="empty">Nothing exported yet. What you make is kept here.</div>';
    return;
  }
  panel.innerHTML = state.history.map((run) => `
    <div class="history-run">
      <div class="history-head">
        <div>
          <div class="history-artwork">${escapeHtml(run.artwork_name || 'Untitled artwork')}</div>
          <div class="history-facts">
            ${escapeHtml(shortDate(run.created_at))}
            &middot; ${escapeHtml(run.set_name || 'no set')}
            &middot; ${escapeHtml(modeName(run.output_mode))}
            &middot; ${escapeHtml(qualityName(run.quality))}
            &middot; ${plural(run.files.length, 'file')}
          </div>
        </div>
        <button class="set-trash" data-forget="${run.id}" title="Delete this export and its files">&#128465;</button>
      </div>
      <div class="history-files">
        ${run.files.map((file) => `
          <button class="history-file" data-preview="/print-outputs/${escapeHtml(file.file_name)}" data-caption="${escapeHtml(buyerName(file.file_name))}">
            <img src="/print-outputs/${escapeHtml(file.file_name)}" alt="${escapeHtml(file.ratio_key)}" loading="lazy">
            <span>${escapeHtml(file.ratio_key)}${file.ms ? ` &middot; ${escapeHtml(took(file.ms))}` : ''}</span>
          </button>`).join('')}
      </div>
    </div>`).join('');
}

async function loadHistory() {
  try {
    const payload = await api('/api/print/exports?limit=40');
    state.history = payload.exports || [];
    renderHistory();
  } catch (error) {
    toast(error.message, true);
  }
}

function showView(view) {
  state.view = view;
  el('exportResults').hidden = view !== 'results';
  el('exportHistory').hidden = view !== 'history';
  document.querySelectorAll('#exportViews [data-view]').forEach((button) => {
    button.classList.toggle('is-on', button.dataset.view === view);
  });
  if (view === 'history') loadHistory();
  else collectPreviews();
}

function showArtwork(file) {
  if (!file) return;
  if (state.artworkUrl) URL.revokeObjectURL(state.artworkUrl);
  state.artwork = file;
  state.artworkUrl = URL.createObjectURL(file);
  const preview = el('artworkPreview');
  preview.src = state.artworkUrl;
  preview.hidden = false;
  el('artworkHint').hidden = true;
  el('runExport').disabled = false;
  const probe = new Image();
  probe.onload = () => {
    state.artworkSize = { width: probe.naturalWidth, height: probe.naturalHeight };
    const shape = probe.naturalWidth > probe.naturalHeight ? 'landscape'
      : probe.naturalWidth === probe.naturalHeight ? 'square' : 'portrait';
    el('artworkFacts').textContent = `${probe.naturalWidth} × ${probe.naturalHeight} px · ${shape}`;
  };
  probe.src = state.artworkUrl;
}

/* The clock. It runs in the header while files land in the panel below, so
   the wait is visible without anything being blocked: every file already made
   stays clickable, and the full-screen view works while the rest render. */
function elapsed(since) {
  const seconds = Math.floor((Date.now() - since) / 1000);
  return `${String(Math.floor(seconds / 60)).padStart(2, '0')}:${String(seconds % 60).padStart(2, '0')}`;
}

function showProgress() {
  const run = state.running;
  const sub = el('exportSub');
  if (!run) {
    sub.classList.remove('is-working');
    return;
  }
  const done = state.lastExport?.files.length || 0;
  sub.classList.add('is-working');
  sub.textContent = `${done} of ${run.total} files · ${elapsed(run.startedAt)} · ${modeName(run.mode)}`;
}

function startClock(run) {
  state.running = run;
  showProgress();
  run.ticker = window.setInterval(showProgress, 1000);
}

function stopClock() {
  if (state.running?.ticker) window.clearInterval(state.running.ticker);
  state.running = null;
  el('exportSub').classList.remove('is-working');
}

/** One newline-delimited JSON line from the export. */
function onExportEvent(event) {
  if (event.event === 'start') {
    state.lastExport = {
      files: [],
      awaiting: [...event.ratios],
      guide: null,
      mode: event.mode,
      quality: event.quality,
      complete: false,
    };
    startClock({ total: event.ratios.length, startedAt: Date.now(), mode: event.mode });
    renderResults();
    return;
  }
  if (event.event === 'file') {
    const { event: _kind, ...entry } = event;
    state.lastExport.files.push(entry);
    state.lastExport.awaiting = state.lastExport.awaiting.filter((key) => key !== entry.ratio);
    renderResults();
    showProgress();
    return;
  }
  if (event.event === 'done') {
    const { event: _kind, ...summary } = event;
    state.lastExport = { ...summary, awaiting: [], complete: true };
    stopClock();
    renderResults();
    const good = summary.files.filter((entry) => entry.success).length;
    el('exportSub').textContent = `${good} of ${summary.files.length} files · ${modeName(summary.mode)} · ${qualityName(summary.quality)}`;
    toast(summary.success ? `${good} print files ready.` : `${good} of ${summary.files.length} made — see the notes.`, !summary.success);
  }
}

async function runExport() {
  if (!state.artwork) return;
  showView('results');
  const button = el('runExport');
  const restore = button.innerHTML;
  button.disabled = true;
  button.classList.add('is-busy');
  button.textContent = 'Exporting…';
  el('downloadZip').disabled = true;
  try {
    const spec = {};
    const setId = el('exportSet').value;
    if (setId) {
      spec.set = Number(setId);
    } else {
      spec.quality = el('exportQuality').value;
      spec.mode = el('exportMode').value;
    }
    const body = new FormData();
    body.append('artwork', state.artwork, state.artwork.name);
    body.append('spec', JSON.stringify(spec));

    // Asking for the stream: each file arrives on its own line as it is made,
    // rather than the whole set appearing at the end of a long silence.
    const response = await fetch('/api/print/export?stream=1', {
      method: 'POST',
      headers: { 'X-CSRF-Token': csrf },
      body,
    });
    if (!response.ok || !response.body) {
      const failed = await response.json().catch(() => ({}));
      throw new Error(failed.error || `Export failed (${response.status})`);
    }

    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = '';
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let cut = buffer.indexOf('\n');
      while (cut >= 0) {
        const line = buffer.slice(0, cut).trim();
        buffer = buffer.slice(cut + 1);
        if (line) onExportEvent(JSON.parse(line));
        cut = buffer.indexOf('\n');
      }
    }
    if (!state.lastExport?.complete) throw new Error('The export stopped before it finished');
  } catch (error) {
    stopClock();
    if (state.lastExport) state.lastExport.awaiting = [];
    renderResults();
    el('exportSub').textContent = 'An artwork in, the files a buyer downloads out';
    toast(error.message, true);
  } finally {
    button.classList.remove('is-busy');
    button.innerHTML = restore;
    button.disabled = false;
  }
}

async function downloadZip() {
  const made = state.lastExport;
  if (!made) return;
  const files = made.files.filter((entry) => entry.success).map((entry) => entry.file);
  if (made.guide) files.push(made.guide.file);
  try {
    const response = await fetch('/api/print/archive', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-CSRF-Token': csrf },
      body: JSON.stringify({ files, name: 'print-files.zip' }),
    });
    if (!response.ok) throw new Error((await response.json().catch(() => ({}))).error || 'The archive could not be built');
    const blob = await response.blob();
    const url = URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = url;
    link.download = 'print-files.zip';
    link.click();
    URL.revokeObjectURL(url);
  } catch (error) {
    toast(error.message, true);
  }
}

/* ----------------------------------------------------------------- modals */

const openModal = (id) => el(id).classList.add('is-open');
const closeModal = (id) => el(id).classList.remove('is-open');

function confirmThen(message, action) {
  el('confirmMessage').textContent = message;
  state.confirmAction = action;
  openModal('confirmModal');
}

function openSetModal(set) {
  state.editingSet = set || null;
  el('setModalTitle').textContent = set ? 'Edit print set' : 'New print set';
  el('setName').value = set?.name || '';
  el('setGuide').checked = set ? Boolean(set.include_guide) : true;
  renderQualityOptions(el('setQuality'), set?.quality || 'bicubic');
  renderOutputModes(set?.output_mode || 'safe_fit');
  setMode(set?.mode || 'matching');
  const chosen = (set?.ratio_keys || []).map((key) => key.toLowerCase());
  el('setRatios').innerHTML = state.ratios.map((ratio) => `
    <button type="button" class="ratio-pick${chosen.includes(ratio.key.toLowerCase()) ? ' is-on' : ''}" data-key="${escapeHtml(ratio.key)}">
      ${shapeHtml(ratio, 46, chosen.includes(ratio.key.toLowerCase()))}
      <span>${escapeHtml(ratio.key)}</span>
    </button>`).join('');
  openModal('setModal');
}

function renderOutputModes(chosen) {
  el('setOutputMode').innerHTML = state.modes.map((mode) => `
    <button type="button" class="output-mode${mode.key === chosen ? ' is-on' : ''}${mode.cuts ? ' cuts' : ''}" data-output="${mode.key}">
      <span class="dot"></span>
      <span>
        <strong>${escapeHtml(mode.name)}</strong> &mdash; <em>${escapeHtml(mode.note)}</em>
      </span>
    </button>`).join('');
}

const currentOutputMode = () => el('setOutputMode').querySelector('.output-mode.is-on')?.dataset.output || 'safe_fit';

function setMode(mode) {
  el('setMode').querySelectorAll('.mode-card').forEach((card) => {
    card.classList.toggle('is-on', card.dataset.mode === mode);
  });
  el('setRatiosField').hidden = mode !== 'chosen';
}

const currentMode = () => (el('setMode').querySelector('.mode-card.is-on')?.dataset.mode || 'matching');

async function saveSet() {
  const mode = currentMode();
  const keys = [...el('setRatios').querySelectorAll('.ratio-pick.is-on')].map((pick) => pick.dataset.key);
  if (mode === 'chosen' && !keys.length) {
    toast('A chosen-ratios set needs at least one ratio.', true);
    return;
  }
  const payload = {
    name: el('setName').value.trim(),
    mode,
    ratio_keys: keys,
    quality: el('setQuality').value,
    output_mode: currentOutputMode(),
    include_guide: el('setGuide').checked,
  };
  if (!payload.name) {
    toast('The set needs a name.', true);
    return;
  }
  try {
    if (state.editingSet) await api(`/api/print/sets/${state.editingSet.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
    else await api('/api/print/sets', { method: 'POST', body: JSON.stringify(payload) });
    closeModal('setModal');
    await loadSets();
    toast(`Saved "${payload.name}".`);
  } catch (error) {
    toast(error.message, true);
  }
}

function openRatioModal(ratio) {
  state.editingRatio = ratio || null;
  el('ratioModalTitle').textContent = ratio ? 'Edit ratio' : 'New ratio';
  el('ratioKey').value = ratio?.key || '';
  el('ratioName').value = ratio?.name || '';
  el('ratioWidth').value = ratio?.width || 7200;
  el('ratioHeight').value = ratio?.height || 10800;
  el('ratioSizes').value = ratio?.sizes || '';
  el('ratioDefaultSet').innerHTML = [
    '<option value="">Its own file only</option>',
    ...state.sets.map((set) => `<option value="${set.id}">${escapeHtml(set.name)}</option>`),
  ].join('');
  el('ratioDefaultSet').value = ratio?.default_set_id ? String(ratio.default_set_id) : '';
  drawRatioProof();
  openModal('ratioModal');
}

function drawRatioProof() {
  const width = Number(el('ratioWidth').value) || 1;
  const height = Number(el('ratioHeight').value) || 1;
  el('ratioProof').innerHTML = shapeHtml({ key: 'preview', width, height }, 38, true);
}

async function saveRatio() {
  const payload = {
    key: el('ratioKey').value.trim(),
    name: el('ratioName').value.trim(),
    width: Number(el('ratioWidth').value),
    height: Number(el('ratioHeight').value),
    sizes: el('ratioSizes').value.trim(),
    // What an incoming artwork of this shape is sold as.
    default_set_id: el('ratioDefaultSet').value ? Number(el('ratioDefaultSet').value) : null,
  };
  try {
    if (state.editingRatio) await api(`/api/print/ratios/${state.editingRatio.id}`, { method: 'PATCH', body: JSON.stringify(payload) });
    else await api('/api/print/ratios', { method: 'POST', body: JSON.stringify(payload) });
    closeModal('ratioModal');
    await loadRatios();
    toast(`Saved ${payload.key}.`);
  } catch (error) {
    toast(error.message, true);
  }
}

function renderQualityStatus() {
  el('qualityStatus').innerHTML = state.qualities.filter((quality) => quality.needs).map((quality) => `
    <div class="quality-line${quality.available ? ' is-ready' : ''}">
      <span>${escapeHtml(quality.name)}</span>
      <em>${quality.available ? 'Ready' : escapeHtml(quality.reason || 'Not installed')}</em>
    </div>`).join('');
  el('qualityStatus').hidden = !state.qualities.some((quality) => quality.needs);
}

async function openSettingsModal() {
  try {
    const payload = await api('/api/print/settings');
    state.settings = payload.settings || {};
    const found = payload.found || {};
    el('realesrganPath').value = state.settings.realesrgan_path || '';
    el('topazPath').value = state.settings.topaz_path || '';
    // An empty box is not a missing program: show where it was found, so the
    // admin can see there is nothing to fill in.
    for (const [id, key] of [['realesrganPath', 'realesrgan'], ['topazPath', 'topaz']]) {
      el(id).placeholder = found[key]
        ? `Leave empty to use ${found[key]}`
        : 'Not found on this machine';
    }
    renderQualityStatus();
    openModal('settingsModal');
  } catch (error) {
    toast(error.message, true);
  }
}

async function saveSettings() {
  try {
    await api('/api/print/settings', {
      method: 'PUT',
      body: JSON.stringify({
        realesrgan_path: el('realesrganPath').value.trim(),
        topaz_path: el('topazPath').value.trim(),
      }),
    });
    closeModal('settingsModal');
    await loadRatios();
    toast('Settings saved.');
  } catch (error) {
    toast(error.message, true);
  }
}

/* ------------------------------------------------------------------ loads */

async function loadRatios() {
  const payload = await api('/api/print/ratios');
  state.ratios = payload.ratios || [];
  state.qualities = payload.qualities || [];
  state.modes = payload.modes || [];
  renderRatios();
  // The set cards name their quality and draw their ratios, both of which
  // arrive here -- and the two loads race, so whichever lands second redraws.
  renderSets();
  renderExportControls();
  renderSummary();
}

async function loadSets() {
  const payload = await api('/api/print/sets');
  state.sets = payload.sets || [];
  renderSets();
  renderExportControls();
  renderSummary();
}

/* ----------------------------------------------------------------- wiring */

function wire() {
  el('addSet').addEventListener('click', () => openSetModal(null));
  el('addRatio').addEventListener('click', () => openRatioModal(null));
  el('openSettings').addEventListener('click', openSettingsModal);
  el('saveSet').addEventListener('click', saveSet);
  el('saveRatio').addEventListener('click', saveRatio);
  el('saveSettings').addEventListener('click', saveSettings);
  el('runExport').addEventListener('click', runExport);
  el('downloadZip').addEventListener('click', downloadZip);
  el('exportSet').addEventListener('change', syncQualityForSet);
  el('setMode').addEventListener('click', (event) => {
    const card = event.target.closest('.mode-card');
    if (card) setMode(card.dataset.mode);
  });
  el('setOutputMode').addEventListener('click', (event) => {
    const option = event.target.closest('.output-mode');
    if (option) renderOutputModes(option.dataset.output);
  });
  el('setRatios').addEventListener('click', (event) => {
    const pick = event.target.closest('.ratio-pick');
    if (!pick) return;
    pick.classList.toggle('is-on');
    pick.querySelector('.ratio-shape')?.classList.toggle('is-on');
  });
  el('ratioWidth').addEventListener('input', drawRatioProof);
  el('ratioHeight').addEventListener('input', drawRatioProof);

  el('setList').addEventListener('click', (event) => {
    const trash = event.target.closest('[data-trash]');
    if (trash) {
      const set = state.sets.find((entry) => String(entry.id) === trash.dataset.trash);
      confirmThen(`Delete the print set "${set?.name}"? Exports already made keep their files; only the set goes.`, async () => {
        await api(`/api/print/sets/${trash.dataset.trash}`, { method: 'DELETE' });
        await loadSets();
        toast('Set deleted.');
      });
      return;
    }
    const card = event.target.closest('[data-set]');
    if (card) openSetModal(state.sets.find((entry) => String(entry.id) === card.dataset.set));
  });

  el('ratioList').addEventListener('click', async (event) => {
    const toggle = event.target.closest('[data-toggle]');
    if (toggle) {
      const ratio = state.ratios.find((entry) => String(entry.id) === toggle.dataset.toggle);
      try {
        await api(`/api/print/ratios/${ratio.id}`, { method: 'PATCH', body: JSON.stringify({ active: !ratio.active }) });
        await loadRatios();
      } catch (error) {
        toast(error.message, true);
      }
      return;
    }
    const drop = event.target.closest('[data-drop]');
    if (drop) {
      const ratio = state.ratios.find((entry) => String(entry.id) === drop.dataset.drop);
      confirmThen(`Delete the ${ratio?.key} ratio? Sets that name it will stop producing that file.`, async () => {
        await api(`/api/print/ratios/${drop.dataset.drop}`, { method: 'DELETE' });
        await loadRatios();
        toast('Ratio deleted.');
      });
      return;
    }
    const card = event.target.closest('[data-ratio]');
    if (card) openRatioModal(state.ratios.find((entry) => String(entry.id) === card.dataset.ratio));
  });

  const openFromCard = (event) => {
    const card = event.target.closest('[data-preview]');
    if (!card) return;
    // One past export is one piece of work: its files scroll among themselves.
    collectPreviews(card.closest('.history-run') || el('exportResults'));
    openPreview(state.previews.findIndex((entry) => entry.url === card.dataset.preview));
  };
  el('exportResults').addEventListener('click', openFromCard);

  el('exportHistory').addEventListener('click', (event) => {
    const forget = event.target.closest('[data-forget]');
    if (forget) {
      const run = state.history.find((entry) => String(entry.id) === forget.dataset.forget);
      confirmThen(
        `Delete this export of "${run?.artwork_name || 'the artwork'}"? Its ${plural(run?.files.length || 0, 'file')} will be deleted too.`,
        async () => {
          await api(`/api/print/exports/${forget.dataset.forget}`, { method: 'DELETE' });
          await loadHistory();
          toast('Export deleted.');
        },
      );
      return;
    }
    openFromCard(event);
  });

  el('exportViews').addEventListener('click', (event) => {
    const button = event.target.closest('[data-view]');
    if (button) showView(button.dataset.view);
  });

  // Moving between the results without leaving the full-screen view.
  el('printPreview').addEventListener('wheel', onPreviewWheel, { passive: false });
  el('previewPrev').addEventListener('click', (event) => {
    event.stopPropagation();
    stepPreview(-1);
  });
  el('previewNext').addEventListener('click', (event) => {
    event.stopPropagation();
    stepPreview(1);
  });

  el('confirmYes').addEventListener('click', async () => {
    const action = state.confirmAction;
    closeModal('confirmModal');
    state.confirmAction = null;
    if (!action) return;
    try {
      await action();
    } catch (error) {
      toast(error.message, true);
    }
  });

  document.querySelectorAll('[data-close]').forEach((button) => {
    button.addEventListener('click', () => closeModal(button.dataset.close));
  });
  document.querySelectorAll('.modal-backdrop, .print-preview').forEach((backdrop) => {
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) backdrop.classList.remove('is-open');
    });
  });
  document.addEventListener('keydown', (event) => {
    if (event.key === 'Escape') {
      document.querySelectorAll('.is-open').forEach((open) => open.classList.remove('is-open'));
      return;
    }
    if (!el('printPreview').classList.contains('is-open')) return;
    if (event.key === 'ArrowRight' || event.key === 'ArrowDown') stepPreview(1);
    if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') stepPreview(-1);
  });

  const drop = el('artworkDrop');
  el('artworkInput').addEventListener('change', (event) => showArtwork(event.target.files[0]));
  ['dragenter', 'dragover'].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.add('is-over');
  }));
  ['dragleave', 'drop'].forEach((name) => drop.addEventListener(name, (event) => {
    event.preventDefault();
    drop.classList.remove('is-over');
  }));
  drop.addEventListener('drop', (event) => showArtwork(event.dataTransfer?.files?.[0]));
}

async function start() {
  wire();
  try {
    await Promise.all([loadRatios(), loadSets()]);
  } catch (error) {
    toast(error.message, true);
  }
}

start();
