/** Project file browser, text preview, and live tail for growing log files. */

import { CONFIG } from "./config.js";
import { demoFetch as fetch, projectFileUrl } from "./demo_api.js";
import { CONTENT } from "./content.js?v=2";
import { initImageModal, openImageModal } from "./image_modal.js?v=1";
import { initPanelResize } from "./panelResize.js?v=2";

const IMAGE_EXT_RE = /\.(png|jpe?g|gif|webp|bmp|svg)$/i;

function isImageFile(name) {
  return IMAGE_EXT_RE.test(name || "");
}

const filesPanelTitle = document.getElementById("files-panel-title");
const filesRefreshBtn = document.getElementById("files-refresh-btn");
const filesBreadcrumb = document.getElementById("files-breadcrumb");
const filesList = document.getElementById("files-list");
const filesEmpty = document.getElementById("files-empty");
const filePreviewPanel = document.getElementById("file-preview-panel");
const filePreviewName = document.getElementById("file-preview-name");
const filePreviewBadge = document.getElementById("file-preview-badge");
const filePreviewDownload = document.getElementById("file-preview-download");
const filePreviewClose = document.getElementById("file-preview-close");
const filePreviewContent = document.getElementById("file-preview-content");
const workspace = document.getElementById("workspace");
const filesPanel = document.getElementById("files-panel");
const filesResizer = document.getElementById("files-resizer");
const previewResizer = document.getElementById("preview-resizer");

let activeProjectId = null;
let currentDir = "";
let previewPath = null;
let previewWatch = false;
let previewPollTimer = null;
let previewLastMtime = null;
let previewLastSize = null;
let previewStickToBottom = true;
let listPollTimer = null;
let listLastFingerprint = null;
let listPollDelay = CONFIG.fileListPollMinMs;
let onSelectProject = null;

function filesApiBase(projectId) {
  return `${CONFIG.apiBase}${CONFIG.projectsEndpoint}/${encodeURIComponent(projectId)}/files`;
}

