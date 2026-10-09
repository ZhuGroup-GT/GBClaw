/** Browser controls for the Qt-free DichromaticMap geometry service. */
import { CONFIG } from "./config.js";

const COLORS = ["#1677d2", "#e35d35"];
const EDGE_COLORS = ["#a52663", "#15784b", "#8a5b12", "#6245ac"];
const PRESET_AXES = ["100", "110", "111", "112"];
const clamp = (value, low, high) => Math.min(high, Math.max(low, value));

export function mapTransform(bounds, width, height, view = {}) {
  const scale = Math.min((width - 60) / (bounds[1] - bounds[0]),
    (height - 60) / (bounds[3] - bounds[2])) * (view.zoom || 1);
  const centerX = (bounds[0] + bounds[1]) / 2;
  const centerY = (bounds[2] + bounds[3]) / 2;
  const x = width / 2 + (view.x || 0);
  const y = height / 2 + (view.y || 0);
  return {
    scale,
    toScreen: (point) => [x + (point[0] - centerX) * scale, y - (point[1] - centerY) * scale],
    toModel: (point) => [(point[0] - x) / scale + centerX, (y - point[1]) / scale + centerY],
  };
}

export function pointMeasurement(first, second, latticeConstant) {
  const dx = second[0] - first[0];
  const dy = second[1] - first[1];
  const distance = Math.hypot(dx, dy);
  return { dx, dy, distance_a0: distance, distance_angstrom: distance * latticeConstant };
}

/** The backend supplies display coordinates; these arrows retain their true length. */
export function edgeVectorAnnotations(diagram) {
  const finitePoint = (point) => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite);
  const measured = new Map((diagram?.edge_analysis?.edges ?? []).flatMap((edge) =>
    (edge.vectors ?? []).map((vector) => [vector.id, vector])));
  return (diagram?.annotations ?? []).filter((item) =>
    Number.isInteger(item.edge_index) && item.edge_index >= 0 && item.edge_index < 4 &&
    finitePoint(item.start) && finitePoint(item.end)).map((item) => ({ ...measured.get(item.id), ...item }));
}

export function edgeVectorCaption(annotation) {
  const grains = (annotation.grain_coordinates ?? annotation.grain_notations?.map((notation, index) =>
    ({ grain: index + 1, lattice_notation: notation })) ?? []).map((item) =>
    `G${item.grain}: ${item.rational?.is_rational === false && !String(item.lattice_notation).startsWith("≈") ? "≈ " : ""}${item.lattice_notation}`);
  const length = `${format(annotation.length_a0)} a₀` +
    (Number.isFinite(annotation.length_angstrom) ? ` (${format(annotation.length_angstrom)} Å)` : "");
  const layer = annotation.layer_name ?? (Number.isInteger(annotation.layer) ? String(annotation.layer + 1) : null);
  return `${annotation.label ?? annotation.id}${layer ? ` · Layer ${layer}` : ""} · ${length}${grains.length ? ` · ${grains.join(" · ")}` : ""}` +
    (annotation.candidate_secondary_misfit === false ? " · Edge translation; no mismatch evidence" : "");
}

export function drawEdgeVectorAnnotations(ctx, annotations, transform, width, height) {
  // A fixed search budget and screen-space occupancy grid keep thousands of
  // tied minima interactive. Crowded labels may overlap; no arrow is dropped.
  const occupied = new Set(); const columns = Math.ceil(width / 32) + 2;
  const labelCells = (x, y, textWidth) => {
    const cells = [];
    for (let row = Math.floor((y - 9) / 18); row <= Math.floor((y + 9) / 18); row += 1) {
      for (let column = Math.floor((x - 4) / 32); column <= Math.floor((x + textWidth + 4) / 32); column += 1) {
        cells.push(row * columns + column);
      }
    }
    return cells;
  };
  ctx.save(); ctx.font = "bold 11px sans-serif"; ctx.textAlign = "left"; ctx.textBaseline = "middle";
  for (const annotation of annotations) {
    const start = transform.toScreen(annotation.start); const end = transform.toScreen(annotation.end);
    const midpoint = [(start[0] + end[0]) / 2, (start[1] + end[1]) / 2];
    // Off-screen vectors remain in the legend but must not acquire misleading
    // arrows at the canvas boundary when the user pans away from the cell.
    if (Math.max(start[0], end[0]) < 0 || Math.min(start[0], end[0]) > width ||
        Math.max(start[1], end[1]) < 0 || Math.min(start[1], end[1]) > height) continue;
    const color = EDGE_COLORS[annotation.edge_index];
    const dx = end[0] - start[0]; const dy = end[1] - start[1]; const length = Math.hypot(dx, dy);
    ctx.strokeStyle = color; ctx.fillStyle = color; ctx.lineWidth = 1.8;
    ctx.beginPath(); ctx.moveTo(...start); ctx.lineTo(...end); ctx.stroke();
    if (length > 0) {
      // Shrink the arrowhead for short vectors instead of enlarging the vector.
      const head = Math.min(6, length * 0.45); const ux = dx / length; const uy = dy / length;
      ctx.beginPath(); ctx.moveTo(...end);
      ctx.lineTo(end[0] - head * ux + head * 0.4 * uy, end[1] - head * uy - head * 0.4 * ux);
      ctx.lineTo(end[0] - head * ux - head * 0.4 * uy, end[1] - head * uy + head * 0.4 * ux);
      ctx.closePath(); ctx.fill();
    }
    const text = annotation.label ?? annotation.id;
    const textWidth = ctx.measureText(text).width;
    let x = clamp(midpoint[0] + 13, 5, Math.max(5, width - textWidth - 8));
    let y = clamp(midpoint[1] - 14, 28, height - 28);
    let cells = labelCells(x, y, textWidth);
    for (let attempts = 0; attempts < 16 && cells.some((cell) => occupied.has(cell)); attempts += 1) {
      y += 18;
      if (y > height - 28) { y = 28; x = clamp(x - textWidth - 12, 5, Math.max(5, width - textWidth - 8)); }
      cells = labelCells(x, y, textWidth);
    }
    cells.forEach((cell) => occupied.add(cell));
    ctx.lineWidth = 0.8; ctx.beginPath(); ctx.moveTo(...midpoint); ctx.lineTo(x, y); ctx.stroke();
    ctx.fillStyle = "rgba(255,255,255,0.92)"; ctx.fillRect(x - 3, y - 8, textWidth + 6, 16);
    ctx.fillStyle = color; ctx.fillText(text, x, y);
  }
  ctx.restore();
}

/** Numerical viewport for the service; the camera remains in the initial map frame. */
export function viewportParameters(bounds, width, height, view = {}) {
  const transform = mapTransform(bounds, width, height, view);
  const lower = transform.toModel([0, height]);
  const upper = transform.toModel([width, 0]);
  return { center: [(lower[0] + upper[0]) / 2, (lower[1] + upper[1]) / 2],
    width: upper[0] - lower[0], height: upper[1] - lower[1] };
}

export function interactionRequestRef(event, projectId) {
  function find(value, depth = 0) {
    if (!value || depth > 8) return null;
    if (typeof value === "string") {
      try { return find(JSON.parse(value), depth + 1); } catch { return null; }
    }
    if (Array.isArray(value)) {
      for (const item of value) { const match = find(item, depth + 1); if (match) return match; }
      return null;
    }
    if (value.ui_action === "pick_dichromatic_cell" && value.project_id === projectId &&
        typeof value.request_id === "string" && value.request_id) return value;
    for (const key of ["interaction", "interactions", "result", "data", "output"]) {
      const match = find(value[key], depth + 1); if (match) return match;
    }
    return null;
  }
  const interaction = find(event);
  if (!interaction) return null;
  return { projectId, requestId: interaction.request_id,
    ...(interaction.status ? { status: interaction.status } : {}),
    ...(Number.isInteger(interaction.revision) ? { revision: interaction.revision } : {}) };
}

