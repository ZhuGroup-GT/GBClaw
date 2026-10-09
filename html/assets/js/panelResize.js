/**
 * Drag-to-resize for the left files panel and right file-preview panel.
 *
 * Defaults match the CSS grid (`22vw` / `36%`). Widths are session-only —
 * a refresh restores the CSS defaults. Disabled below the mobile breakpoint
 * (900px) where the layout stacks vertically.
 */

const MOBILE_MQ = "(max-width: 900px)";
const FILES_MIN = 220;
const PREVIEW_MIN = 240;
const CHAT_MIN = 320;

/**
 * Keep header/footer/composer sizes in CSS variables so the side panels
 * can stay viewport-sized (sticky/floating) while the document scrolls.
 */
export function initChromeMetrics() {
  const root = document.documentElement;
  const header = document.querySelector("header");
  const footer = document.querySelector("footer");
  const composer = document.querySelector(".composer");

  const apply = () => {
    const headerH = header ? Math.round(header.getBoundingClientRect().height) : 0;
    const footerH = footer ? Math.round(footer.getBoundingClientRect().height) : 0;
    const composerH = composer ? Math.round(composer.getBoundingClientRect().height) : 0;
    root.style.setProperty("--header-h", `${headerH}px`);
    root.style.setProperty("--footer-h", `${footerH}px`);
    root.style.setProperty("--composer-h", `${composerH}px`);
    // Pixel height (not 100dvh) so full-page screenshot tools that
    // expand the layout viewport do not stretch the floating panels.
    const panelH = Math.max(0, Math.round(window.innerHeight - headerH - footerH));
    root.style.setProperty("--panel-h", `${panelH}px`);
  };

  const ro = new ResizeObserver(apply);
  if (header) ro.observe(header);
  if (footer) ro.observe(footer);
  if (composer) ro.observe(composer);
  window.addEventListener("resize", apply);
  window.visualViewport?.addEventListener("resize", apply);
  apply();

  return () => {
    ro.disconnect();
    window.removeEventListener("resize", apply);
    window.visualViewport?.removeEventListener("resize", apply);
  };
}

/**
 * Bind pointer/keyboard resize on the files and preview panels.
 * Returns a cleanup function.
 */
