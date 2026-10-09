/** Project-scoped image references shared by chat streaming and history replay. */
import { CONFIG } from "./config.js";
import { projectFileUrl } from "./demo_api.js";

const IMAGE_SUFFIX = /\.(?:png|jpe?g|gif|webp|bmp|svg)$/i;
const IMAGE_END = /\.(?:png|jpe?g|gif|webp|bmp|svg)(?=$|[?#\s)\]},.;:!])/i;

function decodePath(value) {
  try {
    return decodeURIComponent(value);
  } catch {
    return "";
  }
}

/** Never turn another project's path, traversal, or an external URL into a local file. */
export function normalizeArtifactPath(value, projectId = null, origin = globalThis.location?.origin) {
  if (typeof value !== "string") return null;
  let path = value.trim().replace(/\\\//g, "/");
  if (!path || /[\x00-\x1f\x7f]/.test(path)) return null;
  if (/^(?:https?:)?\/\//i.test(path) || /^\/api\/projects\//.test(path)) {
    // Validate before URL parsing can collapse literal or encoded dot segments.
    const rawPath = decodePath(path.replace(/^(?:https?:)?\/\/[^/]+/i, "").split(/[?#]/, 1)[0]);
    if (!rawPath || /[\x00-\x1f\x7f\\]/.test(rawPath) || /(?:^|\/)\.{1,2}(?:\/|$)/.test(rawPath)) return null;
    let url;
    try {
      url = new URL(path, origin || "http://gbclaw.invalid");
    } catch {
      return null;
    }
    if (url.origin !== (origin || "http://gbclaw.invalid") || url.username || url.password) return null;
    const match = url.pathname.match(/\/api\/projects\/([^/]+)\/files\/(?:raw|download)$/);
    if (match) {
      if (!projectId || decodePath(match[1]) !== projectId) return null;
      path = url.searchParams.get("path") || "";
    } else if (url.pathname.startsWith("/artifacts/")) {
      path = decodePath(url.pathname.slice(1));
    } else {
      const staticMatch = url.pathname.match(/(?:^|\/)projects\/([^/]+)\/(artifacts\/.*)$/);
      if (!staticMatch || !projectId || decodePath(staticMatch[1]) !== projectId) return null;
      path = decodePath(staticMatch[2]);
    }
  } else {
    if (/^[a-z][a-z\d+.-]*:/i.test(path)) return null;
    path = decodePath(path.split(/[?#]/, 1)[0]);
  }
  if (!path || /[\x00-\x1f\x7f\\]/.test(path) || /%(?:2e|2f|5c|00)/i.test(path)) return null;
  if (path.startsWith("/artifacts/")) path = path.slice(1);
  if (path.startsWith("./")) path = path.slice(2);
  if (!path.startsWith("artifacts/")) {
    const match = path.match(/(?:^|\/)projects\/([^/]+)\/(artifacts\/.*)$/);
    if (!match || !projectId || match[1] !== projectId) return null;
    path = match[2];
  }
  const parts = path.split("/");
  if (parts.length < 2 || parts.some((part) => !part || part === "." || part === "..")) return null;
  return path;
}

export function normalizeArtifactImagePath(value, projectId = null, origin = globalThis.location?.origin) {
  const path = normalizeArtifactPath(value, projectId, origin);
  return path && IMAGE_SUFFIX.test(path) ? path : null;
}

function textCandidates(text) {
  const candidates = [];
  // Quotes/backticks preserve filenames containing spaces, Unicode, or parentheses.
  for (const match of text.matchAll(/`([^`\n]+)`|"([^"\n]+)"|'([^'\n]+)'|<([^<>\n]+)>/g)) {
    candidates.push({ index: match.index, value: match.slice(1).find((part) => part !== undefined) });
  }
  // Markdown destinations can contain balanced parentheses, unlike a simple PNG regex.
  for (const match of text.matchAll(/\]\(/g)) {
    const start = match.index + 2;
    let depth = 1;
    let end = start;
    for (; end < text.length && depth > 0; end += 1) {
      if (text[end] === "\\") { end += 1; continue; }
      if (text[end] === "(") depth += 1;
      if (text[end] === ")") depth -= 1;
      if (text[end] === "\n") break;
    }
    if (depth === 0) {
      let value = text.slice(start, end - 1).trim();
      value = value.replace(/\s+(?:"[^"\n]*"|'[^'\n]*')\s*$/, "");
      if (value.startsWith("<") && value.endsWith(">")) value = value.slice(1, -1);
      value = value.replace(/\\([()])/g, "$1");
      candidates.push({ index: start, value });
    }
  }
  for (const match of text.matchAll(/[^\s`"'<>|,;]+/g)) {
    let value = match[0].replace(/^[([{]+/, "");
    if (value.includes("](")) continue;
    const end = value.match(IMAGE_END);
    if (end) value = value.slice(0, end.index + end[0].length);
    candidates.push({ index: match.index, value });
  }
  return candidates.sort((left, right) => left.index - right.index);
}

/** Honor summary-image presentation; independent diagnostic reads still display normally. */
export function extractArtifactImagePaths(value, projectId = null, origin = globalThis.location?.origin) {
  const paths = new Set();
  const seen = new WeakSet();
  const add = (candidate) => {
    const path = normalizeArtifactImagePath(candidate, projectId, origin);
    if (path) paths.add(path);
  };
  const visit = (item, depth = 0) => {
    if (depth > 40 || item == null) return;
    if (typeof item === "object") {
      if (seen.has(item)) return;
      seen.add(item);
      const presentation = item.result && typeof item.result === "object" && !Array.isArray(item.result)
        ? item.result : item;
      if (presentation.presentation_policy === "references_only") return;
      if (presentation.presentation_policy === "summary_only") {
        visit(presentation.image_paths || [], depth + 1);
        return;
      }
      if (normalizeArtifactImagePath(presentation.summary_image_path, projectId, origin)) {
        visit(presentation.summary_image_path, depth + 1);
        visit(presentation.image_paths || [], depth + 1);
        return;
      }
      for (const child of Object.values(item)) visit(child, depth + 1);
    } else if (typeof item === "string") {
      const direct = normalizeArtifactImagePath(item, projectId, origin);
      const firstEnding = direct?.match(IMAGE_END);
      if (direct && firstEnding && firstEnding.index + firstEnding[0].length === direct.length) {
        paths.add(direct);
        return;
      }
      try {
        const parsed = JSON.parse(item);
        if (parsed !== item) { visit(parsed, depth + 1); return; }
      } catch { /* Truncated previews and prose are expected. */ }
      for (const candidate of textCandidates(item.replace(/\\\//g, "/"))) add(candidate.value);
    }
  };
  visit(value);
  return [...paths];
}

/** Server image references are extracted before output previews are truncated. */
export function extractToolEventImagePaths(event, projectId = null, origin = globalThis.location?.origin) {
  return extractArtifactImagePaths(
    Array.isArray(event.image_paths) ? event.image_paths : (event.output ?? event.output_text),
    projectId, origin,
  );
}

export function artifactFileUrl(path, { projectId, apiBase = "", projectsEndpoint = "/api/projects" } = {}) {
  const normalized = normalizeArtifactPath(path, projectId);
  if (!projectId || !normalized) return "";
  if (CONFIG.demoMode) return projectFileUrl(projectId, normalized);
  return `${apiBase}${projectsEndpoint}/${encodeURIComponent(projectId)}/files/raw?path=${encodeURIComponent(normalized)}`;
}

export function artifactImageUrl(path, options = {}) {
  const normalized = normalizeArtifactImagePath(path, options.projectId);
  return normalized ? artifactFileUrl(normalized, options) : "";
}

/** Honor explicit answer images/links; use tool images only as a fallback. */
export function enhanceArtifactImages(container, options = {}) {
  if (!container) return;
  const { projectId, paths = [], append = true, onOpenImage = () => {} } = options;
  const hasExplicitPresentation = [...container.querySelectorAll("img[src], a[href]")].some((element) => {
    if (element.closest("figure.chat-render")) return false;
    const source = element.getAttribute(element.tagName === "IMG" ? "src" : "href");
    return Boolean(normalizeArtifactImagePath(source, projectId));
  });
  if (hasExplicitPresentation) {
    // A final answer or a lazy trace refresh can replace an earlier fallback gallery.
    for (const figure of container.querySelectorAll("figure.chat-render")) figure.remove();
  }
  const seen = new Set();
  const bindPreview = (element, path) => {
    element.dataset.artifactPath = path;
    if (element.dataset.artifactPreviewBound) return;
    element.dataset.artifactPreviewBound = "true";
    element.addEventListener("click", (event) => {
      event.preventDefault();
      event.stopPropagation();
      const current = element.dataset.artifactPath;
      onOpenImage(artifactImageUrl(current, options), current.split("/").pop());
    });
  };
  for (const img of [...container.querySelectorAll("img")]) {
    const source = img.getAttribute("src") || "";
    const path = normalizeArtifactImagePath(source, projectId);
    if (!path) {
      // Invalid local artifact references must not become browser-relative or cross-project requests.
      if ((source.includes("artifacts/") && !/^(?:https?:)?\/\//i.test(source)) || /\/api\/projects\/[^/]+\/files\/(?:raw|download)\?/.test(source)) img.remove();
      continue;
    }
    if (seen.has(path)) {
      (img.closest("figure.chat-render") || img).remove();
      continue;
    }
    const url = artifactImageUrl(path, options);
    if (!url) { img.remove(); continue; }
    seen.add(path);
    img.src = url;
    img.alt = img.alt || path.split("/").pop();
    img.loading = "lazy";
    if (!img.closest("figure.chat-render")) img.classList.add("chat-render-inline");
    bindPreview(img, path);
  }
  for (const link of container.querySelectorAll("a[href]")) {
    const path = normalizeArtifactPath(link.getAttribute("href"), projectId);
    if (!path) continue;
    const url = artifactFileUrl(path, options);
    link.href = url;
    if (IMAGE_SUFFIX.test(path)) {
      bindPreview(link, path);
    } else {
      link.target = "_blank";
      link.rel = "noopener";
    }
  }
  if (!append || !projectId || hasExplicitPresentation) return;
  for (const path of extractArtifactImagePaths(paths, projectId)) {
    if (seen.has(path)) continue;
    seen.add(path);
    const url = artifactImageUrl(path, options);
    const name = path.split("/").pop();
    const figure = document.createElement("figure");
    figure.className = "chat-render";
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener";
    bindPreview(link, path);
    const img = document.createElement("img");
    img.src = url;
    img.alt = name;
    img.loading = "lazy";
    img.dataset.artifactPath = path;
    link.appendChild(img);
    const caption = document.createElement("figcaption");
    caption.textContent = name;
    figure.append(link, caption);
    container.appendChild(figure);
  }
}