/** Hydrate physical settings from the server request, preserving signed axes and exact angles. */
export function prefillDichromaticForm(form, parameters) {
  const field = (name) => form.elements.namedItem(name);
  for (const name of ["lattice", "angle_deg", "lattice_constant", "width", "height", "display_rotation_deg"]) {
    if (parameters[name] !== undefined && parameters[name] !== null) field(name).value = parameters[name];
  }
  field("axis").value = Array.isArray(parameters.axis) ? parameters.axis.join(" ") : parameters.axis;
  field("preset").value = "";
  field("show_coincidence").checked = parameters.show_coincidence ?? true;
  field("show_csl_cell").checked = parameters.show_csl_cell ?? true;
  field("near_enabled").checked = parameters.local_cutoff != null;
  if (parameters.local_cutoff != null) field("local_cutoff").value = parameters.local_cutoff;
}

/** Explicit user selections may wait for the running turn, but never cross projects. */
export function selectionContinuationQueue({ getProjectId, isBusy, send }) {
  const pending = new Map(); const sent = new Set();
  return {
    enqueue(item) {
      const key = `${item.projectId}:${item.requestId}`;
      if (item.retry) sent.delete(key);
      if (!sent.has(key) && !pending.has(key)) pending.set(key, { ...item });
    },
    async drain() {
      if (isBusy()) return;
      const entry = [...pending].find(([, item]) => item.projectId === getProjectId());
      if (!entry) return;
      const [key, item] = entry;
      pending.delete(key); sent.add(key);
      if (await send(item) === false) {
        sent.delete(key); pending.set(key, item);
      }
    },
  };
}

function marker(ctx, symbol, x, y, radius, filled = true) {
  ctx.beginPath();
  if (symbol === "d") {
    ctx.moveTo(x, y - radius); ctx.lineTo(x + radius, y);
    ctx.lineTo(x, y + radius); ctx.lineTo(x - radius, y); ctx.closePath();
  } else if (symbol === "s") {
    ctx.rect(x - radius, y - radius, radius * 2, radius * 2);
  } else if (["t", "t1", "t2", "t3"].includes(symbol)) {
    const rotation = { t: -Math.PI / 2, t1: Math.PI / 2, t2: Math.PI, t3: 0 }[symbol];
    for (let i = 0; i < 3; i += 1) {
      const angle = rotation + i * Math.PI * 2 / 3;
      const px = x + radius * Math.cos(angle); const py = y + radius * Math.sin(angle);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.closePath();
  } else if (symbol === "+" || symbol === "x") {
    const diagonal = symbol === "x";
    ctx.moveTo(x - radius, y - (diagonal ? radius : 0));
    ctx.lineTo(x + radius, y + (diagonal ? radius : 0));
    ctx.moveTo(x - (diagonal ? radius : 0), y + radius);
    ctx.lineTo(x + (diagonal ? radius : 0), y - radius);
    ctx.stroke(); return;
  } else if (["p", "h", "star"].includes(symbol)) {
    const count = symbol === "p" ? 5 : symbol === "h" ? 6 : 10;
    for (let i = 0; i < count; i += 1) {
      const angle = -Math.PI / 2 + i * Math.PI * 2 / count;
      const size = symbol === "star" && i % 2 ? radius / 2 : radius;
      const px = x + size * Math.cos(angle); const py = y + size * Math.sin(angle);
      if (i === 0) ctx.moveTo(px, py); else ctx.lineTo(px, py);
    }
    ctx.closePath();
  } else {
    ctx.arc(x, y, radius, 0, Math.PI * 2);
  }
  if (filled) ctx.fill(); else ctx.stroke();
  if (symbol?.startsWith("number:")) {
    ctx.save(); ctx.fillStyle = filled ? "white" : ctx.strokeStyle; ctx.textAlign = "center"; ctx.textBaseline = "middle";
    ctx.font = `${Math.max(7, radius * 1.3)}px sans-serif`;
    ctx.fillText(symbol.slice(7), x, y); ctx.restore();
  }
}

function format(value) {
  return Number(value).toLocaleString("en", { maximumFractionDigits: 5 });
}

async function apiJson(path, { body, signal } = {}) {
  const response = await fetch(`${CONFIG.apiBase}${path}`, {
    method: body === undefined ? "GET" : "POST",
    ...(body === undefined ? {} : { headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }),
    signal,
  });
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const detail = data.detail ?? data.error;
    const message = Array.isArray(detail) ? detail.map((item) => item.msg || String(item)).join("; ")
      : typeof detail === "string" ? detail : `Request failed (HTTP ${response.status})`;
    throw new Error(message);
  }
  return data;
}

