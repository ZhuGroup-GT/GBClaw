/** Explore recorded project coordinates without contacting a calculation service. */
import { projectFileUrl } from "./demo_api.js";

const PROJECT_ID = "DemoProj";
const clamp = (value, minimum, maximum) => Math.min(maximum, Math.max(minimum, value));
const format = (value, digits = 5) => Number(value).toLocaleString("en", { maximumFractionDigits: digits });
const finitePoint = (point) => Array.isArray(point) && point.length === 2 && point.every(Number.isFinite);

export function initDemoDichromatic(container, snapshots = []) {
  if (!container) return null;
  container.replaceChildren();
  const root = document.createElement("section");
  root.className = "demo-dmap";
  root.setAttribute("aria-label", "Recorded dichromatic maps");
  // Only this constant template uses innerHTML; recorded data enters textContent.
  root.innerHTML = `
    <div class="demo-dmap-heading"><div><h3>Recorded dichromatic map</h3>
      <p>Explore saved coordinates from DemoProj. Drag to pan, scroll to zoom, or select two atoms to measure.</p></div>
      <span class="demo-dmap-badge">Recorded map</span></div>
    <label class="demo-dmap-source">Saved view <select aria-label="Choose a recorded dichromatic map"></select></label>
    <div class="demo-dmap-toolbar" role="group" aria-label="Map view controls">
      <button type="button" data-action="out" aria-label="Zoom out">−</button>
      <button type="button" data-action="in" aria-label="Zoom in">+</button>
      <button type="button" data-action="fit">Fit view</button>
      <button type="button" data-action="measure" aria-pressed="false">Measure two atoms</button>
      <button type="button" data-action="clear">Clear measurement</button>
      <label><input type="checkbox" data-overlay="cell" checked> Cell outline</label>
      <label><input type="checkbox" data-overlay="coincidence" checked> Coincidences</label>
      <label><input type="checkbox" data-overlay="pairs"> Near pairs</label>
    </div>
    <div class="demo-dmap-layers" role="group" aria-label="Visible grains and layers"></div>
    <div class="demo-dmap-plot"><canvas tabindex="0" aria-label="Recorded grain coordinates. Arrow keys pan, plus and minus zoom, Home fits the view."></canvas></div>
    <p class="demo-dmap-status" role="status" aria-live="polite">Loading recorded map…</p>
    <p class="demo-dmap-measurement" aria-live="polite"></p>
    <details class="demo-dmap-details"><summary>Recorded parameters and cell</summary><dl></dl></details>
    <div class="demo-dmap-downloads"><a data-download="json" download>Download original JSON</a><a data-download="png" download>Download recorded PNG</a></div>`;
  container.append(root);
  const q = (selector) => root.querySelector(selector);
  const select = q("select");
  const canvas = q("canvas");
  const context = canvas.getContext("2d");
  const status = q(".demo-dmap-status");
  const measurement = q(".demo-dmap-measurement");
  const controls = [...root.querySelectorAll("button, input")];
  const overlays = Object.fromEntries(["cell", "coincidence", "pairs"].map((name) => [name, q(`[data-overlay="${name}"]`)]));
  const cache = new Map();
  let diagram = null;
  let visibleLayers = [new Set(), new Set()];
  let view = { zoom: 1, x: 0, y: 0 };
  let selected = [];
  let measuring = false;
  let pointer = null;
  let requestSerial = 0;
  let abortController = null;
  let destroyed = false;

  function setStatus(message, error = false) {
    status.textContent = message;
    status.classList.toggle("is-error", error);
  }

  function transform() {
    const { width, height } = canvas.getBoundingClientRect();
    const bounds = diagram.view_bounds;
    const scale = Math.max(0.01, Math.min((width - 56) / (bounds[1] - bounds[0]),
      (height - 56) / (bounds[3] - bounds[2]))) * view.zoom;
    const center = [(bounds[0] + bounds[1]) / 2, (bounds[2] + bounds[3]) / 2];
    const anchor = [width / 2 + view.x, height / 2 + view.y];
    return { width, height, scale,
      screen: (point) => [anchor[0] + (point[0] - center[0]) * scale, anchor[1] - (point[1] - center[1]) * scale],
      model: (point) => [(point[0] - anchor[0]) / scale + center[0], (anchor[1] - point[1]) / scale + center[1]] };
  }

  function drawMarker(marker, x, y, radius, filled) {
    context.beginPath();
    if (marker === "d") {
      context.moveTo(x, y - radius); context.lineTo(x + radius, y);
      context.lineTo(x, y + radius); context.lineTo(x - radius, y); context.closePath();
    } else if (marker === "s") {
      context.rect(x - radius, y - radius, radius * 2, radius * 2);
    } else {
      context.arc(x, y, radius, 0, Math.PI * 2);
    }
    if (filled) context.fill(); else context.stroke();
  }

  function draw() {
    if (!diagram || !context || destroyed) return;
    const t = transform();
    if (t.width < 1 || t.height < 1) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(t.width * dpr); canvas.height = Math.round(t.height * dpr);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    const styles = getComputedStyle(root);
    const color = (token, fallback) => styles.getPropertyValue(token).trim() || fallback;
    context.fillStyle = color("--agent-bubble", "#fff"); context.fillRect(0, 0, t.width, t.height);
    const line = (first, second) => {
      context.beginPath(); context.moveTo(...t.screen(first)); context.lineTo(...t.screen(second)); context.stroke();
    };
    const lower = t.model([0, t.height]); const upper = t.model([t.width, 0]);
    const ideal = 65 / t.scale;
    const base = 10 ** Math.floor(Math.log10(ideal));
    const step = [1, 2, 5, 10].find((factor) => factor * base >= ideal) * base;
    context.strokeStyle = color("--border", "#e5e7eb"); context.lineWidth = 0.6;
    for (let x = Math.ceil(lower[0] / step) * step, n = 0; x <= upper[0] && n < 100; x += step, n++) line([x, lower[1]], [x, upper[1]]);
    for (let y = Math.ceil(lower[1] / step) * step, n = 0; y <= upper[1] && n < 100; y += step, n++) line([lower[0], y], [upper[0], y]);
    const bounds = diagram.view_bounds;
    context.save(); context.setLineDash([4, 4]);
    context.strokeStyle = color("--border-strong", "#9ca3af");
    const corner = t.screen([bounds[0], bounds[3]]);
    context.strokeRect(corner[0], corner[1], (bounds[1] - bounds[0]) * t.scale, (bounds[3] - bounds[2]) * t.scale);
    context.restore();

    if (overlays.pairs.checked && diagram.local_pairs) {
      context.strokeStyle = "#a58ad3"; context.lineWidth = 1;
      const pairs = diagram.local_pairs;
      for (let i = 0; i < pairs.first.length; i++) {
        const layer = pairs.layers?.[i];
        if (layer != null && (!visibleLayers[0].has(layer) || !visibleLayers[1].has(layer))) continue;
        line(pairs.first[i], pairs.second[i]);
      }
    }
    const radius = clamp(Math.sqrt((diagram.parameters.marker_size || 32) / Math.PI) * 0.85, 2, 5);
    for (const trace of diagram.traces) {
      if (!visibleLayers[trace.grain]?.has(trace.layer)) continue;
      context.fillStyle = trace.color || (trace.grain ? "#e35d35" : "#1677d2");
      context.strokeStyle = context.fillStyle; context.lineWidth = 1.3;
      for (let i = 0; i < trace.x.length; i++) {
        const [x, y] = t.screen([trace.x[i], trace.y[i]]);
        if (x < -radius || y < -radius || x > t.width + radius || y > t.height + radius) continue;
        drawMarker(trace.marker, x, y, radius, trace.grain === 0);
      }
    }
    if (overlays.coincidence.checked) {
      context.strokeStyle = "#3da46a"; context.lineWidth = 1.5;
      for (const group of diagram.coincidences || []) {
        if (!visibleLayers[0].has(group.layer) || !visibleLayers[1].has(group.layer)) continue;
        for (let i = 0; i < group.x.length; i++) {
          const [x, y] = t.screen([group.x[i], group.y[i]]);
          context.beginPath(); context.arc(x, y, radius + 3, 0, Math.PI * 2); context.stroke();
        }
      }
    }
    const vertices = diagram.selected_cell_outline || diagram.csl_cell?.vertices || diagram.selected_cell?.vertices;
    if (overlays.cell.checked && Array.isArray(vertices) && vertices.every(finitePoint)) {
      context.strokeStyle = color("--text", "#24283b"); context.lineWidth = 2.1;
      for (let i = 0; i < vertices.length; i++) {
        line(vertices[i], vertices[(i + 1) % vertices.length]);
        const point = t.screen(vertices[i]);
        context.fillStyle = context.strokeStyle; context.font = "bold 12px sans-serif";
        context.fillText(String(i + 1), point[0] + 7, point[1] - 7);
      }
    }
    context.strokeStyle = color("--text", "#24283b"); context.lineWidth = 2;
    for (const item of selected) {
      const point = t.screen(item.position);
      context.beginPath(); context.arc(...point, radius + 6, 0, Math.PI * 2); context.stroke();
    }
    if (selected.length === 2) line(selected[0].position, selected[1].position);
    context.fillStyle = color("--text-muted", "#6b7280"); context.font = "11px sans-serif";
    context.textAlign = "left";
    context.fillText(`x / a₀ · y / a₀ · ${format(view.zoom * 100, 0)}%`, 12, t.height - 12);
    context.textAlign = "right";
    context.fillText(`${diagram.parameters.lattice} ${diagram.geometry?.axis_label || ""} · ${format(diagram.parameters.angle_deg)}°`, t.width - 12, 19);
    context.textAlign = "left";
  }

  function clearMeasurement() {
    selected = []; measurement.textContent = measuring ? "Select two recorded atoms. Distances are projected into the map plane." : ""; draw();
  }

  function zoom(factor, anchor) {
    if (!diagram) return;
    const before = transform();
    const point = anchor || [before.width / 2, before.height / 2];
    const modelPoint = before.model(point);
    view.zoom = clamp(view.zoom * factor, 0.25, 20);
    const after = transform().screen(modelPoint);
    view.x += point[0] - after[0]; view.y += point[1] - after[1]; draw();
  }

  function measure(point) {
    if (!diagram || !measuring) return;
    const t = transform();
    let nearest = null; let distance = 14;
    for (const trace of diagram.traces) {
      if (!visibleLayers[trace.grain]?.has(trace.layer)) continue;
      for (let i = 0; i < trace.x.length; i++) {
        const position = [trace.x[i], trace.y[i]]; const screen = t.screen(position);
        const delta = Math.hypot(screen[0] - point[0], screen[1] - point[1]);
        if (delta < distance) { distance = delta; nearest = { position, grain: trace.grain, layer: trace.layer }; }
      }
    }
    if (!nearest) { measurement.textContent = "Select an atom marker to measure its projected distance."; return; }
    if (selected.length === 2) selected = [];
    selected.push(nearest);
    if (selected.length === 2) {
      const dx = selected[1].position[0] - selected[0].position[0];
      const dy = selected[1].position[1] - selected[0].position[1];
      const length = Math.hypot(dx, dy);
      const lattice = diagram.units?.lattice_constant_angstrom || diagram.parameters.lattice_constant;
      measurement.textContent = `Projected distance: ${format(length)} a₀${Number.isFinite(lattice) ? ` · ${format(length * lattice)} Å` : ""} · Δx ${format(dx)}, Δy ${format(dy)} a₀`;
    } else measurement.textContent = "First atom selected. Select a second atom.";
    draw();
  }

  function renderLayers() {
    const holder = q(".demo-dmap-layers"); holder.replaceChildren();
    visibleLayers = [new Set(), new Set()];
    for (let grain = 0; grain < 2; grain++) {
      const layers = [...new Set(diagram.traces.filter((trace) => trace.grain === grain).map((trace) => trace.layer))].sort((a, b) => a - b);
      const fieldset = document.createElement("fieldset"); const legend = document.createElement("legend");
      legend.textContent = `Grain ${grain + 1}${grain ? " · open markers" : " · filled markers"}`; fieldset.append(legend);
      const allLabel = document.createElement("label"); const all = document.createElement("input");
      all.type = "checkbox"; all.checked = true; allLabel.append(all, "All layers"); fieldset.append(allLabel);
      const boxes = layers.map((layer) => {
        visibleLayers[grain].add(layer);
        const label = document.createElement("label"); const box = document.createElement("input");
        box.type = "checkbox"; box.checked = true;
        label.append(box, `Layer ${diagram.geometry?.layer_names?.[layer] || layer + 1}`); fieldset.append(label);
        box.addEventListener("change", () => {
          if (box.checked) visibleLayers[grain].add(layer); else visibleLayers[grain].delete(layer);
          all.checked = boxes.every((item) => item.box.checked);
          all.indeterminate = !all.checked && boxes.some((item) => item.box.checked);
          clearMeasurement();
        });
        return { box, layer };
      });
      all.addEventListener("change", () => {
        all.indeterminate = false;
        for (const { box, layer } of boxes) { box.checked = all.checked; if (all.checked) visibleLayers[grain].add(layer); else visibleLayers[grain].delete(layer); }
        clearMeasurement();
      });
      holder.append(fieldset);
    }
  }

  function renderMetadata(snapshot) {
    const p = diagram.parameters;
    const dl = q("dl"); dl.replaceChildren();
    const entries = [["View", snapshot.title], ["Lattice / tilt axis", `${p.lattice} / ${diagram.geometry?.axis_label || p.axis}`],
      ["Misorientation", `${format(p.angle_deg)}°`], ["Lattice constant", `${format(p.lattice_constant)} Å`],
      ["Recorded viewport", `${format(p.width)} × ${format(p.height)} a₀`], ["Display rotation", `${format(p.display_rotation_deg || 0)}°`],
      ["Recorded atoms", diagram.traces.reduce((count, trace) => count + trace.x.length, 0).toLocaleString()],
      ["Coordinates", "Projected x, y in units of a₀; measurement gives the in-plane distance."]];
    if (diagram.csl_cell?.kind) entries.push(["Cell", diagram.csl_cell.kind]);
    if (Number.isFinite(diagram.csl_cell?.area_a0_squared)) entries.push(["Cell area", `${format(diagram.csl_cell.area_a0_squared)} a₀²`]);
    if (Number.isFinite(diagram.strain?.max_principal_strain)) entries.push(["Recorded maximum principal strain", `${format(diagram.strain.max_principal_strain * 100)}%`]);
    for (const [label, value] of entries) { const term = document.createElement("dt"); const detail = document.createElement("dd"); term.textContent = label; detail.textContent = value; dl.append(term, detail); }
    const json = q('[data-download="json"]'); json.href = projectFileUrl(PROJECT_ID, snapshot.path); json.download = snapshot.path.split("/").pop();
    const png = q('[data-download="png"]'); const pngUrl = diagram.output_files?.png ? projectFileUrl(PROJECT_ID, diagram.output_files.png) : "";
    png.hidden = !pngUrl;
    if (pngUrl) { png.href = pngUrl; png.download = diagram.output_files.png.split("/").pop(); } else png.removeAttribute("href");
  }

  async function loadSnapshot(index) {
    const snapshot = snapshots[index];
    if (!snapshot || destroyed) return;
    const serial = ++requestSerial; abortController?.abort(); abortController = new AbortController();
    controls.forEach((control) => { control.disabled = true; });
    root.setAttribute("aria-busy", "true"); setStatus("Loading recorded map…");
    try {
      const url = projectFileUrl(PROJECT_ID, snapshot.path);
      if (!url) throw new Error("The recorded map path is unavailable.");
      let result = cache.get(snapshot.path);
      if (!result) {
        const response = await fetch(url, { signal: abortController.signal });
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        result = await response.json();
        if (!Array.isArray(result.traces) || !Array.isArray(result.view_bounds) || result.view_bounds.length !== 4 || !result.view_bounds.every(Number.isFinite) || result.view_bounds[0] >= result.view_bounds[1] || result.view_bounds[2] >= result.view_bounds[3] || !result.parameters) throw new Error("This file does not contain recorded map coordinates.");
        cache.set(snapshot.path, result);
      }
      if (serial !== requestSerial || destroyed) return;
      diagram = result; view = { zoom: 1, x: 0, y: 0 }; selected = [];
      renderLayers(); renderMetadata(snapshot); clearMeasurement();
      const count = result.traces.reduce((total, trace) => total + trace.x.length, 0);
      setStatus(`${count.toLocaleString()} recorded atom columns · ${result.geometry?.layer_count || ""} layers · Pan and zoom explore the saved viewport.`);
    } catch (error) {
      if (error.name !== "AbortError" && serial === requestSerial) setStatus(`Could not load recorded map: ${error.message}`, true);
    } finally {
      if (serial === requestSerial && !destroyed) {
        controls.forEach((control) => { control.disabled = !diagram; }); root.removeAttribute("aria-busy");
      }
    }
  }

  for (let index = 0; index < snapshots.length; index++) select.append(new Option(snapshots[index].title, String(index)));
  select.addEventListener("change", () => { void loadSnapshot(Number(select.value)); });
  q('[data-action="in"]').addEventListener("click", () => zoom(1.25));
  q('[data-action="out"]').addEventListener("click", () => zoom(0.8));
  q('[data-action="fit"]').addEventListener("click", () => { view = { zoom: 1, x: 0, y: 0 }; draw(); });
  q('[data-action="clear"]').addEventListener("click", clearMeasurement);
  q('[data-action="measure"]').addEventListener("click", (event) => {
    measuring = !measuring; event.currentTarget.setAttribute("aria-pressed", String(measuring));
    canvas.classList.toggle("is-measuring", measuring); clearMeasurement();
  });
  Object.values(overlays).forEach((overlay) => overlay.addEventListener("change", draw));
  const localPoint = (event) => { const rect = canvas.getBoundingClientRect(); return [event.clientX - rect.left, event.clientY - rect.top]; };
  canvas.addEventListener("wheel", (event) => { if (!diagram) return; event.preventDefault(); zoom(Math.exp(-clamp(event.deltaY, -100, 100) * 0.004), localPoint(event)); }, { passive: false });
  canvas.addEventListener("pointerdown", (event) => {
    if (!diagram || event.button !== 0) return;
    const point = localPoint(event); pointer = { id: event.pointerId, start: point, last: point, moved: false };
    canvas.setPointerCapture(event.pointerId); canvas.focus();
  });
  canvas.addEventListener("pointermove", (event) => {
    if (!pointer || event.pointerId !== pointer.id) return;
    const point = localPoint(event);
    if (Math.hypot(point[0] - pointer.start[0], point[1] - pointer.start[1]) > 4) pointer.moved = true;
    if (pointer.moved) { view.x += point[0] - pointer.last[0]; view.y += point[1] - pointer.last[1]; draw(); }
    pointer.last = point;
  });
  canvas.addEventListener("pointerup", (event) => {
    if (!pointer || event.pointerId !== pointer.id) return;
    if (!pointer.moved) measure(localPoint(event)); pointer = null;
    if (canvas.hasPointerCapture(event.pointerId)) canvas.releasePointerCapture(event.pointerId);
  });
  canvas.addEventListener("pointercancel", () => { pointer = null; });
  canvas.addEventListener("keydown", (event) => {
    if (!diagram) return;
    if (event.key === "+" || event.key === "=") zoom(1.25);
    else if (event.key === "-") zoom(0.8);
    else if (event.key === "Home") { view = { zoom: 1, x: 0, y: 0 }; draw(); }
    else if (event.key === "ArrowLeft") { view.x += 24; draw(); }
    else if (event.key === "ArrowRight") { view.x -= 24; draw(); }
    else if (event.key === "ArrowUp") { view.y += 24; draw(); }
    else if (event.key === "ArrowDown") { view.y -= 24; draw(); }
    else return;
    event.preventDefault();
  });
  const resizeObserver = new ResizeObserver(draw); resizeObserver.observe(canvas);
  const themeObserver = new MutationObserver(draw);
  themeObserver.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme"] });
  if (!context || !snapshots.length) {
    controls.forEach((control) => { control.disabled = true; }); select.disabled = true;
    setStatus(!context ? "Canvas preview is unavailable in this browser. Download the recorded map files to inspect them." : "No recorded dichromatic maps are available.");
    q(".demo-dmap-downloads").hidden = true;
  } else void loadSnapshot(0);
  return { destroy() { destroyed = true; abortController?.abort(); resizeObserver.disconnect(); themeObserver.disconnect(); root.remove(); } };
}