function formatSize(bytes) {
  if (bytes == null) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

function setEmptyState(message) {
  filesList.replaceChildren();
  filesBreadcrumb.replaceChildren();
  filesEmpty.hidden = false;
  filesEmpty.textContent = message;
}

function clearListPoll() {
  if (listPollTimer) {
    clearTimeout(listPollTimer);
    listPollTimer = null;
  }
}

function clearPreviewPoll() {
  if (previewPollTimer) {
    clearTimeout(previewPollTimer);
    previewPollTimer = null;
  }
}

function bumpListPollDelay(current) {
  return Math.min(Math.round(current * 1.6), CONFIG.fileListPollMaxMs);
}

function resetListPollDelay() {
  return CONFIG.fileListPollMinMs;
}

export function closeFilePreview() {
  previewPath = null;
  previewWatch = false;
  previewLastMtime = null;
  previewLastSize = null;
  clearPreviewPoll();
  filePreviewPanel.hidden = true;
  workspace?.classList.remove("preview-open");
  filePreviewContent.textContent = "";
}

function downloadUrl(projectId, path) {
  if (CONFIG.demoMode) return projectFileUrl(projectId, path);
  return `${filesApiBase(projectId)}/download?path=${encodeURIComponent(path)}`;
}

function rawUrl(projectId, path) {
  if (CONFIG.demoMode) return projectFileUrl(projectId, path);
  return `${filesApiBase(projectId)}/raw?path=${encodeURIComponent(path)}`;
}

function renderBreadcrumb() {
  filesBreadcrumb.replaceChildren();

  const rootBtn = document.createElement("button");
  rootBtn.type = "button";
  rootBtn.className = "files-crumb";
  rootBtn.textContent = CONTENT.files.root;
  rootBtn.addEventListener("click", () => loadDirectory(""));
  filesBreadcrumb.appendChild(rootBtn);

  if (!currentDir) return;

  const parts = currentDir.split("/").filter(Boolean);
  let acc = "";
  for (const part of parts) {
    const sep = document.createElement("span");
    sep.className = "files-crumb-sep";
    sep.textContent = "/";
    filesBreadcrumb.appendChild(sep);

    acc = acc ? `${acc}/${part}` : part;
    const target = acc;
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "files-crumb";
    btn.textContent = part;
    btn.addEventListener("click", () => loadDirectory(target));
    filesBreadcrumb.appendChild(btn);
  }
}

function renderEntries(entries) {
  filesList.replaceChildren();
  filesEmpty.hidden = entries.length > 0;
  if (!entries.length) {
    filesEmpty.textContent = CONTENT.files.emptyDir;
    return;
  }

  if (currentDir) {
    const parentPath = currentDir.split("/").slice(0, -1).join("/");
    const up = document.createElement("li");
    up.className = "files-item files-item-dir";
    const upBtn = document.createElement("button");
    upBtn.type = "button";
    upBtn.className = "files-item-btn";
    upBtn.innerHTML = `<span class="files-item-icon" aria-hidden="true">↩</span><span class="files-item-name">${CONTENT.files.parent}</span>`;
    upBtn.addEventListener("click", () => loadDirectory(parentPath));
    up.appendChild(upBtn);
    filesList.appendChild(up);
  }

  for (const entry of entries) {
    const item = document.createElement("li");
    item.className = `files-item files-item-${entry.entry_type}`;

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "files-item-btn";

    const icon = entry.entry_type === "dir" ? "📁" : entry.is_text ? "📄" : "📦";
    const meta =
      entry.entry_type === "file"
        ? `<span class="files-item-meta">${formatSize(entry.size)}${entry.watch ? " · live" : ""}</span>`
        : "";

    btn.innerHTML = `<span class="files-item-icon" aria-hidden="true">${icon}</span><span class="files-item-name">${entry.name}</span>${meta}`;
    const nameEl = btn.querySelector(".files-item-name");
    if (nameEl) nameEl.title = entry.name;
    btn.addEventListener("click", () => {
      if (entry.entry_type === "dir") {
        loadDirectory(entry.path);
        return;
      }
      if (entry.is_text) {
        openTextPreview(entry.path, entry.watch);
        return;
      }
      if (isImageFile(entry.name)) {
        openImageModal(rawUrl(activeProjectId, entry.path), entry.name);
        return;
      }
      window.open(downloadUrl(activeProjectId, entry.path), "_blank", "noopener");
    });

    const download = document.createElement("a");
    download.className = "files-item-download";
    download.href = downloadUrl(activeProjectId, entry.path);
    download.download = entry.name;
    download.title = CONTENT.files.download;
    download.textContent = "↓";
    download.addEventListener("click", (event) => event.stopPropagation());

    const row = document.createElement("div");
    row.className = "files-item-row";
    row.append(btn);
    if (entry.entry_type === "file") row.append(download);

    item.appendChild(row);
    filesList.appendChild(item);
  }
}

async function fetchAvailableProjects() {
  const response = await fetch(`${CONFIG.apiBase}${CONFIG.projectsEndpoint}`);
  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }
  const data = await response.json();
  return data.projects ?? [];
}

function renderAvailableProjects(projects) {
  filesList.replaceChildren();
  filesBreadcrumb.replaceChildren();
  filesEmpty.hidden = true;

  const hint = document.createElement("li");
  hint.className = "files-picker-hint";
  hint.textContent = CONTENT.files.pickProject;
  filesList.appendChild(hint);

  if (!projects.length) {
    filesEmpty.hidden = false;
    filesEmpty.textContent = CONTENT.project.emptyList;
    return;
  }

  for (const item of projects) {
    const row = document.createElement("li");
    row.className = "files-item files-project-item";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "files-item-btn";
    btn.innerHTML = `<span class="files-item-icon" aria-hidden="true">📁</span><span class="files-item-name">${item.project_id}</span>`;
    const nameEl = btn.querySelector(".files-item-name");
    if (nameEl) nameEl.title = item.project_id;
    btn.addEventListener("click", () => onSelectProject?.(item.project_id));

    row.appendChild(btn);
    filesList.appendChild(row);
  }
}