export function initDichromaticWidget({ getProjectId, ensureProject, onSaved, prepareChat, continueChat } = {}) {
  const root = document.createElement("div");
  root.className = "dmap-widget";
  // This template is constant. Server values and user content only enter textContent.
  root.innerHTML = `
    <button type="button" class="dmap-bubble" aria-controls="dmap-panel" aria-expanded="false" title="Open DichromaticMap">
      <span class="dmap-bubble-mark" aria-hidden="true"><i></i><i></i></span><span>Dichromatic map</span>
    </button>
    <section id="dmap-panel" class="dmap-panel" role="dialog" aria-modal="false" aria-labelledby="dmap-title" hidden>
      <div class="dmap-header">
        <div><h2 id="dmap-title">DichromaticMap</h2><p>Explore the two projected grains</p></div>
        <div class="dmap-header-actions">
          <button type="button" data-action="expand" aria-label="Expand window" title="Expand window">⛶</button>
          <button type="button" data-action="minimize" aria-label="Minimize to bubble" title="Minimize to bubble">−</button>
        </div>
      </div>
      <div class="dmap-body">
        <form class="dmap-form">
          <div class="dmap-fields">
            <label>Lattice<select name="lattice"><option>FCC</option><option>BCC</option><option>SC</option></select></label>
            <label>Tilt axis [h k l]
              <select name="axis_choice" aria-label="Tilt axis preset">
                <option value="100">[1 0 0]</option><option value="110" selected>[1 1 0]</option>
                <option value="111">[1 1 1]</option><option value="112">[1 1 2]</option>
                <option value="custom">Custom axis…</option>
              </select>
              <input name="axis" value="110" aria-label="Custom tilt axis indices" placeholder="e.g. 1 2 3" required maxlength="40" spellcheck="false" hidden />
            </label>
            <label>Angle (°)<input name="angle_deg" type="number" min="0" max="180" step="any" placeholder="Auto preset" /></label>
            <label class="dmap-presets-field">CSL preset<select name="preset"><option value="">Auto / custom angle</option></select></label>
            <label>a₀ (Å)<input name="lattice_constant" type="number" value="1" min="0.0001" max="1000" step="any" required /></label>
            <label>Display rotation (°)<input name="display_rotation_deg" type="number" value="0" min="-180" max="180" step="any" required /></label>
            <label>Width (a₀)<input name="width" type="number" value="12" min="0.1" max="100" step="any" required /></label>
            <label>Height (a₀)<input name="height" type="number" value="9" min="0.1" max="100" step="any" required /></label>
          </div>
          <div class="dmap-display-options">
            <label><input name="show_coincidence" type="checkbox" checked /> Coincidence sites</label>
            <label><input name="show_csl_cell" type="checkbox" checked /> CSL cell</label>
            <label><input name="near_enabled" type="checkbox" /> Near pairs</label>
            <label class="dmap-near-distance" hidden>Cutoff (a₀)<input name="local_cutoff" type="number" value="0.08" min="0.0001" max="0.5" step="any" /></label>
          </div>
          <div class="dmap-layers" aria-label="Visible layers in each grain"></div>
          <div class="dmap-generate-row"><span class="dmap-geometry-note"></span><button type="submit" class="dmap-primary">Update map</button></div>
        </form>
        <div class="dmap-view-tools" role="toolbar" aria-label="Map view controls">
          <button type="button" data-action="zoom-out" aria-label="Zoom out">−</button>
          <button type="button" data-action="zoom-in" aria-label="Zoom in">+</button>
          <button type="button" data-action="fit">Fit map</button>
          <button type="button" data-action="measure" aria-pressed="false">Measure atoms</button>
          <button type="button" data-action="clear-measure">Clear</button>
          <button type="button" data-action="pick-cell">Pick near-CSL cell</button>
          <span class="dmap-view-hint">Drag to pan · scroll to zoom</span>
        </div>
        <div class="dmap-stage"><canvas tabindex="0" aria-label="Dichromatic map. Drag to pan, scroll or press plus and minus to zoom, and press 0 to fit the map."></canvas><p class="dmap-empty">Set the grain geometry to draw a map.</p></div>
        <p class="dmap-measurement" aria-live="polite"></p>
        <section class="dmap-edge-analysis" aria-label="Selected-cell edge vectors" hidden>
          <div class="dmap-edge-heading">
            <strong>Shortest edge vectors</strong>
            <label><input class="dmap-show-edge-vectors" type="checkbox" checked /> Show annotations</label>
          </div>
          <p class="dmap-edge-explanation">Arrows point from G1 to G2 at actual scale. Lattice components use each grain’s deformed conventional lattice basis. Geometric mismatches are candidate Burgers vectors; proximity alone does not establish a dislocation.</p>
          <details class="dmap-edge-details" open>
            <summary class="dmap-edge-summary"></summary>
            <div class="dmap-edge-legend"></div>
          </details>
        </section>
        <section class="dmap-cell-selection" aria-label="Select a near-CSL unit cell" hidden>
          <p class="dmap-cell-purpose"></p>
          <p>Click four highlighted near-CSL sites in perimeter order, all from the same layer. Each site represents one pair of grain positions.</p>
          <label>Picking layer<select class="dmap-pick-layer"><option value="">Choose with first site</option></select></label>
          <p class="dmap-cell-count" aria-live="polite"></p>
          <div class="dmap-cell-actions">
            <button type="button" data-action="undo-cell">Undo point</button>
            <button type="button" data-action="reset-cell">Reset points</button>
            <button type="button" data-action="cancel-cell">Cancel selection</button>
            <button type="button" data-action="confirm-cell" class="dmap-primary">Confirm cell and continue</button>
            <button type="button" data-action="resume-cell" class="dmap-primary" hidden>Continue with agent</button>
            <button type="button" data-action="new-map" hidden>Explore another map</button>
          </div>
        </section>
        <p class="dmap-status" role="status" aria-live="polite"></p>
        <div class="dmap-export">
          <label>Purpose<input name="purpose" placeholder="e.g. Compare a Σ9 tilt boundary" maxlength="2000" /></label>
          <div class="dmap-save-actions">
            <span class="dmap-project"></span>
            <button type="button" data-action="save" class="dmap-primary" disabled>Save current view</button>
            <button type="button" data-action="chat" disabled>Use in chat</button>
          </div>
          <p>Saved maps include the current visible field and its parameters. a₀ = 1 uses the normalized length scale.</p>
          <a class="dmap-saved-link" hidden target="_blank" rel="noopener">Open saved PNG</a>
        </div>
      </div>
    </section>`;
  document.body.append(root);
  const q = (selector) => root.querySelector(selector);
  const panel = q(".dmap-panel"); const bubble = q(".dmap-bubble"); const form = q("form");
  const field = (name) => form.elements.namedItem(name);
  const canvas = q("canvas"); const ctx = canvas.getContext("2d");
  const status = q(".dmap-status"); const saveButton = q('[data-action="save"]');
  const chatButton = q('[data-action="chat"]'); const measurement = q(".dmap-measurement");
  const showEdgeVectors = q(".dmap-show-edge-vectors"); showEdgeVectors.checked = true;
  let data = null; let acceptedParameters = null; let saved = null; let previewBusy = false; let saving = false;
  let requestNumber = 0; let controller = null; let debounce = null; let optionsKey = "";
  let visibleLayers = null; let colors = [...COLORS]; let measureMode = false; let selected = [];
  let view = { zoom: 1, x: 0, y: 0 }; let drag = null; let positioned = false;
  let baseBounds = null; let viewportTimer = null; let viewportSerial = 0; let viewportController = null; let viewportScheduledAt = 0;
  let interaction = null; let cellPoints = []; let interactionBusy = false; let interactionSerial = 0;
  let pickLayer = "";
  let widgetProject = getProjectId?.() ?? null;
  const candidateId = (candidate) => candidate.candidate_id ?? candidate.id;
  const requestPath = (item = interaction) => `${CONFIG.projectsEndpoint}/${encodeURIComponent(item.project_id)}/dichromatic-map/requests/${encodeURIComponent(item.request_id)}`;

  function setStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("is-error", error);
  }

  function updateButtons() {
    saveButton.disabled = !data || previewBusy || saving || !acceptedParameters || Boolean(interaction);
    chatButton.disabled = saveButton.disabled;
    saveButton.textContent = saving ? "Saving…" : "Save current view";
    for (const control of form.elements) control.disabled = saving || Boolean(interaction);
    q('[data-action="pick-cell"]').disabled = !acceptedParameters || previewBusy || saving || Boolean(interaction);
    q('[data-action="confirm-cell"]').disabled = interaction?.status !== "pending" || cellPoints.length !== 4 || interactionBusy || previewBusy;
    q('[data-action="undo-cell"]').disabled = interaction?.status !== "pending" || !cellPoints.length || interactionBusy;
    q('[data-action="reset-cell"]').disabled = q('[data-action="undo-cell"]').disabled;
    q('[data-action="cancel-cell"]').disabled = interaction?.status !== "pending" || interactionBusy;
    q('[data-action="new-map"]').hidden = !interaction || interaction.status === "pending";
    q('[data-action="resume-cell"]').hidden = interaction?.status !== "selected";
    q('[data-action="resume-cell"]').disabled = interactionBusy;
    q('[data-action="measure"]').disabled = Boolean(interaction);
    q(".dmap-pick-layer").disabled = interaction?.status !== "pending" || interactionBusy;
    q(".dmap-cell-count").textContent = interaction?.status === "pending"
      ? `${cellPoints.length} / 4 sites selected${cellPoints.length ? ` · Layer ${cellPoints[0].layer + 1}` : ""}`
      : interaction ? `Selection ${interaction.status}` : "";
  }

  function projectChanged() {
    const id = getProjectId?.();
    q(".dmap-project").textContent = id ? `Project: ${id}` : "Saving creates a project";
    saved = null;
    q(".dmap-saved-link").hidden = true;
    if (widgetProject !== id) {
      widgetProject = id; interactionSerial += 1;
      clearInteraction();
      data = null; acceptedParameters = null; baseBounds = null; selected = []; visibleLayers = null;
      updateEdgeLegend();
      requestNumber += 1; controller?.abort(); previewBusy = false;
      clearTimeout(debounce); cancelViewport();
      q(".dmap-empty").hidden = false; draw(); updateButtons();
    }
    if (id) void restorePending(id);
  }

  function parameters() {
    const number = (name) => Number(field(name).value);
    return {
      lattice: field("lattice").value,
      axis: field("axis").value.trim(),
      angle_deg: field("angle_deg").value.trim() === "" ? null : number("angle_deg"),
      lattice_constant: number("lattice_constant"), width: number("width"), height: number("height"),
      marker_size: 32, display_rotation_deg: number("display_rotation_deg"),
      g1_color: colors[0], g2_color: colors[1],
      visible_grain_layers: visibleLayers?.map((layers) => [...layers].sort((a, b) => a - b)) ?? null,
      show_coincidence: field("show_coincidence").checked,
      show_csl_cell: field("show_csl_cell").checked,
      local_cutoff: field("near_enabled").checked ? number("local_cutoff") : null,
    };
  }

  function currentMapParameters() {
    const snapshot = JSON.parse(JSON.stringify(acceptedParameters));
    if (!baseBounds) return snapshot;
    const { width, height } = canvas.getBoundingClientRect();
    const viewport = viewportParameters(baseBounds, width, height, view);
    if (viewport.width < 0.1 || viewport.height < 0.1 || viewport.width > 100 || viewport.height > 100) {
      throw new Error("Zoom to a field between 0.1 and 100 a₀ on each axis before saving or selecting a cell.");
    }
    return { ...snapshot, ...viewport };
  }

  function syncAxisChoice() {
    const normalized = field("axis").value.replace(/[\s,\[\]()]/g, "");
    field("axis_choice").value = PRESET_AXES.includes(normalized) ? normalized : "custom";
    field("axis").hidden = field("axis_choice").value !== "custom";
  }

  function updateEdgeLegend() {
    const analysis = data?.edge_analysis;
    const section = q(".dmap-edge-analysis"); const legend = q(".dmap-edge-legend");
    section.hidden = !analysis;
    legend.replaceChildren();
    if (!analysis) return;
    const annotations = edgeVectorAnnotations(data); const byId = new Map(annotations.map((item) => [item.id, item]));
    const layerCoverage = analysis.analyzed_layers?.length;
    q(".dmap-edge-summary").textContent = `${annotations.length} annotations across ${(analysis.edges ?? []).length} edges` +
      (layerCoverage ? ` · ${layerCoverage} layers checked` : "") + " · G1 → G2";
    q(".dmap-edge-explanation").textContent = "Arrows point from G1 to G2 at actual scale. Lattice components use each grain’s deformed conventional lattice basis. " +
      (layerCoverage ? "Each edge compares all crystallographic layers, pairing atoms only within the same layer. " : "") +
      "Geometric mismatches are candidate Burgers vectors; proximity alone does not establish a dislocation.";
    for (const edge of analysis.edges ?? []) {
      const group = document.createElement("div"); group.className = "dmap-edge-group";
      const heading = document.createElement("strong");
      heading.textContent = `E${edge.edge_index + 1}: ${edge.edge_label}`;
      heading.style.color = EDGE_COLORS[edge.edge_index]; group.append(heading);
      const list = document.createElement("ul");
      for (const vector of edge.vectors ?? []) {
        const annotation = byId.get(vector.id) ?? vector;
        const row = document.createElement("li");
        row.textContent = edgeVectorCaption(annotation);
        const errors = (vector.grain_coordinates ?? []).filter((item) => item.rational?.is_rational === false)
          .map((item) => `G${item.grain} reconstruction error: ${format(item.reconstruction_error_a0)} a₀`);
        if (errors.length) row.title = errors.join("; ");
        list.append(row);
      }
      if (!(edge.vectors ?? []).length) {
        const empty = document.createElement("li");
        empty.textContent = `No vector reported${edge.status ? `: ${edge.status.replaceAll("_", " ")}` : "."}`;
        list.append(empty);
      }
      group.append(list);
      if (edge.warnings?.length) {
        const warning = document.createElement("p"); warning.textContent = edge.warnings.join(" · "); group.append(warning);
      }
      legend.append(group);
    }
    if (analysis.warnings?.length) {
      const warning = document.createElement("p"); warning.className = "dmap-edge-warning";
      warning.textContent = analysis.warnings.join(" · "); legend.append(warning);
    }
  }

  function draw() {
    if (panel.hidden || !ctx) return;
    const { width, height } = canvas.getBoundingClientRect();
    if (width < 1 || height < 1) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width * dpr); canvas.height = Math.round(height * dpr);
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.fillStyle = "#ffffff"; ctx.fillRect(0, 0, width, height);
    if (!data) return;
    const transform = mapTransform(baseBounds ?? data.view_bounds, width, height, view);
    const screen = transform.toScreen;
    const line = (first, second) => {
      const a = screen(first); const b = screen(second);
      ctx.beginPath(); ctx.moveTo(...a); ctx.lineTo(...b); ctx.stroke();
    };
    const bounds = data.view_bounds;
    ctx.strokeStyle = "#e8ebef"; ctx.lineWidth = 1;
    const step = Math.pow(10, Math.floor(Math.log10(70 / transform.scale)));
    const lower = transform.toModel([0, height]); const upper = transform.toModel([width, 0]);
    for (let x = Math.ceil(lower[0] / step) * step, n = 0; x <= upper[0] && n < 300; x += step, n += 1) {
      line([x, lower[1]], [x, upper[1]]);
    }
    for (let y = Math.ceil(lower[1] / step) * step, n = 0; y <= upper[1] && n < 300; y += step, n += 1) {
      line([lower[0], y], [upper[0], y]);
    }
    ctx.strokeStyle = "#b5bdc9";
    line([bounds[0], 0], [bounds[1], 0]); line([0, bounds[2]], [0, bounds[3]]);
    ctx.save(); ctx.setLineDash([4, 4]); ctx.strokeStyle = "#c4cbd4";
    const corner = screen([bounds[0], bounds[3]]);
    ctx.strokeRect(corner[0], corner[1], (bounds[1] - bounds[0]) * transform.scale,
      (bounds[3] - bounds[2]) * transform.scale); ctx.restore();
    const radius = Math.max(1, Math.sqrt(data.parameters.marker_size / Math.PI) * 0.85);
    for (const trace of data.traces ?? []) {
      ctx.fillStyle = trace.color; ctx.strokeStyle = trace.color; ctx.lineWidth = 1.6;
      for (let i = 0; i < trace.x.length; i += 1) {
        const [x, y] = screen([trace.x[i], trace.y[i]]);
        if (x < -radius || y < -radius || x > width + radius || y > height + radius) continue;
        marker(ctx, trace.marker, x, y, radius, trace.grain === 0);
      }
    }
    ctx.strokeStyle = "#8b5cf6"; ctx.lineWidth = 1.2;
    const pairs = data.local_pairs;
    if (pairs) for (let i = 0; i < pairs.first.length; i += 1) line(pairs.first[i], pairs.second[i]);
    ctx.strokeStyle = "#208b4d"; ctx.lineWidth = 1.4;
    if (data.parameters.show_coincidence) for (const group of data.coincidences ?? []) {
      for (let i = 0; i < group.x.length; i += 1) {
        const [x, y] = screen([group.x[i], group.y[i]]);
        ctx.beginPath(); ctx.arc(x, y, radius + 2, 0, Math.PI * 2); ctx.stroke();
      }
    }
    if (data.parameters.show_csl_cell && data.csl_cell?.vertices?.length) {
      ctx.lineWidth = 2; ctx.strokeStyle = "#27364b";
      const vertices = data.selected_cell_outline ?? data.csl_cell.vertices;
      for (let i = 0; i < vertices.length; i += 1) line(vertices[i], vertices[(i + 1) % vertices.length]);
    }
    if (interaction && interaction.status !== "applied") {
      const candidates = interaction.candidates ?? interaction.pick_candidates ?? [];
      const chosen = new Set(cellPoints.map(candidateId));
      for (const candidate of candidates) {
        const [x, y] = screen(candidate.position);
        if (x < -12 || y < -12 || x > width + 12 || y > height + 12) continue;
        if (pickLayer !== "" && candidate.layer !== Number(pickLayer)) continue;
        if (cellPoints.length && candidate.layer !== cellPoints[0].layer) continue;
        ctx.strokeStyle = chosen.has(candidateId(candidate)) ? "#9b1788" : "#7951bf";
        ctx.lineWidth = chosen.has(candidateId(candidate)) ? 3 : 1.5;
        ctx.beginPath(); ctx.arc(x, y, radius + 5, 0, Math.PI * 2); ctx.stroke();
      }
      ctx.strokeStyle = "#9b1788"; ctx.lineWidth = 2;
      for (let i = 0; i < cellPoints.length; i += 1) {
        const [x, y] = screen(cellPoints[i].position);
        ctx.fillStyle = "#9b1788"; ctx.font = "bold 13px sans-serif"; ctx.textAlign = "left";
        ctx.fillText(String(i + 1), x + radius + 7, y - radius - 4);
        if (i) line(cellPoints[i - 1].position, cellPoints[i].position);
      }
      if (cellPoints.length === 4) line(cellPoints[3].position, cellPoints[0].position);
    }
    ctx.strokeStyle = "#151b26"; ctx.lineWidth = 2;
    for (const point of selected) {
      const [x, y] = screen(point.position);
      ctx.beginPath(); ctx.arc(x, y, radius + 5, 0, Math.PI * 2); ctx.stroke();
    }
    if (selected.length === 2) line(selected[0].position, selected[1].position);
    if (showEdgeVectors.checked) drawEdgeVectorAnnotations(ctx, edgeVectorAnnotations(data), transform, width, height);
    ctx.fillStyle = "#374151"; ctx.font = "11px sans-serif"; ctx.textAlign = "left";
    ctx.fillText(`x / a₀  ·  y / a₀  ·  ${format(view.zoom * 100)}%`, 12, height - 12);
    ctx.textAlign = "right";
    ctx.fillText(`${data.parameters.lattice} [${data.geometry.axis_indices.join(" ")}]  ${format(data.parameters.angle_deg)}°`, width - 12, 18);
  }

  function layerControls() {
    const container = q(".dmap-layers");
    container.replaceChildren();
    const count = data.geometry.layer_count;
    if (!visibleLayers) visibleLayers = [new Set(Array.from({ length: count }, (_, i) => i)), new Set(Array.from({ length: count }, (_, i) => i))];
    for (let grain = 0; grain < 2; grain += 1) {
      const group = document.createElement("fieldset"); const legend = document.createElement("legend");
      legend.textContent = `Grain ${grain + 1}`; group.append(legend);
      const color = document.createElement("input"); color.type = "color"; color.value = colors[grain];
      color.setAttribute("aria-label", `Grain ${grain + 1} color`);
      color.disabled = Boolean(interaction);
      color.addEventListener("input", () => { colors[grain] = color.value; schedule(); });
      group.append(color);
      for (let layer = 0; layer < count; layer += 1) {
        const label = document.createElement("label"); const check = document.createElement("input");
        check.type = "checkbox"; check.checked = visibleLayers[grain].has(layer); check.disabled = Boolean(interaction);
        check.addEventListener("change", () => {
          if (check.checked) visibleLayers[grain].add(layer); else visibleLayers[grain].delete(layer);
          schedule();
        });
        label.append(check, document.createTextNode(data.geometry.layer_names?.[layer] ?? String(layer + 1)));
        group.append(label);
      }
      container.append(group);
    }
  }

  async function loadOptions(signal) {
    const key = `${field("lattice").value}:${field("axis").value.trim()}`;
    if (key === optionsKey) return;
    const options = await apiJson(`/api/dichromatic-map/options?lattice=${encodeURIComponent(field("lattice").value)}&axis=${encodeURIComponent(field("axis").value.trim())}`, { signal });
    if (signal.aborted) return;
    optionsKey = key;
    const presets = field("preset"); presets.replaceChildren(new Option("Auto / custom angle", ""));
    for (const preset of options.presets ?? []) presets.append(new Option(preset.label ?? `Σ${preset.sigma} · ${format(preset.angle_deg)}°`, preset.angle_deg));
    const maximum = options.geometry?.angle_range?.maximum_deg ?? options.geometry?.maximum_angle_deg ?? 180;
    field("angle_deg").max = String(maximum);
  }

  async function generate() {
    clearTimeout(debounce);
    if (interaction) return;
    if (!form.reportValidity()) return;
    const serial = ++requestNumber;
    controller?.abort(); controller = new AbortController(); const { signal } = controller;
    previewBusy = true; updateButtons(); setStatus("Drawing map…");
    try {
      await loadOptions(signal);
      if (serial !== requestNumber || signal.aborted) return;
      if (!form.reportValidity()) { setStatus("Check the angle range for this tilt axis.", true); return; }
      const payload = parameters();
      const result = await apiJson("/api/dichromatic-map/preview", { body: payload, signal });
      if (serial !== requestNumber || signal.aborted) return;
      cancelViewport(); data = result; acceptedParameters = result.parameters ?? payload; saved = null; selected = [];
      baseBounds = [...result.view_bounds]; view = { zoom: 1, x: 0, y: 0 };
      measurement.textContent = ""; q(".dmap-empty").hidden = true; q(".dmap-saved-link").hidden = true;
      layerControls();
      updateEdgeLegend();
      const atomCount = data.traces.reduce((count, trace) => count + trace.x.length, 0);
      q(".dmap-geometry-note").textContent = `${data.geometry.layer_count} layers · ${atomCount.toLocaleString()} visible columns`;
      setStatus((data.warnings ?? []).join(" · ") || `Map ready · ${data.geometry.x_label} / ${data.geometry.y_label}`);
      draw();
    } catch (error) {
      if (error.name !== "AbortError" && serial === requestNumber) {
        acceptedParameters = null; setStatus(error.message, true);
      }
    } finally {
      if (serial === requestNumber) { previewBusy = false; updateButtons(); }
    }
  }

  function schedule() {
    if (interaction) return;
    cancelViewport();
    acceptedParameters = null; saved = null; updateButtons();
    q(".dmap-saved-link").hidden = true;
    // Invalidate immediately so an older response cannot become the new map
    // while the user is still editing or waiting for the debounce timer.
    requestNumber += 1; controller?.abort(); previewBusy = false;
    clearTimeout(debounce); debounce = setTimeout(generate, 350);
  }

  function fit() { view = { zoom: 1, x: 0, y: 0 }; draw(); scheduleViewport(); }

  function zoom(factor, point) {
    const rect = canvas.getBoundingClientRect();
    const x = point?.[0] ?? rect.width / 2; const y = point?.[1] ?? rect.height / 2;
    const next = clamp(view.zoom * factor, 0.25, 20); const actual = next / view.zoom;
    view.x = x - rect.width / 2 - (x - rect.width / 2 - view.x) * actual;
    view.y = y - rect.height / 2 - (y - rect.height / 2 - view.y) * actual;
    view.zoom = next; draw(); scheduleViewport();
  }

  function cancelViewport() {
    clearTimeout(viewportTimer); viewportTimer = null; viewportScheduledAt = 0;
    viewportSerial += 1; viewportController?.abort();
  }

  function scheduleViewport() {
    if (!data || !baseBounds || panel.hidden || !acceptedParameters || interactionBusy) return;
    // Debounce short gestures, but refresh at least every 200 ms during a long
    // drag so new columns appear while the pointer is still moving. The camera
    // is independent of the returned bounds and never jumps with a response.
    if (!viewportTimer) viewportScheduledAt = Date.now();
    clearTimeout(viewportTimer);
    const delay = Math.min(120, Math.max(0, 200 - (Date.now() - viewportScheduledAt)));
    viewportTimer = setTimeout(() => { viewportTimer = null; viewportScheduledAt = 0; void refreshViewport(); }, delay);
  }

  async function refreshViewport() {
    if (!data || !baseBounds || !acceptedParameters || panel.hidden || interactionBusy) return;
    const { width, height } = canvas.getBoundingClientRect();
    if (width < 61 || height < 61) return;
    const viewport = viewportParameters(baseBounds, width, height, view);
    if (viewport.width > 100 || viewport.height > 100) {
      setStatus("Zoom in to show a field of at most 100 a₀ on each axis.", true); return;
    }
    const serial = ++viewportSerial; const project = getProjectId?.(); const activeRequest = interaction;
    viewportController?.abort();
    viewportController = new AbortController();
    try {
      const result = await apiJson(activeRequest ? `${requestPath(activeRequest)}/preview` : "/api/dichromatic-map/preview", {
        body: activeRequest ? viewport : { ...acceptedParameters, ...viewport }, signal: viewportController.signal,
      });
      if (serial !== viewportSerial || project !== getProjectId?.() || activeRequest !== interaction) return;
      const diagram = result.diagram ?? result;
      data = diagram;
      updateEdgeLegend();
      if (interaction?.status === "pending") {
        const candidates = new Map((interaction.candidates ?? interaction.pick_candidates ?? []).map((item) => [candidateId(item), item]));
        for (const item of diagram.pick_candidates ?? []) candidates.set(candidateId(item), item);
        interaction.candidates = [...candidates.values()];
        updatePickingLayers();
        if (Number.isInteger(result.request_revision)) interaction.revision = result.request_revision;
      }
      setStatus((diagram.warnings ?? []).join(" · ") || (interaction?.status === "pending"
        ? "Select four highlighted sites; drag or zoom to explore more candidates."
        : interaction?.status === "applied" ? "Aligned grains updated for the current view."
        : "Map updated for the current view."));
      draw(); updateButtons();
    } catch (error) {
      if (error.name !== "AbortError" && serial === viewportSerial) setStatus(error.message, true);
    }
  }

  function selectAtom(point) {
    if (!data) return;
    const rect = canvas.getBoundingClientRect();
    const transform = mapTransform(baseBounds ?? data.view_bounds, rect.width, rect.height, view);
    let nearest = null; let distance = Math.max(12, Math.sqrt(data.parameters.marker_size) + 3);
    for (const trace of data.traces) for (let i = 0; i < trace.x.length; i += 1) {
      const position = [trace.x[i], trace.y[i]]; const rendered = transform.toScreen(position);
      const candidate = Math.hypot(point[0] - rendered[0], point[1] - rendered[1]);
      if (candidate < distance) {
        distance = candidate;
        nearest = { position, grain: trace.grain, layer: trace.layer, half_indices: trace.half_indices?.[i] };
      }
    }
    if (!nearest) { measurement.textContent = "Click closer to an atom. Hide layers to select overlapping columns."; return; }
    if (selected.length === 2) selected = [];
    selected.push(nearest);
    if (selected.length === 2) {
      const result = pointMeasurement(selected[0].position, selected[1].position, data.parameters.lattice_constant);
      measurement.textContent = `Projected distance ${format(result.distance_a0)} a₀ = ${format(result.distance_angstrom)} Å · Δx ${format(result.dx)} a₀ · Δy ${format(result.dy)} a₀`;
    } else {
      measurement.textContent = `Grain ${nearest.grain + 1}, layer ${data.geometry.layer_names?.[nearest.layer] ?? nearest.layer + 1} selected. Click a second atom.`;
    }
    draw();
  }

  function open() {
    panel.hidden = false; bubble.hidden = true; bubble.setAttribute("aria-expanded", "true");
    keepOnScreen();
  }

  function clearInteraction() {
    cancelViewport(); interaction = null; cellPoints = []; pickLayer = ""; interactionBusy = false;
    q(".dmap-cell-selection").hidden = true; canvas.classList.remove("is-picking-cell");
    updateButtons();
  }

  function acceptInteraction(item) {
    interaction = item;
    updatePickingLayers();
    q(".dmap-cell-selection").hidden = false;
    q(".dmap-cell-purpose").textContent = item.purpose || "Select a unit cell for the agent.";
    canvas.classList.toggle("is-picking-cell", item.status === "pending");
    updateButtons();
  }

  function updatePickingLayers() {
    const selector = q(".dmap-pick-layer");
    const layers = [...new Set((interaction?.candidates ?? interaction?.pick_candidates ?? []).map((candidate) => candidate.layer))].sort((a, b) => a - b);
    selector.replaceChildren(new Option("Choose with first site", ""), ...layers.map((layer) => new Option(`Layer ${layer + 1}`, String(layer))));
    selector.value = pickLayer;
  }

  async function openRequest({ projectId, requestId }) {
    if (!projectId || projectId !== getProjectId?.()) return;
    const serial = ++interactionSerial;
    cancelViewport(); requestNumber += 1; controller?.abort(); clearTimeout(debounce);
    controller = new AbortController(); const { signal } = controller;
    previewBusy = true; open(); updateButtons(); setStatus("Loading the agent's cell selection request…");
    try {
      const path = requestPath({ project_id: projectId, request_id: requestId });
      const item = await apiJson(path, { signal });
      if (serial !== interactionSerial || projectId !== getProjectId?.()) return;
      if (item.project_id !== projectId || item.request_id !== requestId) throw new Error("Cell request does not belong to the current project.");
      if (!["pending", "selected", "applied"].includes(item.status)) throw new Error(`This cell request is already ${item.status}.`);
      cellPoints = []; pickLayer = ""; selected = []; measureMode = false; canvas.classList.remove("is-measuring");
      q('[data-action="measure"]').setAttribute("aria-pressed", "false"); measurement.textContent = "";
      acceptInteraction(item);
      const p = item.parameters;
      prefillDichromaticForm(form, p);
      syncAxisChoice();
      await loadOptions(signal);
      if (serial !== interactionSerial || projectId !== getProjectId?.()) return;
      q(".dmap-near-distance").hidden = !field("near_enabled").checked;
      colors = [p.g1_color ?? COLORS[0], p.g2_color ?? COLORS[1]];
      visibleLayers = p.visible_grain_layers?.map((layers) => new Set(layers)) ?? null;
      q('[name="purpose"]').value = item.purpose ?? "";
      const response = await apiJson(`${path}/preview`, { body: { center: p.center ?? [0, 0], width: p.width, height: p.height }, signal });
      if (serial !== interactionSerial || projectId !== getProjectId?.()) return;
      const result = response.diagram ?? response;
      if (Number.isInteger(response.request_revision)) item.revision = response.request_revision;
      if (item.status === "pending") {
        const candidates = new Map((item.candidates ?? []).map((candidate) => [candidateId(candidate), candidate]));
        for (const candidate of result.pick_candidates ?? []) candidates.set(candidateId(candidate), candidate);
        item.candidates = [...candidates.values()]; updatePickingLayers();
      }
      data = result; acceptedParameters = p; baseBounds = [...result.view_bounds]; view = { zoom: 1, x: 0, y: 0 };
      showEdgeVectors.checked = true; updateEdgeLegend();
      if (item.status !== "applied" && item.selection?.candidate_ids) {
        const candidates = item.selection.candidates ?? item.candidates ?? item.pick_candidates ?? [];
        cellPoints = item.selection.candidate_ids.map((id) => candidates.find((candidate) => candidateId(candidate) === id)).filter(Boolean);
      }
      q(".dmap-empty").hidden = true; layerControls();
      saved = null; q(".dmap-saved-link").hidden = true;
      const atomCount = data.traces.reduce((count, trace) => count + trace.x.length, 0);
      q(".dmap-geometry-note").textContent = `${data.geometry.layer_count} layers · ${atomCount.toLocaleString()} visible columns`;
      setStatus(item.status === "pending" ? "Select four highlighted sites in perimeter order. Strain will be applied by the agent after confirmation."
        : item.status === "applied" ? "The agent applied strain. This numerical view shows the aligned grains and their transformed cell."
        : "The selected cell is saved. The agent can now apply strain.");
      draw();
    } catch (error) {
      if (error.name !== "AbortError" && serial === interactionSerial) { clearInteraction(); acceptedParameters = null; setStatus(error.message, true); }
    } finally {
      if (serial === interactionSerial) { previewBusy = false; updateButtons(); scheduleViewport(); }
    }
  }

  async function restorePending(project) {
    const serial = interactionSerial;
    try {
      const responses = await Promise.all(["pending", "selected"].map((state) =>
        apiJson(`${CONFIG.projectsEndpoint}/${encodeURIComponent(project)}/dichromatic-map/requests?status=${state}`)));
      if (serial !== interactionSerial || project !== getProjectId?.() || interaction) return;
      const requests = responses.flatMap((response) => Array.isArray(response) ? response : response.requests ?? [])
        .sort((a, b) => String(b.updated_at ?? b.created_at ?? "").localeCompare(String(a.updated_at ?? a.created_at ?? "")));
      if (requests.length) await openRequest({ projectId: project, requestId: requests[0].request_id });
    } catch (error) {
      if (project === getProjectId?.() && !panel.hidden) setStatus(error.message, true);
    }
  }

  async function createCellRequest() {
    if (!acceptedParameters || interaction || previewBusy || saving) return;
    const purpose = q('[name="purpose"]').value.trim() || "Select a near-CSL unit cell and apply strain to align the two grains.";
    interactionBusy = true; previewBusy = true; updateButtons();
    try {
      const snapshot = { ...currentMapParameters(), local_cutoff: acceptedParameters.local_cutoff ?? 0.08 };
      const project = await ensureProject?.() ?? getProjectId?.();
      if (!project) throw new Error("Open a project before selecting a cell.");
      const result = await apiJson(`${CONFIG.projectsEndpoint}/${encodeURIComponent(project)}/dichromatic-map/requests`, { body: { parameters: snapshot, purpose } });
      if (getProjectId?.() !== project) return;
      interactionBusy = false; await openRequest({ projectId: project, requestId: result.request_id });
    } catch (error) { setStatus(error.message, true); }
    finally { interactionBusy = false; previewBusy = false; updateButtons(); }
  }

  function selectCellSite(point) {
    if (interaction?.status !== "pending" || interactionBusy || previewBusy || !data) return;
    const { width, height } = canvas.getBoundingClientRect();
    const transform = mapTransform(baseBounds, width, height, view);
    let nearest = null; let distance = 15;
    for (const item of interaction.candidates ?? interaction.pick_candidates ?? []) {
      if (pickLayer !== "" && item.layer !== Number(pickLayer)) continue;
      if (cellPoints.length && item.layer !== cellPoints[0].layer) continue;
      const screen = transform.toScreen(item.position); const delta = Math.hypot(point[0] - screen[0], point[1] - screen[1]);
      if (delta < distance) { nearest = item; distance = delta; }
    }
    if (!nearest) { setStatus("Click a highlighted near-CSL site in the same layer as the first point.", true); return; }
    if (pickLayer === "") { pickLayer = String(nearest.layer); q(".dmap-pick-layer").value = pickLayer; }
    const previous = cellPoints.findIndex((item) => candidateId(item) === candidateId(nearest));
    if (previous >= 0) cellPoints.splice(previous, 1);
    else if (cellPoints.length < 4) cellPoints.push(nearest);
    else { setStatus("Four sites are selected. Undo a point or confirm the cell."); return; }
    setStatus(cellPoints.length === 4 ? "Check the numbered perimeter, then confirm the cell. The backend will validate the geometry." : "Continue around the cell perimeter. Click a selected site to remove it.");
    updateButtons(); draw();
  }

  async function submitCell(cancel = false) {
    if (interaction?.status !== "pending" || interactionBusy || (!cancel && cellPoints.length !== 4)) return;
    const current = interaction; const project = current.project_id;
    if (project !== getProjectId?.()) return;
    cancelViewport(); interactionBusy = true; updateButtons();
    try {
      const result = await apiJson(`${requestPath(current)}/${cancel ? "cancel" : "selection"}`, { body: {
        expected_revision: current.revision,
        ...(cancel ? {} : { candidate_ids: cellPoints.map(candidateId) }),
      } });
      if (interaction !== current || project !== getProjectId?.()) return;
      acceptInteraction(result);
      onSaved?.(result);
      if (cancel) { clearInteraction(); setStatus("Cell selection cancelled."); layerControls(); return; }
      setStatus("Cell saved. Continuing the agent with the selected site IDs…");
      minimize();
      continueSelected(result);
    } catch (error) {
      if (interaction === current && project === getProjectId?.()) {
        setStatus(error.message, true);
        // A concurrent tab/viewport may have advanced the revision. Keep the
        // user's points, refresh authority, and require another explicit click.
        try {
          const latest = await apiJson(requestPath(current));
          if (interaction === current && project === getProjectId?.()) acceptInteraction(latest);
        } catch { /* Keep the original actionable selection error. */ }
      }
    } finally { interactionBusy = false; updateButtons(); draw(); }
  }

  function continueSelected(item = interaction, retry = false) {
    if (item?.status !== "selected" || item.project_id !== getProjectId?.()) return;
    const plan = item.plan_id && item.step_id ? ` Resume the original plan ${item.plan_id}, step ${item.step_id}; it was waiting for this selection.` : "";
    minimize();
    continueChat?.({ projectId: item.project_id, requestId: item.request_id, retry,
      text: `I confirmed the near-CSL unit cell in the DichromaticMap widget. Request: ${item.request_id}. Read the saved selection with get_dichromatic_cell_selection and continue the requested strain calculation using apply_dichromatic_cell_strain.${plan} Purpose: ${item.purpose || "Align the selected cell."}` });
  }

  async function save(useInChat = false) {
    if (!acceptedParameters || saving || previewBusy) return;
    const purpose = q('[name="purpose"]').value.trim() || "Interactively inspect a projected dichromatic grain map.";
    saving = true; updateButtons();
    try {
      const snapshot = currentMapParameters();
      const project = await ensureProject?.() ?? getProjectId?.();
      if (!project) throw new Error("Open a project before saving this map.");
      const signature = JSON.stringify({ project, snapshot, purpose });
      if (saved?.signature !== signature) {
        setStatus("Saving map and its purpose to the project…");
        const result = await apiJson(`${CONFIG.projectsEndpoint}/${encodeURIComponent(project)}/dichromatic-map/export`, { body: { parameters: snapshot, purpose } });
        if (result.status !== "completed") throw new Error(result.error || "The map could not be saved.");
        if (getProjectId?.() !== project) {
          setStatus(`Saved to project ${project}. Open that project to use this map in chat.`); return;
        }
        saved = { signature, result, project };
        onSaved?.(result);
      }
      const result = saved.result;
      const path = result.result?.image_paths?.[0];
      const link = q(".dmap-saved-link");
      if (path) {
        link.href = `${CONFIG.apiBase}${CONFIG.projectsEndpoint}/${encodeURIComponent(project)}/files/raw?path=${encodeURIComponent(path)}`;
        link.hidden = false;
      }
      setStatus(`Saved map · ${result.run_id}`);
      if (useInChat) {
        const details = [
          "Please help me interpret this dichromatic map.",
          `Purpose: ${purpose}`,
          `Project run: ${result.run_id}`,
          ...(path ? [`Image: ${path}`] : []),
          `Parameters: ${JSON.stringify(result.result?.parameters ?? snapshot)}`,
        ];
        if (selected.length === 2) details.push(`Selected projected atoms: ${JSON.stringify(selected)}`, measurement.textContent);
        minimize();
        prepareChat?.(details.join("\n"));
      }
    } catch (error) {
      setStatus(error.message, true);
    } finally {
      saving = false; updateButtons();
    }
  }

  function minimize() {
    cancelViewport();
    panel.hidden = true; bubble.hidden = false; bubble.setAttribute("aria-expanded", "false"); bubble.focus();
  }

  function keepOnScreen() {
    if (!positioned || panel.hidden || panel.classList.contains("is-expanded")) return;
    const rect = panel.getBoundingClientRect();
    panel.style.left = `${clamp(rect.left, 8, Math.max(8, window.innerWidth - rect.width - 8))}px`;
    panel.style.top = `${clamp(rect.top, 8, Math.max(8, window.innerHeight - rect.height - 8))}px`;
  }

  bubble.addEventListener("click", () => {
    open();
    keepOnScreen(); field("lattice").focus();
    if (!data && !interaction) void generate(); else { draw(); scheduleViewport(); }
  });
  form.addEventListener("submit", (event) => { event.preventDefault(); void generate(); });
  showEdgeVectors.addEventListener("change", draw);
  q(".dmap-pick-layer").addEventListener("change", (event) => {
    if (interaction?.status !== "pending" || interactionBusy) return;
    pickLayer = event.target.value; cellPoints = []; updateButtons(); draw();
    setStatus("Picking layer changed. Select four sites in perimeter order.");
  });
  form.addEventListener("change", (event) => {
    if (event.target.name === "axis_choice") {
      const choice = field("axis_choice").value;
      field("axis").hidden = choice !== "custom";
      if (choice === "custom") { field("axis").focus(); return; }
      field("axis").value = choice;
    }
    if (event.target.name === "axis") syncAxisChoice();
    if (["lattice", "axis", "axis_choice"].includes(event.target.name)) {
      visibleLayers = null; field("angle_deg").value = ""; optionsKey = "";
      field("angle_deg").max = "180"; fit();
    }
    if (event.target.name === "preset") field("angle_deg").value = field("preset").value;
    if (event.target.name === "angle_deg") field("preset").value = "";
    if (event.target.name === "near_enabled") q(".dmap-near-distance").hidden = !field("near_enabled").checked;
    schedule();
  });
  panel.addEventListener("keydown", (event) => { if (event.key === "Escape") { event.preventDefault(); minimize(); } });
  root.addEventListener("click", (event) => {
    const action = event.target.closest("[data-action]")?.dataset.action;
    if (action === "minimize") minimize();
    if (action === "expand") {
      panel.classList.toggle("is-expanded");
      const expanded = panel.classList.contains("is-expanded");
      q('[data-action="expand"]').setAttribute("aria-label", expanded ? "Restore window size" : "Expand window");
      draw(); scheduleViewport();
    }
    if (action === "fit") fit();
    if (action === "zoom-in") zoom(1.25);
    if (action === "zoom-out") zoom(0.8);
    if (action === "measure") {
      measureMode = !measureMode; canvas.classList.toggle("is-measuring", measureMode);
      q('[data-action="measure"]').setAttribute("aria-pressed", String(measureMode));
      measurement.textContent = measureMode ? "Click two atoms to measure their projected separation. Hide overlapping layers if needed." : "";
    }
    if (action === "clear-measure") { selected = []; measurement.textContent = ""; draw(); }
    if (action === "pick-cell") void createCellRequest();
    if (action === "undo-cell" && !interactionBusy) { cellPoints.pop(); updateButtons(); draw(); }
    if (action === "reset-cell" && !interactionBusy) { cellPoints = []; updateButtons(); draw(); }
    if (action === "confirm-cell") void submitCell();
    if (action === "resume-cell") continueSelected(interaction, true);
    if (action === "cancel-cell") void submitCell(true);
    if (action === "new-map" && interaction?.status !== "pending") { clearInteraction(); void generate(); }
    if (action === "save") void save();
    if (action === "chat") void save(true);
  });
  canvas.addEventListener("wheel", (event) => {
    event.preventDefault(); const rect = canvas.getBoundingClientRect();
    zoom(Math.exp(-clamp(event.deltaY, -150, 150) * 0.003), [event.clientX - rect.left, event.clientY - rect.top]);
  }, { passive: false });
  canvas.addEventListener("keydown", (event) => {
    if (["+", "=", "-", "0", "ArrowLeft", "ArrowRight", "ArrowUp", "ArrowDown"].includes(event.key)) event.preventDefault();
    if (event.key === "+" || event.key === "=") zoom(1.25);
    if (event.key === "-") zoom(0.8);
    if (event.key === "0") fit();
    if (event.key === "ArrowLeft") view.x += 25;
    if (event.key === "ArrowRight") view.x -= 25;
    if (event.key === "ArrowUp") view.y += 25;
    if (event.key === "ArrowDown") view.y -= 25;
    draw(); scheduleViewport();
  });
  canvas.addEventListener("pointerdown", (event) => {
    if (event.button !== 0) return;
    canvas.setPointerCapture(event.pointerId); canvas.focus();
    drag = { x: event.clientX, y: event.clientY, startX: event.clientX, startY: event.clientY, moved: false };
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!drag) return;
    drag.moved ||= Math.hypot(event.clientX - drag.startX, event.clientY - drag.startY) > 4;
    if (drag.moved) { view.x += event.clientX - drag.x; view.y += event.clientY - drag.y; draw(); scheduleViewport(); }
    drag.x = event.clientX; drag.y = event.clientY;
  });
  canvas.addEventListener("pointerup", (event) => {
    if (drag && !drag.moved && (measureMode || interaction)) {
      const rect = canvas.getBoundingClientRect(); const point = [event.clientX - rect.left, event.clientY - rect.top];
      if (interaction) selectCellSite(point); else selectAtom(point);
    }
    drag = null;
  });
  canvas.addEventListener("pointercancel", () => { drag = null; });
  canvas.addEventListener("lostpointercapture", () => { drag = null; });
  const header = q(".dmap-header"); let windowDrag = null;
  header.addEventListener("pointerdown", (event) => {
    if (event.button !== 0 || event.target.closest("button") || panel.classList.contains("is-expanded")) return;
    const rect = panel.getBoundingClientRect();
    windowDrag = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    positioned = true; panel.style.right = "auto"; panel.style.bottom = "auto";
    panel.style.left = `${rect.left}px`; panel.style.top = `${rect.top}px`;
    header.setPointerCapture(event.pointerId);
  });
  header.addEventListener("pointermove", (event) => {
    if (!windowDrag) return;
    panel.style.left = `${event.clientX - windowDrag.x}px`; panel.style.top = `${event.clientY - windowDrag.y}px`;
    keepOnScreen();
  });
  header.addEventListener("pointerup", () => { windowDrag = null; });
  header.addEventListener("pointercancel", () => { windowDrag = null; });
  header.addEventListener("lostpointercapture", () => { windowDrag = null; });
  if (typeof ResizeObserver !== "undefined") new ResizeObserver(() => { draw(); scheduleViewport(); }).observe(q(".dmap-stage"));
  window.addEventListener("resize", () => { keepOnScreen(); draw(); scheduleViewport(); });
  syncAxisChoice(); projectChanged();
  return { projectChanged, openRequest, handleInteraction(event, projectId = getProjectId?.()) {
    const ref = interactionRequestRef(event, projectId);
    if (!ref || projectId !== getProjectId?.()) return;
    if (interaction?.request_id === ref.requestId && interaction.project_id === ref.projectId &&
        (!ref.status || ref.status === interaction.status) && (!ref.revision || ref.revision <= interaction.revision)) return;
    void openRequest(ref);
  } };
}
