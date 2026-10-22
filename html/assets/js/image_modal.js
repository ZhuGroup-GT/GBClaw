/** Full-screen image viewer with zoom (wheel + buttons) and drag-to-pan. */

const MIN_SCALE = 0.1;
const MAX_SCALE = 12;
const ZOOM_STEP = 1.2;

let modal = null;
let stage = null;
let img = null;
let nameEl = null;
let downloadEl = null;
let zoomLabel = null;

let scale = 1;
let translateX = 0;
let translateY = 0;
let dragging = false;
let dragStartX = 0;
let dragStartY = 0;
let originX = 0;
let originY = 0;
let initialized = false;

function applyTransform() {
  if (!img) return;
  // Force top-left origin from JS so the cursor-pivot zoom math holds even if the
  // stylesheet (transform-origin: 0 0) failed to load or was cached out.
  img.style.transformOrigin = "0 0";
  img.style.transform = `translate(${translateX}px, ${translateY}px) scale(${scale})`;
  if (zoomLabel) zoomLabel.textContent = `${Math.round(scale * 100)}%`;
  if (stage) stage.classList.toggle("is-zoomed", scale > 1);
}

function resetView() {
  scale = 1;
  translateX = 0;
  translateY = 0;
  applyTransform();
}

function clampScale(value) {
  return Math.min(MAX_SCALE, Math.max(MIN_SCALE, value));
}

function zoomTo(nextScale, pivotClientX, pivotClientY) {
  const stageRect = stage.getBoundingClientRect();
  const imgRect = img.getBoundingClientRect();
  const clamped = clampScale(nextScale);
  const ratio = clamped / scale;
  if (ratio === 1) return;

  // transform-origin is top-left, so scaling never moves the image's top-left
  // corner; compute the pivot offset relative to the currently-rendered image
  // so the point under the cursor (or the stage center) stays put.
  const pivotX = (pivotClientX ?? stageRect.left + stageRect.width / 2) - stageRect.left;
  const pivotY = (pivotClientY ?? stageRect.top + stageRect.height / 2) - stageRect.top;
  const curLeft = imgRect.left - stageRect.left;
  const curTop = imgRect.top - stageRect.top;

  translateX += (pivotX - curLeft) * (1 - ratio);
  translateY += (pivotY - curTop) * (1 - ratio);
  scale = clamped;
  applyTransform();
}

function onWheel(event) {
  event.preventDefault();
  const factor = event.deltaY < 0 ? ZOOM_STEP : 1 / ZOOM_STEP;
  zoomTo(scale * factor, event.clientX, event.clientY);
}

function onPointerDown(event) {
  if (event.button !== 0) return;
  dragging = true;
  dragStartX = event.clientX;
  dragStartY = event.clientY;
  originX = translateX;
  originY = translateY;
  stage.setPointerCapture?.(event.pointerId);
  stage.classList.add("is-dragging");
}

function onPointerMove(event) {
  if (!dragging) return;
  translateX = originX + (event.clientX - dragStartX);
  translateY = originY + (event.clientY - dragStartY);
  applyTransform();
}

function onPointerUp(event) {
  if (!dragging) return;
  dragging = false;
  stage.releasePointerCapture?.(event.pointerId);
  stage.classList.remove("is-dragging");
}

function onKeydown(event) {
  if (modal?.hidden) return;
  if (event.key === "Escape") closeImageModal();
  else if (event.key === "+" || event.key === "=") zoomTo(scale * ZOOM_STEP);
  else if (event.key === "-") zoomTo(scale / ZOOM_STEP);
  else if (event.key === "0") resetView();
}

export function initImageModal() {
  if (initialized) return;
  modal = document.getElementById("image-modal");
  if (!modal) return;
  stage = document.getElementById("image-modal-stage");
  img = document.getElementById("image-modal-img");
  nameEl = document.getElementById("image-modal-name");
  downloadEl = document.getElementById("image-modal-download");
  zoomLabel = document.getElementById("image-zoom-level");

  document.getElementById("image-modal-backdrop")?.addEventListener("click", closeImageModal);
  document.getElementById("image-modal-close")?.addEventListener("click", closeImageModal);
  document.getElementById("image-zoom-in")?.addEventListener("click", () => zoomTo(scale * ZOOM_STEP));
  document.getElementById("image-zoom-out")?.addEventListener("click", () => zoomTo(scale / ZOOM_STEP));
  document.getElementById("image-zoom-reset")?.addEventListener("click", resetView);

  stage?.addEventListener("wheel", onWheel, { passive: false });
  stage?.addEventListener("pointerdown", onPointerDown);
  stage?.addEventListener("pointermove", onPointerMove);
  stage?.addEventListener("pointerup", onPointerUp);
  stage?.addEventListener("pointercancel", onPointerUp);
  img?.addEventListener("dblclick", () => (scale > 1 ? resetView() : zoomTo(2)));
  document.addEventListener("keydown", onKeydown);

  initialized = true;
}

export function openImageModal(url, name = "") {
  if (!initialized) initImageModal();
  if (!modal || !img) {
    window.open(url, "_blank", "noopener");
    return;
  }
  img.src = url;
  img.alt = name;
  if (nameEl) nameEl.textContent = name;
  if (downloadEl) {
    downloadEl.href = url;
    downloadEl.download = name || "image";
  }
  resetView();
  modal.hidden = false;
  document.body.classList.add("modal-open");
}

export function closeImageModal() {
  if (!modal) return;
  modal.hidden = true;
  document.body.classList.remove("modal-open");
  if (img) img.src = "";
}