async function loadAvailableProjects() {
  clearListPoll();
  closeFilePreview();

  try {
    const projects = await fetchAvailableProjects();
    renderAvailableProjects(projects);
  } catch (err) {
    setEmptyState(CONTENT.files.loadFailed(err.message));
  }
}

async function fetchPathStat(projectId, path) {
  const url = `${filesApiBase(projectId)}/stat?path=${encodeURIComponent(path)}`;
  const response = await fetch(url);
  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }
  return response.json();
}

async function fetchDirectory(projectId, path) {
  const url = `${filesApiBase(projectId)}?path=${encodeURIComponent(path)}`;
  const response = await fetch(url);
  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }
  return response.json();
}

async function fetchFileContent(projectId, path) {
  const url = `${filesApiBase(projectId)}/content?path=${encodeURIComponent(path)}`;
  const response = await fetch(url);
  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }
  return response.json();
}

async function loadDirectory(path = "", { force = false } = {}) {
  if (!activeProjectId) {
    setEmptyState(CONTENT.files.noProject);
    return;
  }

  try {
    const data = await fetchDirectory(activeProjectId, path);
    const fingerprint = data.fingerprint ?? null;
    if (!force && fingerprint && fingerprint === listLastFingerprint && path === currentDir) {
      return false;
    }
    currentDir = data.path ?? path ?? "";
    listLastFingerprint = fingerprint;
    renderBreadcrumb();
    renderEntries(data.entries ?? []);
    return true;
  } catch (err) {
    setEmptyState(CONTENT.files.loadFailed(err.message));
    return false;
  }
}

function updatePreviewContent(text, { stickBottom = true } = {}) {
  filePreviewContent.textContent = text;
  if (stickBottom && previewStickToBottom) {
    filePreviewContent.scrollTop = filePreviewContent.scrollHeight;
  }
}

async function refreshPreview({ force = false } = {}) {
  if (!activeProjectId || !previewPath) return false;

  try {
    if (!force) {
      const stat = await fetchPathStat(activeProjectId, previewPath);
      const unchanged =
        stat.mtime === previewLastMtime && String(stat.size ?? "") === String(previewLastSize ?? "");
      previewWatch = stat.watch;
      filePreviewBadge.hidden = !stat.watch;
      if (unchanged) return false;
    }

    const data = await fetchFileContent(activeProjectId, previewPath);
    previewLastMtime = data.mtime;
    previewLastSize = data.size;
    previewWatch = data.watch;
    filePreviewBadge.hidden = !data.watch;

    const nearBottom =
      filePreviewContent.scrollHeight - filePreviewContent.scrollTop - filePreviewContent.clientHeight < 48;
    previewStickToBottom = nearBottom;
    updatePreviewContent(data.content, { stickBottom: previewStickToBottom });
    return true;
  } catch (err) {
    updatePreviewContent(CONTENT.files.previewFailed(err.message));
    return false;
  }
}

async function openTextPreview(path, watch = false) {
  if (!activeProjectId) return;

  previewPath = path;
  previewWatch = watch;
  previewStickToBottom = true;
  previewLastMtime = null;
  previewLastSize = null;
  clearPreviewPoll();

  filePreviewPanel.hidden = false;
  workspace?.classList.add("preview-open");
  filePreviewName.textContent = path;
  filePreviewDownload.href = downloadUrl(activeProjectId, path);
  filePreviewDownload.download = path.split("/").pop() ?? "download";
  filePreviewBadge.hidden = !watch;
  filePreviewContent.textContent = CONTENT.files.loading;

  await refreshPreview({ force: true });
  if (previewWatch) {
    schedulePreviewPoll();
  }
}

async function pollDirectoryIfChanged() {
  if (!activeProjectId || document.hidden) {
    scheduleListPoll();
    return;
  }

  try {
    const stat = await fetchPathStat(activeProjectId, currentDir);
    if (stat.fingerprint && stat.fingerprint === listLastFingerprint) {
      listPollDelay = bumpListPollDelay(listPollDelay);
    } else {
      listPollDelay = resetListPollDelay();
      await loadDirectory(currentDir, { force: true });
    }
  } catch {
    listPollDelay = bumpListPollDelay(listPollDelay);
  }

  scheduleListPoll();
}