export function initPanelResize({
  workspace,
  filesPanel,
  previewPanel,
  filesResizer,
  previewResizer,
} = {}) {
  if (!workspace || !filesPanel || !filesResizer) return () => {};

  const mobileMq = window.matchMedia(MOBILE_MQ);
  let active = null; // { side, el, startX, startWidth, previewWidth, workspaceWidth }
  let cleanups = [];

  const isMobile = () => mobileMq.matches;

  const applyColumns = (filesPx, previewPx) => {
    workspace.style.setProperty("--files-col", `${Math.round(filesPx)}px`);
    if (previewPx != null && workspace.classList.contains("preview-open")) {
      workspace.style.setProperty("--preview-col", `${Math.round(previewPx)}px`);
    }
  };

  const measure = () => {
    const workspaceWidth = workspace.getBoundingClientRect().width;
    const filesWidth = filesPanel.getBoundingClientRect().width;
    const previewOpen = workspace.classList.contains("preview-open") && previewPanel && !previewPanel.hidden;
    const previewWidth = previewOpen ? previewPanel.getBoundingClientRect().width : 0;
    return { workspaceWidth, filesWidth, previewOpen, previewWidth };
  };

  const clampFiles = (width, workspaceWidth, previewOpen, previewWidth) => {
    const reserved = CHAT_MIN + (previewOpen ? Math.max(previewWidth, PREVIEW_MIN) : 0);
    const max = Math.max(FILES_MIN, workspaceWidth - reserved);
    return Math.min(Math.max(width, FILES_MIN), max);
  };

  const clampPreview = (width, workspaceWidth, filesWidth) => {
    const reserved = CHAT_MIN + Math.max(filesWidth, FILES_MIN);
    const max = Math.max(PREVIEW_MIN, workspaceWidth - reserved);
    return Math.min(Math.max(width, PREVIEW_MIN), max);
  };

  const endDrag = () => {
    if (!active) return;
    active.el?.classList.remove("is-active");
    document.body.classList.remove("is-panel-resizing");
    active = null;
  };

  const onPointerMove = (event) => {
    if (!active) return;
    const dx = event.clientX - active.startX;
    if (active.side === "files") {
      const next = clampFiles(
        active.startWidth + dx,
        active.workspaceWidth,
        active.previewOpen,
        active.previewWidth,
      );
      applyColumns(next, active.previewOpen ? active.previewWidth : null);
    } else if (active.side === "preview") {
      // Dragging the left edge of the preview: moving right shrinks preview.
      const next = clampPreview(
        active.startWidth - dx,
        active.workspaceWidth,
        active.filesWidth,
      );
      applyColumns(active.filesWidth, next);
    }
  };

  const onPointerUp = () => {
    endDrag();
  };

  const startDrag = (side, el, event) => {
    if (isMobile()) return;
    if (side === "preview" && (!previewPanel || previewPanel.hidden)) return;
    event.preventDefault();
    const { workspaceWidth, filesWidth, previewOpen, previewWidth } = measure();
    active = {
      side,
      el,
      startX: event.clientX,
      startWidth: side === "files" ? filesWidth : previewWidth,
      filesWidth,
      previewWidth,
      previewOpen,
      workspaceWidth,
    };
    // Lock current widths to px so the first drag frame doesn't jump from vw/%.
    applyColumns(filesWidth, previewOpen ? previewWidth : null);
    el.classList.add("is-active");
    document.body.classList.add("is-panel-resizing");
  };

  const onKeyResize = (side, event) => {
    if (isMobile()) return;
    const step = event.shiftKey ? 40 : 16;
    let delta = 0;
    if (event.key === "ArrowLeft") delta = -step;
    else if (event.key === "ArrowRight") delta = step;
    else return;
    event.preventDefault();
    const { workspaceWidth, filesWidth, previewOpen, previewWidth } = measure();
    if (side === "files") {
      // ArrowRight widens the files panel.
      const next = clampFiles(filesWidth + delta, workspaceWidth, previewOpen, previewWidth);
      applyColumns(next, previewOpen ? previewWidth : null);
    } else if (side === "preview" && previewOpen) {
      // ArrowRight widens the preview (moves its left edge left).
      const next = clampPreview(previewWidth + delta, workspaceWidth, filesWidth);
      applyColumns(filesWidth, next);
    }
  };

  const bindResizer = (el, side) => {
    if (!el) return;
    const onPointerDown = (event) => {
      if (event.button != null && event.button !== 0) return;
      startDrag(side, el, event);
      try {
        el.setPointerCapture(event.pointerId);
      } catch {
        /* ignore */
      }
    };
    const onKeyDown = (event) => onKeyResize(side, event);
    el.addEventListener("pointerdown", onPointerDown);
    el.addEventListener("keydown", onKeyDown);
    cleanups.push(() => {
      el.removeEventListener("pointerdown", onPointerDown);
      el.removeEventListener("keydown", onKeyDown);
    });
  };

  bindResizer(filesResizer, "files");
  bindResizer(previewResizer, "preview");

  document.addEventListener("pointermove", onPointerMove);
  document.addEventListener("pointerup", onPointerUp);
  document.addEventListener("pointercancel", onPointerUp);
  cleanups.push(() => {
    document.removeEventListener("pointermove", onPointerMove);
    document.removeEventListener("pointerup", onPointerUp);
    document.removeEventListener("pointercancel", onPointerUp);
  });

  // If the viewport crosses into mobile, drop any custom widths so the
  // stacked layout uses the CSS defaults again.
  const onMqChange = () => {
    if (!isMobile()) return;
    endDrag();
    workspace.style.removeProperty("--files-col");
    workspace.style.removeProperty("--preview-col");
  };
  if (typeof mobileMq.addEventListener === "function") {
    mobileMq.addEventListener("change", onMqChange);
    cleanups.push(() => mobileMq.removeEventListener("change", onMqChange));
  } else if (typeof mobileMq.addListener === "function") {
    mobileMq.addListener(onMqChange);
    cleanups.push(() => mobileMq.removeListener(onMqChange));
  }

  return () => {
    endDrag();
    for (const fn of cleanups) fn();
    cleanups = [];
  };
}