function scheduleListPoll() {
  clearListPoll();
  if (CONFIG.demoMode || !activeProjectId) return;
  listPollTimer = setTimeout(pollDirectoryIfChanged, listPollDelay);
}

async function pollPreviewIfChanged() {
  if (!activeProjectId || !previewPath || document.hidden) {
    if (previewWatch) schedulePreviewPoll();
    return;
  }

  try {
    const stat = await fetchPathStat(activeProjectId, previewPath);
    previewWatch = stat.watch;
    filePreviewBadge.hidden = !stat.watch;

    const unchanged =
      stat.mtime === previewLastMtime && String(stat.size ?? "") === String(previewLastSize ?? "");
    if (!unchanged) {
      await refreshPreview({ force: true });
    }
  } catch {
    // keep polling on transient errors
  }

  if (previewWatch) {
    schedulePreviewPoll();
  }
}

function schedulePreviewPoll() {
  clearPreviewPoll();
  if (!previewPath || !previewWatch) return;
  previewPollTimer = setTimeout(pollPreviewIfChanged, CONFIG.filePreviewPollMs);
}

function startListPoll() {
  clearListPoll();
  if (!activeProjectId) return;
  listPollDelay = resetListPollDelay();
  scheduleListPoll();
}

export function setProjectSelectHandler(handler) {
  onSelectProject = handler;
}

export function initFilesPanel() {
  initImageModal();
  filesPanelTitle.textContent = CONTENT.files.title;
  filesRefreshBtn.textContent = CONTENT.files.refresh;
  filesRefreshBtn.title = CONTENT.files.refresh;
  filePreviewClose.textContent = "×";
  filePreviewClose.setAttribute("aria-label", CONTENT.files.closePreview);
  filePreviewBadge.textContent = CONTENT.files.liveBadge;
  filePreviewDownload.textContent = CONTENT.files.download;

  filesRefreshBtn.addEventListener("click", () => {
    if (activeProjectId) loadDirectory(currentDir, { force: true });
    else loadAvailableProjects();
  });
  filePreviewClose.addEventListener("click", closeFilePreview);

  filePreviewContent.addEventListener("scroll", () => {
    previewStickToBottom =
      filePreviewContent.scrollHeight - filePreviewContent.scrollTop - filePreviewContent.clientHeight < 48;
  });

  initPanelResize({
    workspace,
    filesPanel,
    previewPanel: filePreviewPanel,
    filesResizer,
    previewResizer,
  });

  document.addEventListener("visibilitychange", () => {
    if (document.hidden) return;
    if (activeProjectId) {
      loadDirectory(currentDir, { force: true });
      if (previewPath) refreshPreview({ force: true });
    }
  });
}

export function onProjectChanged(projectId) {
  activeProjectId = projectId;
  currentDir = "";
  listLastFingerprint = null;
  listPollDelay = resetListPollDelay();
  closeFilePreview();
  clearListPoll();

  if (!projectId) {
    loadAvailableProjects();
    return;
  }

  loadDirectory("", { force: true });
  startListPoll();
}

export function refreshProjectFiles() {
  if (!activeProjectId) return;
  listPollDelay = resetListPollDelay();
  loadDirectory(currentDir, { force: true });
  if (previewPath) {
    refreshPreview({ force: true });
    if (previewWatch) schedulePreviewPoll();
  }
}

/** Showcase shortcuts use the same preview and directory controls as the file browser. */
export async function openProjectFile(path) {
  if (!activeProjectId) return;
  const directory = path.split("/").slice(0, -1).join("/");
  await loadDirectory(directory, { force: true });
  if (isImageFile(path)) openImageModal(rawUrl(activeProjectId, path), path.split("/").pop());
  else await openTextPreview(path, false);
}
