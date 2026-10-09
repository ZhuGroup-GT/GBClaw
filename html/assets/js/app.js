import { CONFIG } from "./config.js";
import { demoFetch as fetch } from "./demo_api.js";
import { initDemoShowcase, navigateDemo, filterDemoHistory } from "./demo_showcase.js";
import { CONTENT } from "./content.js?v=2";
import {
  matchSlashCommands,
  parseChatCommand,
} from "./chat_commands.js?v=1";
import {
  closeFilePreview,
  initFilesPanel,
  onProjectChanged,
  refreshProjectFiles,
  setProjectSelectHandler,
} from "./files.js?v=3";
import { openImageModal } from "./image_modal.js?v=1";
import { renderMarkdown, renderMarkdownStreaming } from "./markdown.js?v=5";
import { enhanceArtifactImages, extractArtifactImagePaths, extractToolEventImagePaths } from "./artifact_images.js?v=4";
import { applyTheme, bindThemeToggle, resolveInitialTheme } from "./theme.js?v=1";
import { initChromeMetrics } from "./panelResize.js?v=3";
import { initDichromaticWidget, selectionContinuationQueue } from "./dichromatic_widget.js?v=5";
import {
  captureScrollPosition,
  getPageScroller,
  installStickyScroll,
  restoreScrollPosition,
  scrollToBottom,
  stickToBottom,
} from "./autoScroll.js?v=2";

function newThreadId() {
  if (typeof crypto?.randomUUID === "function") {
    return crypto.randomUUID();
  }
  // HTTP on a LAN IP is not a secure context — randomUUID is unavailable there.
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (ch) => {
    const n = (Math.random() * 16) | 0;
    return (ch === "x" ? n : (n & 0x3) | 0x8).toString(16);
  });
}

let projectId = null;
let threadId = null;
let projectReady = false;
let dichromaticWidget = null;

const chatLog = document.getElementById("chat-log");
const welcome = document.getElementById("welcome");
const form = document.getElementById("chat-form");
const input = document.getElementById("user-input");
const slashMenu = document.getElementById("slash-menu");
const sendBtn = document.getElementById("send-btn");
const stopBtn = document.getElementById("stop-btn");
const planApprovalEl = document.getElementById("plan-approval");
const planApprovalTitleEl = document.getElementById("plan-approval-title");
const planApprovalMetaEl = document.getElementById("plan-approval-meta");
const planApproveBtn = document.getElementById("plan-approve-btn");
const planRequestChangesBtn = document.getElementById("plan-request-changes-btn");
const statusBadge = document.getElementById("status-badge");
const statusText = document.getElementById("status-text");
const suggestionsEl = document.getElementById("suggestions");
const projectIdEl = document.getElementById("project-id");
const projectIdHintEl = document.getElementById("project-id-hint");
const threadIdEl = document.getElementById("thread-id");
const projectSwitchBtn = document.getElementById("project-switch-btn");
const projectModal = document.getElementById("project-modal");
const projectModalBackdrop = document.getElementById("project-modal-backdrop");
const projectModalClose = document.getElementById("project-modal-close");
const projectListEl = document.getElementById("project-list");
const projectNewBtn = document.getElementById("project-new-btn");
const themeToggleBtn = document.getElementById("theme-toggle");
const themeToggleLabelEl = document.getElementById("theme-toggle-label");

let isLoading = false;
let isStopping = false;
let currentAbortController = null;
let currentRunMessage = "";
let currentLiveRun = null;
let pendingPlanDraft = null;
const cellContinuations = selectionContinuationQueue({
  getProjectId: () => projectId,
  isBusy: () => isLoading || !projectReady,
  send: ({ text, projectId: expectedProjectId }) => handleSend(text, { preserveDraft: true, expectedProjectId }),
});

function setConnected() {
  statusBadge.classList.add("connected");
  statusText.textContent = CONTENT.status.connected;
}

function setBackendNotReady() {
  statusBadge.classList.remove("connected");
  statusText.textContent = CONTENT.status.backendNotReady;
}

function initPage() {
  const { page, brand, status, welcome: welcomeCopy, chat, project, footer } = CONTENT;

  document.documentElement.lang = page.lang;
  document.title = page.title;

  const logoEl = document.getElementById("brand-logo");
  logoEl.src = brand.logoSrc;
  logoEl.alt = brand.logoAlt;
  document.getElementById("brand-name").textContent = brand.name;
  document.getElementById("brand-tagline").textContent = brand.tagline;

  statusBadge.title = status.title;
  statusText.textContent = status.loading || status.backendNotReady;

  document.getElementById("project-label").textContent = project.label;
  projectSwitchBtn.textContent = project.switch;
  document.getElementById("project-modal-title").textContent = project.modalTitle;
  document.getElementById("project-modal-hint").textContent = project.modalHint;
  projectNewBtn.textContent = project.newProject;
  projectModalClose.textContent = "×";
  projectModalClose.setAttribute("aria-label", project.close);

  document.getElementById("welcome-title").textContent = welcomeCopy.title;
  document.getElementById("welcome-description").textContent = welcomeCopy.description;

  suggestionsEl.replaceChildren(
    ...welcomeCopy.suggestions.map(({ label, prompt }) => {
      const btn = document.createElement("button");
      btn.className = "chip";
      btn.type = "button";
      btn.dataset.prompt = prompt;
      btn.textContent = label;
      return btn;
    }),
  );

  chatLog.setAttribute("aria-label", chat.logLabel);
  input.placeholder = chat.inputPlaceholder;
  input.setAttribute("aria-label", chat.inputAriaLabel);
  sendBtn.setAttribute("aria-label", chat.sendAriaLabel);
  stopBtn.setAttribute("aria-label", chat.stopAriaLabel);
  stopBtn.title = chat.stopTitle;
  planApproveBtn.textContent = CONTENT.planApproval.approve;
  planRequestChangesBtn.textContent = CONTENT.planApproval.requestChanges;

  document.getElementById("hint-prefix").textContent = chat.hintPrefix;
  document.getElementById("hint-endpoint").textContent = chat.hintEndpoint;
  document.getElementById("hint-project-prefix").textContent = chat.hintProjectPrefix;
  document.getElementById("hint-thread-prefix").textContent = chat.hintThreadPrefix;
  updateProjectDisplay();

  document.getElementById("footer-text").textContent = footer;

  // Track user scroll position so live updates can stay pinned to the
  // bottom without yanking the viewport while the user is reading.
  // Chat uses the document/viewport scroller so full-page screenshots
  // see a real page scrollbar.
  initChromeMetrics();
  installStickyScroll(getPageScroller());
}

function shortId(value) {
  if (!value) return CONTENT.chat.threadPlaceholder;
  return value.length > 12 ? `${value.slice(0, 8)}…` : value;
}

function updateProjectDisplay() {
  const displayProject = projectId ?? CONTENT.project.newSession;
  const displayThread = threadId ?? CONTENT.chat.threadPlaceholder;

  projectIdEl.textContent = displayProject;
  projectIdEl.title = projectId ?? CONTENT.project.newSession;
  projectIdHintEl.textContent = displayProject;
  projectIdHintEl.title = projectId ?? CONTENT.project.newSession;
  threadIdEl.textContent = shortId(displayThread);
  threadIdEl.title = displayThread;
}

function setProjectReady(ready) {
  projectReady = ready;
  updateComposerControls();
}

function updateComposerControls() {
  input.disabled = CONFIG.demoMode || !projectReady || isLoading;
  sendBtn.disabled = CONFIG.demoMode || !projectReady || isLoading;
  stopBtn.hidden = !isLoading || !currentAbortController;
  stopBtn.disabled = !isLoading || isStopping;
  planApproveBtn.disabled = !projectReady || isLoading || !pendingPlanDraft;
  planRequestChangesBtn.disabled = !projectReady || isLoading || !pendingPlanDraft;
  stopBtn.title = isStopping ? CONTENT.progress.stopping : CONTENT.chat.stopTitle;
}

function updateProjectUrl() {
  if (!projectId) return;
  const url = new URL(window.location.href);
  url.searchParams.set("project", projectId);
  window.history.replaceState(null, "", url);
}

function applyProject(data, { announce = false, created = false } = {}) {
  projectId = data.project_id;
  threadId = data.thread_id ?? threadId ?? newThreadId();
  updateProjectDisplay();
  updateProjectUrl();
  setProjectReady(true);
  onProjectChanged(projectId);
  dichromaticWidget?.projectChanged();
  void cellContinuations.drain();
  void refreshPlanApproval();
  if (announce) {
    appendMessage(
      "system",
      created ? CONTENT.project.created(projectId) : CONTENT.project.switched(projectId),
    );
  }
}

function clearProjectSession({ announce = false } = {}) {
  projectId = null;
  threadId = null;
  updateProjectDisplay();
  const url = new URL(window.location.href);
  url.searchParams.delete("project");
  window.history.replaceState(null, "", url);
  setProjectReady(true);
  onProjectChanged(null);
  dichromaticWidget?.projectChanged();
  renderPlanApproval(null);
  closeFilePreview();
  if (announce) {
    appendMessage("system", CONTENT.project.newSessionReady);
  }
}

function resetChatForProject() {
  chatLog.replaceChildren();
  if (welcome) welcome.style.display = "";
}

async function fetchProject(projectIdValue) {
  const response = await fetch(`${CONFIG.apiBase}${CONFIG.projectsEndpoint}/${encodeURIComponent(projectIdValue)}`);
  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }
  return response.json();
}

function renderPlanApproval(draft) {
  pendingPlanDraft = draft || null;
  if (!pendingPlanDraft) {
    planApprovalEl.hidden = true;
    planApprovalTitleEl.textContent = "";
    planApprovalMetaEl.textContent = "";
    updateComposerControls();
    return;
  }

  const proposed = pendingPlanDraft.proposed_plan || {};
  const revision = proposed.revision ?? 1;
  const stepCount = Array.isArray(proposed.steps) ? proposed.steps.length : 0;
  planApprovalTitleEl.textContent = CONTENT.planApproval.title(
    proposed.title || proposed.plan_id || pendingPlanDraft.draft_id,
  );
  planApprovalMetaEl.textContent = CONTENT.planApproval.meta(
    pendingPlanDraft.draft_id,
    revision,
    stepCount,
  );
  planApprovalEl.hidden = false;
  updateComposerControls();
}

async function refreshPlanApproval() {
  if (CONFIG.demoMode) {
    renderPlanApproval(null);
    return;
  }
  const requestedProjectId = projectId;
  if (!requestedProjectId) {
    renderPlanApproval(null);
    return;
  }
  try {
    const response = await fetch(
      `${CONFIG.apiBase}${CONFIG.projectsEndpoint}/${encodeURIComponent(requestedProjectId)}/memory`,
    );
    if (!response.ok) {
      const err = await response.text().catch(() => response.statusText);
      throw new Error(err || `HTTP ${response.status}`);
    }
    const data = await response.json();
    if (projectId !== requestedProjectId) return;
    renderPlanApproval(data.pending_plan?.draft || null);
  } catch (err) {
    if (projectId !== requestedProjectId) return;
    renderPlanApproval(null);
    console.warn(CONTENT.planApproval.loadFailed(err.message));
  }
}

async function loadProjectHistory(projectIdValue) {
  if (!projectIdValue) return;
  try {
    let offset = 0;
    const limit = 200;
    let hasMore = true;
    while (hasMore) {
      const response = await fetch(
        `${CONFIG.apiBase}${CONFIG.projectsEndpoint}/${encodeURIComponent(projectIdValue)}/history?offset=${offset}&limit=${limit}`,
      );
      if (!response.ok) throw new Error(`Could not load recorded history (${response.status}).`);
      const data = await response.json();
      for (const turn of data.turns ?? []) {
        if (!turn?.content) continue;
        if (turn.role === "assistant") {
          appendHistoricalAgentMessage(
            turn.content,
            {},
            null,
            projectIdValue,
            turn.turn_id,
            turn.image_paths || [],
          );
        } else {
          appendMessage("user", turn.content);
        }
        const message = chatLog.lastElementChild;
        if (message) {
          message.dataset.searchText = turn.content.toLocaleLowerCase();
          message.dataset.turnId = turn.turn_id;
          message.id = `turn-${turn.turn_id}`;
        }
      }
      offset += (data.turns ?? []).length;
      hasMore = Boolean(data.has_more) && (data.turns ?? []).length > 0;
    }
    if (CONFIG.demoMode) filterDemoHistory();
    else scrollChatToBottom();
  } catch (error) {
    if (CONFIG.demoMode) throw error;
    // No prior transcript (or offline) — start with an empty chat.
  }
}

async function createProjectOnServer(name = null) {
  const response = await fetch(`${CONFIG.apiBase}${CONFIG.projectsEndpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(name ? { name } : {}),
  });
  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }
  return response.json();
}

async function listProjectsOnServer() {
  const response = await fetch(`${CONFIG.apiBase}${CONFIG.projectsEndpoint}`);
  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }
  const data = await response.json();
  return data.projects ?? [];
}

async function ensureProject() {
  if (projectId) return projectId;

  const data = await createProjectOnServer();
  applyProject(data, { announce: true, created: true });
  return projectId;
}

async function initProject() {
  if (CONFIG.demoMode) {
    const data = await fetchProject(CONFIG.demoProjectId);
    resetChatForProject();
    applyProject(data);
    await loadProjectHistory(CONFIG.demoProjectId);
    return;
  }
  const urlProject = new URLSearchParams(window.location.search).get("project");

  if (urlProject) {
    try {
      const data = await fetchProject(urlProject);
      resetChatForProject();
      await loadProjectHistory(urlProject);
      applyProject(data);
      return;
    } catch (err) {
      appendMessage("system", CONTENT.project.initFailed(err.message));
    }
  }

  clearProjectSession();
}

function renderProjectList(projects) {
  projectListEl.replaceChildren();

  if (!projects.length) {
    const empty = document.createElement("li");
    empty.className = "project-list-item-meta";
    empty.style.padding = "0.75rem";
    empty.textContent = CONTENT.project.emptyList;
    projectListEl.appendChild(empty);
    return;
  }

  for (const item of projects) {
    const row = document.createElement("li");
    row.className = "project-list-item-wrap";

    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "project-list-item";
    if (item.project_id === projectId) btn.classList.add("is-active");

    const idEl = document.createElement("span");
    idEl.className = "project-list-item-id";
    idEl.textContent = item.project_id;
    idEl.title = item.project_id;

    const metaEl = document.createElement("span");
    metaEl.className = "project-list-item-meta";
    metaEl.textContent = item.name === item.project_id ? item.updated_at : `${item.name} · ${item.updated_at}`;

    btn.append(idEl, metaEl);
    btn.addEventListener("click", () => {
      closeProjectModal();
      switchToProject(item.project_id);
    });

    const delBtn = document.createElement("button");
    delBtn.type = "button";
    delBtn.className = "project-delete-btn";
    delBtn.textContent = CONTENT.project.delete;
    delBtn.title = CONTENT.project.delete;
    delBtn.addEventListener("click", (event) => {
      event.stopPropagation();
      deleteProject(item.project_id);
    });

    row.appendChild(btn);
    if (!CONFIG.demoMode) row.appendChild(delBtn);
    projectListEl.appendChild(row);
  }
}

async function openProjectModal() {
  projectModal.hidden = false;

  try {
    const projects = await listProjectsOnServer();
    renderProjectList(projects);
  } catch (err) {
    projectListEl.replaceChildren();
    const error = document.createElement("li");
    error.className = "project-list-item-meta";
    error.style.padding = "0.75rem";
    error.textContent = CONTENT.project.loadFailed(err.message);
    projectListEl.appendChild(error);
  }
}

function closeProjectModal() {
  projectModal.hidden = true;
}

async function switchToProject(nextProjectId) {
  if (!nextProjectId || nextProjectId === projectId) return;

  try {
    const data = await fetchProject(nextProjectId);
    resetChatForProject();
    await loadProjectHistory(nextProjectId);
    applyProject(data, { announce: true });
  } catch (err) {
    appendMessage("system", CONTENT.project.initFailed(err.message));
  }
}

async function deleteProject(targetProjectId) {
  if (!targetProjectId) return;
  if (!confirm(CONTENT.project.deleteConfirm(targetProjectId))) return;

  try {
    const response = await fetch(
      `${CONFIG.apiBase}${CONFIG.projectsEndpoint}/${encodeURIComponent(targetProjectId)}`,
      { method: "DELETE" },
    );
    if (!response.ok) {
      const err = await response.text().catch(() => response.statusText);
      throw new Error(err || `HTTP ${response.status}`);
    }

    const wasCurrent = targetProjectId === projectId;
    if (wasCurrent) {
      resetChatForProject();
      clearProjectSession();
      appendMessage("system", CONTENT.project.deleted(targetProjectId));
    }

    const projects = await listProjectsOnServer();
    renderProjectList(projects);
    refreshProjectFiles();
  } catch (err) {
    appendMessage("system", CONTENT.project.deleteFailed(err.message));
  }
}

async function createAndSwitchProject() {
  closeProjectModal();
  resetChatForProject();
  clearProjectSession({ announce: true });
}

async function checkHealth() {
  try {
    const res = await fetch(CONFIG.apiBase + CONFIG.healthEndpoint);
    if (res.ok) {
      setConnected();
      return;
    }
  } catch {
    // backend not running — fall through
  }
  setBackendNotReady();
}

function hideWelcome() {
  if (CONFIG.demoMode) return;
  if (welcome) welcome.style.display = "none";
}

function labelForRole(role) {
  return CONTENT.labels[role] ?? role;
}

function appendMessage(role, text) {
  hideWelcome();
  const el = document.createElement("div");
  el.className = `message ${role}`;

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = labelForRole(role);

  const bubble = document.createElement("div");
  bubble.className = "bubble";

  if (role === "agent") {
    bubble.classList.add("markdown");
    try {
      bubble.innerHTML = renderMarkdown(text);
    } catch (err) {
      console.error("Markdown render failed:", err);
      bubble.textContent = text;
    }
  } else {
    bubble.textContent = text;
  }

  el.append(label, bubble);
  chatLog.appendChild(el);
  if (!CONFIG.demoMode) scrollToBottom(getPageScroller());
  return el;
}

function appendAgentLive() {
  hideWelcome();
  const el = document.createElement("div");
  el.className = "message agent";
  el.id = "agent-live";

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = labelForRole("agent");

  const bubble = document.createElement("div");
  bubble.className = "bubble agent-live";

  const progressPanel = document.createElement("div");
  progressPanel.className = "progress-panel";

  const progressStatus = document.createElement("div");
  progressStatus.className = "progress-status";
  progressStatus.dataset.stage = "planning";

  const progressDot = document.createElement("span");
  progressDot.className = "progress-dot";
  progressDot.setAttribute("aria-hidden", "true");

  const progressCopy = document.createElement("div");
  progressCopy.className = "progress-status-copy";

  const progressStatusText = document.createElement("span");
  progressStatusText.className = "progress-status-text";
  progressStatusText.textContent = CONTENT.progress.planning;

  const progressStatusHint = document.createElement("span");
  progressStatusHint.className = "progress-status-hint";
  progressStatusHint.hidden = true;

  progressCopy.append(progressStatusText, progressStatusHint);
  progressStatus.append(progressDot, progressCopy);

  const liveTraceBody = document.createElement("div");
  liveTraceBody.className = "progress-live-body";

  progressPanel.append(progressStatus, liveTraceBody);

  const streamText = document.createElement("div");
  streamText.className = "stream-text";
  streamText.hidden = true;

  bubble.append(progressPanel, streamText);
  el.append(label, bubble);
  chatLog.appendChild(el);
  scrollToBottom(getPageScroller());

  return {
    root: el,
    bubble,
    progressPanel,
    progressStatusBar: progressStatus,
    progressStatus: progressStatusText,
    progressHint: progressStatusHint,
    liveTraceBody,
    thinkingStream: null,
    activeThinkingStream: null,
    progressSteps: null,
    streamText,
    streaming: false,
    hasThinking: false,
    thinkingParts: [],
    traceSegments: [],
    openReasoning: new Map(),
    pendingThinkingParents: new Set(),
    thinkingRenderScheduled: false,
    agentResultNodes: new Map(),
    pendingAgentResultParents: new Set(),
    agentResultRenderScheduled: false,
    replyParts: [],
    renderScheduled: false,
    runningTools: new Map(),
    toolStepSeq: 0,
    activeToolStep: null,
    renderImages: [],
  };
}

function extractRenderPaths(value, projectIdOverride = null) {
  return extractArtifactImagePaths(value, projectIdOverride ?? projectId);
}

function collectRenderImages(live, value) {
  for (const path of extractRenderPaths(value)) {
    if (!live.renderImages.includes(path)) live.renderImages.push(path);
  }
}

function imageOptions(projectIdOverride = null) {
  return {
    projectId: projectIdOverride ?? projectId,
    apiBase: CONFIG.apiBase,
    projectsEndpoint: CONFIG.projectsEndpoint,
    onOpenImage: openImageModal,
  };
}

function fixArtifactImagesIn(container, projectIdOverride = null) {
  enhanceArtifactImages(container, { ...imageOptions(projectIdOverride), append: false });
}

function embedRenderImages(live) {
  embedRenderImagesInto(live.streamText, live.renderImages || []);
}

function embedRenderImagesInto(container, paths, projectIdOverride = null) {
  enhanceArtifactImages(container, { ...imageOptions(projectIdOverride), paths });
}

function buildStaticToolStep(step) {
  const item = document.createElement("li");
  item.className = "progress-step tool-call is-done";
  if (step.depth) item.classList.add("tool-call-nested");
  if (isDelegateTool(step.name)) item.classList.add("tool-call-delegate");
  item.dataset.toolId = step.id || step.name || "tool";
  item.dataset.toolName = step.name || "";

  const header = document.createElement("div");
  header.className = "tool-call-header";

  const labelEl = document.createElement("span");
  labelEl.className = "tool-call-label";
  labelEl.textContent = formatToolLabel(step.label, step.name, segmentParentName(step));

  const statusEl = document.createElement("span");
  statusEl.className = "tool-call-status is-done";
  statusEl.textContent = CONTENT.progress.toolDoneBadge;

  header.append(labelEl, statusEl);
  item.appendChild(header);

  appendToolInput(item, step.name, step.input);
  if (!isDelegateTool(step.name)) {
    appendToolOutput(item, step.name, step.output);
  }
  return item;
}

function normalizeTrace(record) {
  const trace = Array.isArray(record?.trace) ? record.trace : [];
  if (
    trace.some(
      (segment) =>
        segment &&
        (segment.kind === "reasoning" ||
          segment.kind === "tool" ||
          segment.kind === "agent_result"),
    )
  ) {
    return attributeOrphanReasoning(trace);
  }
  const out = [];
  const reasoning = (record?.reasoning || "").trim();
  if (reasoning) out.push({ kind: "reasoning", text: reasoning });
  for (const step of trace) {
    if (step && typeof step === "object") out.push({ kind: "tool", ...step });
  }
  return attributeOrphanReasoning(out);
}

function traceEventsToSegments(events) {
  const segments = [];
  const eventById = new Map();
  const openTools = [];
  const openReasoning = new Map();
  for (const event of Array.isArray(events) ? events : []) {
    if (!event || typeof event !== "object") continue;
    const parentEvent = event.parent_event_id ? eventById.get(event.parent_event_id) : null;
    const parentId = event.parent_event_id || "";
    const parentName = parentEvent?.tool_name || "";
    if (event.event_type === "reasoning") {
      const existing = openReasoning.get(parentId);
      if (existing) {
        existing.text += event.output_text || "";
      } else {
        const segment = {
          kind: "reasoning",
          text: event.output_text || "",
          parent_id: parentId,
          parent_name: parentName,
          depth: parentId ? 1 : 0,
        };
        openReasoning.set(parentId, segment);
        segments.push(segment);
      }
    } else if (event.event_type === "tool_start") {
      openReasoning.delete(parentId);
      const segment = {
        kind: "tool",
        id: event.event_id,
        tool_call_id: event.tool_call_id || "",
        name: event.tool_name || "tool",
        label: event.label || "",
        input: event.input_json || "",
        output: "",
        parent_id: parentId,
        parent_name: parentName,
        depth: parentId ? 1 : 0,
        open: true,
      };
      segments.push(segment);
      openTools.push(segment);
      eventById.set(event.event_id, event);
    } else if (event.event_type === "tool_end") {
      openReasoning.delete(parentId);
      let completedTool = null;
      for (let index = openTools.length - 1; index >= 0; index -= 1) {
        const segment = openTools[index];
        const callMatches = event.tool_call_id
          ? segment.tool_call_id === event.tool_call_id
          : segment.name === (event.tool_name || "tool");
        if (
          segment.open &&
          callMatches &&
          segmentParentId(segment) === parentId
        ) {
          segment.output = event.output_text || "";
          segment.image_paths = event.image_paths;
          segment.open = false;
          completedTool = segment;
          break;
        }
      }
      if (!parentId && completedTool) {
        openReasoning.delete(completedTool.id || "");
      }
      if (
        completedTool &&
        isDelegateTool(completedTool.name) &&
        (event.output_text || "").trim()
      ) {
        segments.push({
          kind: "agent_result",
          text: event.output_text || "",
          parent_id: completedTool.id || "",
          parent_name: completedTool.name || "",
          depth: 1,
          streaming: false,
        });
      }
    }
    eventById.set(event.event_id, event);
  }
  return attributeOrphanReasoning(segments);
}

function buildLazyHistoryTrace(projectIdValue, turnId, onImages = () => {}) {
  const panel = document.createElement("div");
  panel.className = "progress-collapsed";
  const details = document.createElement("details");
  details.className = "progress-details";
  const summary = document.createElement("summary");
  summary.className = "progress-details-summary";
  summary.textContent = CONTENT.progress.viewTrace("");
  const body = document.createElement("div");
  body.className = "progress-details-body";
  body.textContent = CONTENT.project.loading;
  details.append(summary, body);
  panel.appendChild(details);

  let loaded = false;
  details.addEventListener("toggle", async () => {
    if (!details.open || loaded) return;
    loaded = true;
    try {
      const response = await fetch(
        `${CONFIG.apiBase}${CONFIG.projectsEndpoint}/${encodeURIComponent(projectIdValue)}/history/${encodeURIComponent(turnId)}`,
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const detail = await response.json();
      onImages(detail.image_paths || []);
      const segments = traceEventsToSegments(detail.trace);
      if (!segments.length && detail.reasoning) {
        segments.push({ kind: "reasoning", text: detail.reasoning });
      }
      const replacement = buildTracePanel(segments);
      if (!replacement) {
        body.textContent = "No execution trace was recorded.";
        return;
      }
      panel.replaceWith(replacement);
      const replacementDetails = replacement.querySelector("details");
      if (replacementDetails) replacementDetails.open = true;
    } catch (error) {
      loaded = false;
      body.textContent = `Could not load execution trace: ${error.message || error}`;
    }
  });
  return panel;
}

function serializeTraceSegments(segments) {
  return (Array.isArray(segments) ? segments : [])
    .filter((segment) => segment && typeof segment === "object")
    .map(({ open: _open, provisional: _provisional, ...segment }) => ({ ...segment }));
}

function buildTracePanel(segments) {
  const segs = Array.isArray(segments) ? segments : [];
  if (!segs.length) return null;

  const panel = document.createElement("div");
  panel.className = "progress-collapsed";

  const details = document.createElement("details");
  details.className = "progress-details";

  const summary = document.createElement("summary");
  summary.className = "progress-details-summary";

  const body = document.createElement("div");
  body.className = "progress-details-body";

  let toolCount = 0;
  let delegateCount = 0;
  let hasReasoning = false;
  let pendingTools = null;
  let lastTop = null;
  const explicitResultParents = new Set();
  const legacyDelegateResults = [];

  const ensureToolsSection = () => {
    if (pendingTools) return pendingTools;
    const section = document.createElement("div");
    section.className = "progress-section";
    const label = document.createElement("div");
    label.className = "progress-section-label";
    label.textContent = CONTENT.progress.toolStepsSection;
    const list = document.createElement("ul");
    list.className = "progress-steps";
    section.append(label, list);
    body.appendChild(section);
    pendingTools = list;
    return list;
  };

  for (const segment of segs) {
    if (!segment || typeof segment !== "object") continue;
    if (segment.kind === "reasoning") {
      const text = (segment.text || "").trim();
      if (!text) continue;
      hasReasoning = true;
      const parentId = segmentParentId(segment);
      const parentName = segmentParentName(segment);
      if (parentId) {
        const parentItem =
          (lastTop && lastTop.dataset.toolId === parentId ? lastTop : null) ||
          (pendingTools && findParentToolItem(pendingTools, parentId, parentName));
        if (parentItem) {
          const { root } = buildReasoningBlock(text, parentName);
          ensureToolSubsteps(parentItem).appendChild(root);
          lastTop = parentItem;
          continue;
        }
      }
      pendingTools = null;
      lastTop = null;
      const { root } = buildReasoningBlock(text, "");
      body.appendChild(root);
    } else if (segment.kind === "agent_result") {
      const text = (segment.text || "").trim();
      if (!text) continue;
      const parentId = segmentParentId(segment);
      const parentItem =
        (lastTop && lastTop.dataset.toolId === parentId ? lastTop : null) ||
        (pendingTools &&
          findParentToolItem(pendingTools, parentId, segmentParentName(segment)));
      if (!parentItem) continue;
      const built = buildAgentResultBlock({ ...segment, streaming: false });
      ensureToolSubsteps(parentItem).appendChild(built.item);
      explicitResultParents.add(parentId);
      lastTop = parentItem;
    } else if (segment.kind === "tool") {
      if (isDelegateTool(segment.name)) delegateCount += 1;
      const list = ensureToolsSection();
      const item = buildStaticToolStep(segment);
      const parentId = segmentParentId(segment);
      const parentItem = parentId
        ? findParentToolItem(list, parentId, segmentParentName(segment))
        : lastTop;
      if (segment.depth && parentItem) {
        let subList = parentItem.querySelector(":scope > .tool-call-substeps");
        if (!subList) {
          subList = document.createElement("ul");
          subList.className = "tool-call-substeps";
          parentItem.appendChild(subList);
        }
        subList.appendChild(item);
        lastTop = parentItem;
      } else {
        list.appendChild(item);
        lastTop = item;
        toolCount += 1;
      }
      if (isDelegateTool(segment.name) && (segment.output || "").trim()) {
        legacyDelegateResults.push({
          parentItem: item,
          parentId: segment.id || segment.name || "",
          parentName: segment.name || "",
          text: formatToolOutput(segment.name, segment.output),
        });
      }
    }
  }

  for (const result of legacyDelegateResults) {
    if (explicitResultParents.has(result.parentId) || !result.text) continue;
    const built = buildAgentResultBlock({
      kind: "agent_result",
      text: result.text,
      parent_id: result.parentId,
      parent_name: result.parentName,
      streaming: false,
    });
    ensureToolSubsteps(result.parentItem).appendChild(built.item);
  }

  if (!hasReasoning && toolCount === 0) return null;

  const summaryParts = [];
  if (toolCount > 0) summaryParts.push(`${toolCount} tool step${toolCount === 1 ? "" : "s"}`);
  if (delegateCount > 0) {
    summaryParts.push(`${delegateCount} agent result${delegateCount === 1 ? "" : "s"}`);
  }
  if (hasReasoning) summaryParts.push("reasoning");
  summary.textContent = CONTENT.progress.viewTrace(summaryParts.join(" · "));

  details.append(summary, body);
  panel.appendChild(details);
  return panel;
}

function appendHistoricalAgentMessage(
  reply,
  recordOrReasoning,
  steps,
  projectIdOverride = null,
  turnId = null,
  imagePaths = [],
) {
  hideWelcome();
  const el = document.createElement("div");
  el.className = "message agent";

  const label = document.createElement("span");
  label.className = "label";
  label.textContent = labelForRole("agent");

  const bubble = document.createElement("div");
  bubble.className = "bubble markdown";

  const segments = normalizeTrace(
    typeof recordOrReasoning === "object" && recordOrReasoning !== null
      ? recordOrReasoning
      : { reasoning: recordOrReasoning || "", trace: steps || [] },
  );
  const tracePanel = buildTracePanel(segments);
  if (tracePanel) {
    bubble.appendChild(tracePanel);
  } else if (projectIdOverride && turnId) {
    bubble.appendChild(buildLazyHistoryTrace(projectIdOverride, turnId, (paths) => {
      embedRenderImagesInto(answer, paths, projectIdOverride);
    }));
  }

  const answer = document.createElement("div");
  answer.className = "stream-text markdown";
  try {
    answer.innerHTML = renderMarkdown(reply);
  } catch (err) {
    console.error("Markdown render failed:", err);
    answer.textContent = reply;
  }
  bubble.appendChild(answer);

  const paths = new Set(extractRenderPaths([reply, imagePaths], projectIdOverride));
  for (const segment of segments) {
    if (segment?.kind !== "tool") continue;
    for (const p of extractToolEventImagePaths(segment, projectIdOverride)) paths.add(p);
  }
  fixArtifactImagesIn(answer, projectIdOverride);
  embedRenderImagesInto(answer, [...paths], projectIdOverride);

  el.append(label, bubble);
  chatLog.appendChild(el);
}

function removeAgentLive() {
  document.getElementById("agent-live")?.remove();
}

function scrollChatToBottom() {
  stickToBottom(getPageScroller());
}

const PROGRESS_STAGES = {
  planning: "planning",
  reasoning: "reasoning",
  toolWaiting: "tool-waiting",
  composing: "composing",
  streaming: "streaming",
};

function setProgressStage(
  live,
  stage,
  { label = "", hint = "", statusMessage = "" } = {},
) {
  if (live.progressStatusBar) {
    live.progressStatusBar.dataset.stage = stage;
  }

  let message = "";
  switch (stage) {
    case PROGRESS_STAGES.reasoning:
      message = CONTENT.progress.reasoning;
      break;
    case PROGRESS_STAGES.toolWaiting:
      message = CONTENT.progress.waitingForTool(label || "tool");
      break;
    case PROGRESS_STAGES.composing:
      message = CONTENT.progress.composing;
      break;
    case PROGRESS_STAGES.streaming:
      message = CONTENT.progress.streamingAnswer;
      break;
    case PROGRESS_STAGES.planning:
    default:
      message = CONTENT.progress.planning;
      break;
  }

  if (live.progressStatus) {
    live.progressStatus.textContent = statusMessage || message;
  }
  if (live.progressHint) {
    if (hint) {
      live.progressHint.textContent = hint;
      live.progressHint.hidden = false;
    } else {
      live.progressHint.textContent = "";
      live.progressHint.hidden = true;
    }
  }
  scrollChatToBottom();
}

function toolCallDepth(item) {
  let depth = 0;
  let parent = item.parentElement;
  while (parent) {
    if (parent.classList?.contains("tool-call-substeps")) depth += 1;
    parent = parent.parentElement;
  }
  return depth;
}

function getRunningToolLabel(live) {
  let deepest = null;
  let maxDepth = -1;
  for (const item of live.runningTools.values()) {
    if (!item.classList.contains("is-running")) continue;
    const depth = toolCallDepth(item);
    if (depth >= maxDepth) {
      maxDepth = depth;
      deepest = item;
    }
  }
  if (!deepest && live.activeToolStep?.classList.contains("is-running")) {
    deepest = live.activeToolStep;
  }
  if (!deepest) return "";
  return deepest.querySelector(".tool-call-label")?.textContent?.trim() || "";
}

function syncToolWaitingStatus(live) {
  const label = getRunningToolLabel(live);
  if (!label) return false;
  setProgressStage(live, PROGRESS_STAGES.toolWaiting, {
    label,
    hint: CONTENT.progress.toolWaitingHint,
  });
  return true;
}

function createToolDots() {
  const dots = document.createElement("span");
  dots.className = "tool-dots";
  dots.setAttribute("aria-hidden", "true");
  dots.setAttribute("aria-label", CONTENT.progress.toolRunning);
  for (let i = 0; i < 3; i += 1) {
    dots.appendChild(document.createElement("span"));
  }
  return dots;
}

function isDelegateTool(name) {
  return String(name || "").startsWith("delegate_");
}

const PRESERVE_FINAL_STREAM_TOOLS = new Set([
  "update_project_plan_step",
  "get_project_plan",
  "get_project_plan_draft",
  "query_project_memory",
  "search_project_memory",
  "list_run_artifacts",
  "read_run_artifact",
  "report_artifact_usage",
  "list_available_potentials",
]);

function preservesFinalStream(name) {
  return PRESERVE_FINAL_STREAM_TOOLS.has(String(name || ""));
}

function delegateSummaryFromPayload(payload) {
  if (!payload || typeof payload !== "object") return "";
  return String(
    payload.summary || payload.result?.summary || payload.error || "",
  ).trim();
}

function formatToolOutput(name, output) {
  const text = (output || "").trim();
  if (!text) return "";
  if (isDelegateTool(name)) {
    try {
      const parsed = JSON.parse(text);
      const summary = delegateSummaryFromPayload(parsed);
      if (summary) return summary;
    } catch {
      // fall through to raw output
    }
  }
  return text;
}

function appendToolInput(item, name, input) {
  const text = (input || "").trim();
  if (!text) return;
  if (isDelegateTool(name)) {
    const label = document.createElement("div");
    label.className = "tool-call-payload-label";
    label.textContent = CONTENT.progress.delegatedTask;
    item.appendChild(label);
  }
  const inputEl = document.createElement("div");
  inputEl.className = "tool-call-input";
  inputEl.textContent = text;
  item.appendChild(inputEl);
}

function appendToolOutput(item, name, output) {
  const text = formatToolOutput(name, output);
  if (!text) return;
  const outputEl = document.createElement("div");
  outputEl.className = "tool-call-output";
  outputEl.textContent = text;
  item.appendChild(outputEl);
}

function buildAgentResultBlock(segment) {
  const item = document.createElement("li");
  item.className = "progress-step agent-result-step";
  if (segment.streaming) item.classList.add("is-streaming");

  const header = document.createElement("div");
  header.className = "tool-call-header";

  const label = document.createElement("span");
  label.className = "tool-call-label";
  const agent = parentAgentLabel(segmentParentName(segment));
  label.textContent = agent
    ? `${agent} › ${CONTENT.progress.agentResult}`
    : CONTENT.progress.agentResult;

  const status = document.createElement("span");
  status.className = `tool-call-status${segment.streaming ? "" : " is-done"}`;
  if (segment.streaming) {
    status.appendChild(createToolDots());
  } else {
    status.textContent = CONTENT.progress.toolDoneBadge;
  }

  const output = document.createElement("div");
  output.className = "agent-result-output";
  output.textContent = segment.text || "";

  header.append(label, status);
  item.append(header, output);
  return { item, output, status };
}

// Acronyms that should stay uppercase when appearing as a token in a tool name.
const HUMANIZE_KEEP_UPPER = new Set(["csl", "gb", "lammps", "fcc", "bcc", "hcp", "adp", "eam", "nep", "dft", "dxa"]);

/** Top-level delegate tools → agent display names (matches backend _tool_label). */
const DELEGATE_AGENT_LABELS = {
  delegate_plan: "Planning agent",
  delegate_csl: "Structure agent",
  delegate_lammps: "LAMMPS agent",
  delegate_analysis: "Analysis agent",
  delegate_coding: "Coding agent",
};

function humanizeToolName(name) {
  if (!name) return "";
  const spaced = String(name).replace(/[_\-]+/g, " ").trim();
  if (!spaced) return name;
  return spaced
    .split(/\s+/)
    .map((word) => {
      const lower = word.toLowerCase();
      if (HUMANIZE_KEEP_UPPER.has(lower)) return lower.toUpperCase();
      return word ? word[0].toUpperCase() + word.slice(1) : word;
    })
    .join(" ");
}

function toolLabelOnly(label, name) {
  const friendly = (label || "").trim();
  const toolName = (name || "").trim();
  // Prefer the backend-provided human label (e.g. "Structure agent"). Only fall back
  // to a humanized version of the raw tool name when no friendly label exists.
  if (friendly && friendly !== toolName) return friendly;
  if (toolName) return humanizeToolName(toolName);
  return "Tool";
}

function parentAgentLabel(parent) {
  const key = (parent || "").trim();
  if (!key) return "";
  return DELEGATE_AGENT_LABELS[key] || humanizeToolName(key);
}

function eventParentId(event) {
  return (event?.parent_id || event?.parent || "").trim();
}

function eventParentName(event) {
  return (event?.parent_name || event?.parent || "").trim();
}

function segmentParentId(segment) {
  return (segment?.parent_id || segment?.parent || "").trim();
}

function segmentParentName(segment) {
  return (segment?.parent_name || segment?.parent || "").trim();
}

/**
 * Display label for a tool call. Nested worker tools (parent = delegate_*)
 * are shown as "LAMMPS agent › GB box estimate" so the calling agent is clear.
 */
function formatToolLabel(label, name, parent = "") {
  const toolPart = toolLabelOnly(label, name);
  const agent = parentAgentLabel(parent);
  if (agent) return `${agent} › ${toolPart}`;
  return toolPart;
}

function formatReasoningLabel(parent = "") {
  const agent = parentAgentLabel(parent);
  const base = CONTENT.progress.reasoningSection;
  if (agent) return `${agent} › ${base}`;
  return `${CONTENT.labels.agent} › ${base}`;
}

/**
 * Older traces stored worker thinking without `parent`. Attribute orphan
 * reasoning that sits under an open delegate_* tool to that agent so the
 * historical UI matches the live nested layout.
 */
function attributeOrphanReasoning(segments) {
  const segs = (Array.isArray(segments) ? segments : [])
    .filter((s) => s && typeof s === "object")
    .map((s) => ({ ...s }));
  let openDelegateId = "";
  let openDelegateName = "";
  for (const seg of segs) {
    if (!seg.parent_id && seg.parent) seg.parent_id = seg.parent;
    if (!seg.parent_name && seg.parent) seg.parent_name = seg.parent;
    if (seg.kind === "agent_result") {
      // The worker result is the delegate's terminal boundary. Reasoning that
      // follows without a parent belongs to the orchestrator, not this worker.
      if (
        !openDelegateId ||
        segmentParentId(seg) === openDelegateId ||
        segmentParentName(seg) === openDelegateName
      ) {
        openDelegateId = "";
        openDelegateName = "";
      }
      continue;
    }
    if (seg.kind === "tool") {
      const name = (seg.name || "").trim();
      if (!segmentParentId(seg) && name.startsWith("delegate_")) {
        openDelegateId = seg.id || name;
        openDelegateName = name;
      } else if (!segmentParentId(seg)) {
        openDelegateId = "";
        openDelegateName = "";
      }
      continue;
    }
    if (seg.kind !== "reasoning") continue;
    if (segmentParentId(seg)) {
      openDelegateId = segmentParentId(seg);
      openDelegateName = segmentParentName(seg);
      continue;
    }
    if (openDelegateId) {
      seg.parent_id = openDelegateId;
      seg.parent_name = openDelegateName;
      seg.depth = 1;
    }
  }
  return segs;
}

function buildReasoningBlock(text, parent = "", { open = false } = {}) {
  const stream = document.createElement("div");
  stream.className = "thinking-stream";
  stream.textContent = text || "";

  if (parent) {
    const item = document.createElement("li");
    item.className = "progress-step tool-reasoning";
    if (open) item.classList.add("is-open");
    const label = document.createElement("div");
    label.className = "progress-section-label";
    label.textContent = formatReasoningLabel(parent);
    item.append(label, stream);
    return { root: item, stream };
  }

  const section = document.createElement("div");
  section.className = "progress-section";
  const label = document.createElement("div");
  label.className = "progress-section-label";
  label.textContent = formatReasoningLabel(parent);
  section.append(label, stream);
  return { root: section, stream };
}

function findParentToolItem(listRoot, parentId, parentName = "") {
  if (!listRoot || (!parentId && !parentName)) return null;
  for (const item of listRoot.querySelectorAll(":scope > .tool-call")) {
    if (parentId && item.dataset.toolId === parentId) return item;
  }
  for (const item of listRoot.querySelectorAll(":scope > .tool-call")) {
    if (parentName && item.dataset.toolName === parentName) return item;
  }
  return null;
}

function ensureToolSubsteps(parentItem) {
  let subList = parentItem.querySelector(":scope > .tool-call-substeps");
  if (!subList) {
    subList = document.createElement("ul");
    subList.className = "tool-call-substeps";
    parentItem.appendChild(subList);
  }
  return subList;
}

function buildLiveToolStep(step) {
  const item = document.createElement("li");
  const done = Boolean((step.output || "").trim());
  item.className = `progress-step tool-call ${done ? "is-done" : "is-running"}`;
  if (step.depth) item.classList.add("tool-call-nested");
  if (isDelegateTool(step.name)) item.classList.add("tool-call-delegate");
  item.dataset.toolId = step.id || step.name || "tool";
  item.dataset.toolName = step.name || "";

  const header = document.createElement("div");
  header.className = "tool-call-header";

  const labelEl = document.createElement("span");
  labelEl.className = "tool-call-label";
  labelEl.textContent = formatToolLabel(step.label, step.name, segmentParentName(step));

  const statusEl = document.createElement("span");
  statusEl.className = `tool-call-status${done ? " is-done" : ""}`;
  if (done) {
    statusEl.textContent = CONTENT.progress.toolDoneBadge;
  } else {
    statusEl.appendChild(createToolDots());
  }

  header.append(labelEl, statusEl);
  item.appendChild(header);

  appendToolInput(item, step.name, step.input);
  if (!isDelegateTool(step.name)) {
    appendToolOutput(item, step.name, step.output);
  }
  return item;
}

/** Rebuild live DOM from traceSegments so order always matches the data model. */
function renderLiveTraceFromSegments(live) {
  const body = live?.liveTraceBody;
  if (!body) return;

  const chatScroll = captureScrollPosition(getPageScroller());
  const thinkingScrollSnapshots = [...body.querySelectorAll(".thinking-stream")].map((stream) =>
    captureScrollPosition(stream),
  );
  let thinkingStreamIndex = 0;
  const restoreThinkingStream = (stream) => {
    const snapshot = thinkingScrollSnapshots[thinkingStreamIndex++];
    installStickyScroll(stream);
    if (snapshot) {
      restoreScrollPosition(stream, snapshot);
    } else {
      scrollToBottom(stream);
    }
  };

  body.replaceChildren();
  live.runningTools = new Map();
  live.activeToolStep = null;
  live.activeThinkingStream = null;
  live.thinkingStream = null;
  live.openReasoning = new Map();
  live.agentResultNodes = new Map();
  live.progressSteps = null;
  live.hasThinking = false;

  let pendingTools = null;
  let lastTop = null;
  const explicitResultParents = new Set();
  const legacyDelegateResults = [];

  const ensureToolsList = () => {
    if (pendingTools) return pendingTools;
    const section = document.createElement("div");
    section.className = "progress-section";
    const label = document.createElement("div");
    label.className = "progress-section-label";
    label.textContent = CONTENT.progress.toolStepsSection;
    const list = document.createElement("ul");
    list.className = "progress-steps";
    section.append(label, list);
    body.appendChild(section);
    pendingTools = list;
    live.progressSteps = list;
    return list;
  };

  for (const segment of live.traceSegments) {
    if (!segment || typeof segment !== "object") continue;

    if (segment.kind === "agent_result") {
      const parentId = segmentParentId(segment);
      const parentItem =
        (lastTop && lastTop.dataset.toolId === parentId ? lastTop : null) ||
        (pendingTools &&
          findParentToolItem(pendingTools, parentId, segmentParentName(segment)));
      if (!parentItem) continue;
      const built = buildAgentResultBlock(segment);
      ensureToolSubsteps(parentItem).appendChild(built.item);
      explicitResultParents.add(parentId);
      if (segment.streaming) {
        live.agentResultNodes.set(parentId, { segment, ...built });
      }
      installStickyScroll(built.output);
      scrollToBottom(built.output);
      lastTop = parentItem;
      continue;
    }

    if (segment.kind === "reasoning") {
      const parentId = segmentParentId(segment);
      const parentName = segmentParentName(segment);
      let stream = null;
      if (parentId) {
        const parentItem =
          (lastTop && lastTop.dataset.toolId === parentId ? lastTop : null) ||
          (pendingTools && findParentToolItem(pendingTools, parentId, parentName));
        if (parentItem) {
          const built = buildReasoningBlock(segment.text || "", parentName, {
            open: Boolean(segment.open),
          });
          ensureToolSubsteps(parentItem).appendChild(built.root);
          stream = built.stream;
          lastTop = parentItem;
          if (segment.open) {
            live.openReasoning.set(parentId, { segment, stream });
            live.activeThinkingStream = stream;
            live.thinkingStream = stream;
            live.hasThinking = true;
          }
          restoreThinkingStream(stream);
          continue;
        }
      }
      pendingTools = null;
      lastTop = null;
      const built = buildReasoningBlock(segment.text || "", "");
      body.appendChild(built.root);
      stream = built.stream;
      if (segment.open) {
        live.openReasoning.set(parentId, { segment, stream });
        live.activeThinkingStream = stream;
        live.thinkingStream = stream;
        live.hasThinking = true;
      }
      restoreThinkingStream(stream);
      continue;
    }

    if (segment.kind !== "tool") continue;

    const list = ensureToolsList();
    const item = buildLiveToolStep(segment);
    const parentId = segmentParentId(segment);
    const parentItem = parentId
      ? findParentToolItem(list, parentId, segmentParentName(segment))
      : lastTop;
    if (segment.depth && parentItem) {
      ensureToolSubsteps(parentItem).appendChild(item);
      lastTop = parentItem;
    } else {
      list.appendChild(item);
      lastTop = item;
      if (!parentId) {
        live.activeToolStep = item;
      }
    }
    const key = segment.id || segment.name || "tool";
    live.runningTools.set(key, item);
    if (isDelegateTool(segment.name) && (segment.output || "").trim()) {
      legacyDelegateResults.push({
        parentItem: item,
        parentId: key,
        parentName: segment.name || "",
        text: formatToolOutput(segment.name, segment.output),
      });
    }
  }

  for (const result of legacyDelegateResults) {
    if (explicitResultParents.has(result.parentId) || !result.text) continue;
    const built = buildAgentResultBlock({
      kind: "agent_result",
      text: result.text,
      parent_id: result.parentId,
      parent_name: result.parentName,
      streaming: false,
    });
    ensureToolSubsteps(result.parentItem).appendChild(built.item);
    installStickyScroll(built.output);
    scrollToBottom(built.output);
  }

  restoreScrollPosition(getPageScroller(), chatScroll);
}

function traceKeyForEvent(event) {
  return event.id || event.name || "tool";
}

function scheduleLiveThinkingRender(live, parentKey) {
  live.pendingThinkingParents.add(parentKey);
  if (live.thinkingRenderScheduled) return;
  live.thinkingRenderScheduled = true;
  requestAnimationFrame(() => {
    live.thinkingRenderScheduled = false;
    const parents = [...live.pendingThinkingParents];
    live.pendingThinkingParents.clear();
    for (const parent of parents) {
      const entry = live.openReasoning.get(parent);
      if (!entry?.stream?.isConnected) continue;
      entry.stream.textContent = entry.segment.text || "";
      stickToBottom(entry.stream);
    }
    scrollChatToBottom();
  });
}

function scheduleLiveAgentResultRender(live, parentKey) {
  live.pendingAgentResultParents.add(parentKey);
  if (live.agentResultRenderScheduled) return;
  live.agentResultRenderScheduled = true;
  requestAnimationFrame(() => {
    live.agentResultRenderScheduled = false;
    const parents = [...live.pendingAgentResultParents];
    live.pendingAgentResultParents.clear();
    for (const parent of parents) {
      const entry = live.agentResultNodes.get(parent);
      if (!entry?.output?.isConnected) continue;
      entry.output.textContent = entry.segment.text || "";
      stickToBottom(entry.output);
    }
    scrollChatToBottom();
  });
}

function appendLiveAgentResult(live, text, parentId = "", parentName = "") {
  if (!live || !text) return;
  const parentKey = parentId || "";
  let segment = live.traceSegments.find(
    (item) => item?.kind === "agent_result" && segmentParentId(item) === parentKey,
  );
  if (segment) {
    segment.text += text;
    segment.streaming = true;
    scheduleLiveAgentResultRender(live, parentKey);
    return;
  }
  sealLiveReasoning(live, parentKey);
  segment = {
    kind: "agent_result",
    text,
    parent_id: parentKey,
    parent_name: parentName || "",
    depth: parentKey ? 1 : 0,
    streaming: true,
  };
  live.traceSegments.push(segment);
  renderLiveTraceFromSegments(live);
}

function resetLiveAgentResult(live, parentId = "") {
  if (!live) return;
  const parentKey = parentId || "";
  const before = live.traceSegments.length;
  live.traceSegments = live.traceSegments.filter(
    (segment) =>
      segment?.kind !== "agent_result" || segmentParentId(segment) !== parentKey,
  );
  live.pendingAgentResultParents.delete(parentKey);
  live.agentResultNodes.delete(parentKey);
  if (live.traceSegments.length !== before) {
    renderLiveTraceFromSegments(live);
  }
}

function finalizeLiveAgentResult(live, text, parentId = "", parentName = "") {
  if (!live) return;
  const parentKey = parentId || "";
  let segment = live.traceSegments.find(
    (item) => item?.kind === "agent_result" && segmentParentId(item) === parentKey,
  );
  if (!segment && !text) return;
  if (!segment) {
    segment = {
      kind: "agent_result",
      text,
      parent_id: parentKey,
      parent_name: parentName || "",
      depth: parentKey ? 1 : 0,
      streaming: false,
    };
    live.traceSegments.push(segment);
  } else {
    if (text) segment.text = text;
    segment.streaming = false;
  }
  live.pendingAgentResultParents.delete(parentKey);
  live.agentResultNodes.delete(parentKey);
  renderLiveTraceFromSegments(live);
}

function appendLiveThinking(live, text, parentId = "", parentName = "") {
  if (!live || !text) return;
  const segments = live.traceSegments;
  const parentKey = parentId || "";
  const wasThinking = live.hasThinking;
  const existing = live.openReasoning.get(parentKey);
  if (existing) {
    existing.segment.text += text;
    live.activeThinkingStream = existing.stream;
    live.thinkingStream = existing.stream;
    scheduleLiveThinkingRender(live, parentKey);
  } else {
    segments.push({
      kind: "reasoning",
      text,
      open: true,
      parent_id: parentKey,
      parent_name: parentName || "",
      depth: parentKey ? 1 : 0,
    });
    renderLiveTraceFromSegments(live);
  }
  live.thinkingParts.push(text);
  if (!live.streaming && !wasThinking) {
    setProgressStage(live, PROGRESS_STAGES.reasoning, {
      label: parentName ? formatReasoningLabel(parentName) : "",
      hint: CONTENT.progress.reasoningHint,
    });
  }
}

function sealLiveReasoning(live, parent = null) {
  if (!live) return;
  if (parent === null) {
    for (const entry of live.openReasoning.values()) {
      entry.segment.open = false;
    }
    live.openReasoning.clear();
  } else {
    const parentKey = parent || "";
    const entry = live.openReasoning.get(parentKey);
    if (entry) entry.segment.open = false;
    live.openReasoning.delete(parentKey);
  }
  const remaining = [...live.openReasoning.values()];
  const active = remaining[remaining.length - 1] || null;
  live.activeThinkingStream = active?.stream || null;
  live.thinkingStream = active?.stream || null;
  live.hasThinking = remaining.length > 0;
}

function appendLiveToolStart(live, event) {
  if (!live || !event) return;
  const parentId = eventParentId(event);
  const parentName = eventParentName(event);
  sealLiveReasoning(live, parentId);
  const key = traceKeyForEvent(event);
  const name = event.name || "tool";
  const provisional = live.traceSegments.find(
    (segment) =>
      segment?.kind === "tool" &&
      segment.provisional &&
      (segment.id === key || segment.name === name),
  );
  const values = {
    kind: "tool",
    id: key,
    label: event.label || "",
    name,
    input: event.input || "",
    output: "",
    depth: parentId ? 1 : 0,
    parent_id: parentId,
    parent_name: parentName,
    provisional: false,
  };
  if (provisional) {
    Object.assign(provisional, values);
  } else {
    live.traceSegments.push(values);
  }
  renderLiveTraceFromSegments(live);
}

function appendLiveToolProgress(live, event) {
  if (!live || !event?.name) return;
  const key = traceKeyForEvent(event);
  const alreadyVisible = live.traceSegments.some(
    (segment) =>
      segment?.kind === "tool" &&
      (segment.id === key || (segment.provisional && segment.name === event.name)),
  );
  if (alreadyVisible) return;
  sealLiveReasoning(live, "");
  live.traceSegments.push({
    kind: "tool",
    id: key,
    label: event.label || "",
    name: event.name,
    input: "",
    output: "",
    depth: 0,
    parent_id: "",
    parent_name: "",
    provisional: true,
  });
  renderLiveTraceFromSegments(live);
}

function findLiveToolSegment(live, event) {
  if (!live || !event) return null;
  const key = traceKeyForEvent(event);
  return (
    live.traceSegments.find(
      (segment) => segment?.kind === "tool" && segment.id === key,
    ) || null
  );
}

function appendLiveToolEnd(live, event) {
  if (!live || !event) return;
  const parentId = eventParentId(event);
  const parentName = eventParentName(event);
  const eventToolName = event.name || "";
  const reasoningParent = parentId || (
    eventToolName.startsWith("delegate_") ? (event.id || eventToolName) : null
  );
  if (reasoningParent !== null) {
    sealLiveReasoning(live, reasoningParent);
  }
  const segment = findLiveToolSegment(live, event);
  if (segment) {
    segment.output = event.output || "";
    segment.image_paths = event.image_paths;
  } else {
    live.traceSegments.push({
      kind: "tool",
      id: traceKeyForEvent(event),
      label: event.label || "",
      name: event.name || "tool",
      input: "",
      output: event.output || "",
      image_paths: event.image_paths,
      depth: parentId ? 1 : 0,
      parent_id: parentId,
      parent_name: parentName,
    });
  }
  const delegateSegment = segment || findLiveToolSegment(live, event);
  const toolName = eventToolName || delegateSegment?.name || "";
  if (isDelegateTool(toolName)) {
    finalizeLiveAgentResult(
      live,
      event.output || "",
      delegateSegment?.id || event.id || toolName,
      toolName || parentName,
    );
    return;
  }
  renderLiveTraceFromSegments(live);
}

function scheduleStreamRender(live) {
  if (live.renderScheduled) return;
  live.renderScheduled = true;
  requestAnimationFrame(() => {
    live.renderScheduled = false;
    if (!live.streaming) return;
    const text = live.replyParts.join("");
    try {
      live.streamText.innerHTML = renderMarkdownStreaming(text);
      fixArtifactImagesIn(live.streamText);
    } catch (err) {
      console.error("Markdown render failed:", err);
      live.streamText.textContent = text;
    }
    scrollChatToBottom();
  });
}

function resetFinalStream(live) {
  live.streaming = false;
  live.replyParts = [];
  live.streamText.innerHTML = "";
  live.streamText.hidden = true;
  live.streamText.classList.remove("markdown");
  live.bubble.classList.remove("markdown");
  if (live.progressPanel) {
    live.progressPanel.hidden = false;
  }
  if (live.progressStatusBar) {
    live.progressStatusBar.hidden = false;
  }
}

function beginFinalStream(live) {
  if (live.streaming) return;
  sealLiveReasoning(live);
  live.streaming = true;
  setProgressStage(live, PROGRESS_STAGES.streaming);
  if (live.progressPanel) {
    // Keep completed delegation/results visible while the orchestrator writes.
    // Only the transient spinner/status line is no longer useful at this point.
    live.progressPanel.hidden = live.traceSegments.length === 0;
  }
  if (live.progressStatusBar) {
    live.progressStatusBar.hidden = true;
  }
  live.streamText.hidden = false;
  live.bubble.classList.add("markdown");
  live.streamText.classList.add("markdown");
  scheduleStreamRender(live);
}

function appendStreamToken(live, token) {
  if (!live.streaming) return;
  live.replyParts.push(token);
  scheduleStreamRender(live);
}

function collapseProgressPanel(live) {
  const panel = live.progressPanel;
  if (!panel?.isConnected) return;

  const tracePanel = buildTracePanel(serializeTraceSegments(live.traceSegments));
  if (!tracePanel) {
    panel.remove();
    return;
  }

  panel.replaceWith(tracePanel);
  live.progressPanel = tracePanel;
}

function reconcileFinalImages(live, reply) {
  collectRenderImages(live, reply);
  fixArtifactImagesIn(live.streamText);
  embedRenderImages(live);
}

function finalizeAgentReply(live, reply) {
  // Stop queued streaming renders before replacing the DOM with the authoritative final reply.
  // Otherwise an older requestAnimationFrame callback can overwrite the completed rendering.
  live.streaming = false;
  collapseProgressPanel(live);
  live.streamText.hidden = false;
  live.bubble.classList.add("markdown");
  live.streamText.classList.add("markdown");
  try {
    live.streamText.innerHTML = renderMarkdown(reply);
    reconcileFinalImages(live, reply);
  } catch (err) {
    console.error("Markdown render failed:", err);
    live.streamText.textContent = reply;
  }
  live.root.id = "";
  scrollChatToBottom();

  // Reconcile once more after the browser has drained any already queued streaming frame.
  // The helpers are idempotent, so existing images are retained without duplication.
  requestAnimationFrame(() => {
    if (!live.streamText?.isConnected) return;
    reconcileFinalImages(live, reply);
  });
}

function handleStreamEvent(live, event) {
  // A request may finish after the user changes projects. Its stored history
  // remains in the original project; it must not change the new chat or thread.
  if (live.projectId && live.projectId !== projectId) return event.type === "done" ? "done" : null;
  switch (event.type) {
    case "status":
      if (!live.streaming) {
        const statusMessage = (event.message || "").trim();
        const normalizedMessage = statusMessage.toLowerCase();
        if (
          normalizedMessage.includes("compos") ||
          normalizedMessage.includes("writing")
        ) {
          setProgressStage(live, PROGRESS_STAGES.composing, {
            hint: CONTENT.progress.composingHint,
            statusMessage,
          });
        } else if (live.hasThinking || getRunningToolLabel(live)) {
          // Worker/delegate status is useful precisely while its parent tool is
          // active (for example, the automatic project-memory handoff). Keep
          // the current progress stage and surface the more specific message.
          if (live.progressStatus) live.progressStatus.textContent = statusMessage;
          scrollChatToBottom();
        } else {
          setProgressStage(live, PROGRESS_STAGES.planning, { statusMessage });
        }
      }
      break;
    case "thinking":
      appendLiveThinking(
        live,
        event.text || "",
        eventParentId(event),
        eventParentName(event),
      );
      break;
    case "agent_result_delta":
      appendLiveAgentResult(
        live,
        event.text || "",
        eventParentId(event),
        eventParentName(event),
      );
      break;
    case "agent_result_reset":
      resetLiveAgentResult(live, eventParentId(event));
      break;
    case "tool_start":
      if (
        live.streaming &&
        !eventParentId(event) &&
        !preservesFinalStream(event.name)
      ) {
        resetFinalStream(live);
      }
      appendLiveToolStart(live, event);
      syncToolWaitingStatus(live);
      break;
    case "tool_progress":
      if (live.streaming) break;
      appendLiveToolProgress(live, event);
      if (!syncToolWaitingStatus(live)) {
        setProgressStage(live, PROGRESS_STAGES.toolWaiting, {
          label: formatToolLabel(event.label, event.name, eventParentName(event)),
          hint: CONTENT.progress.toolWaitingHint,
        });
      }
      break;
    case "tool_end": {
      appendLiveToolEnd(live, event);
      dichromaticWidget?.handleInteraction(event, live.projectId);
      collectRenderImages(live, extractToolEventImagePaths(event, live.projectId));
      if (live.streaming || live.hasThinking) break;
      if (syncToolWaitingStatus(live)) break;
      if (!eventParentId(event)) {
        setProgressStage(live, PROGRESS_STAGES.composing, {
          hint: CONTENT.progress.composingHint,
        });
      }
      break;
    }
    case "final_start":
      beginFinalStream(live);
      break;
    case "reset_stream":
      resetFinalStream(live);
      if (!live.hasThinking && !syncToolWaitingStatus(live)) {
        setProgressStage(live, PROGRESS_STAGES.planning);
      }
      break;
    case "token":
      appendStreamToken(live, event.text || "");
      break;
    case "done":
      collectRenderImages(live, event.image_paths || []);
      if (event.thread_id && (!live.projectId || live.projectId === projectId)) {
        threadId = event.thread_id;
        updateProjectDisplay();
      }
      finalizeAgentReply(live, event.reply || live.replyParts.join(""));
      return "done";
    case "error":
      throw new Error(event.message || "Stream failed");
    default:
      break;
  }
  return null;
}

function throwIfAborted(signal) {
  if (signal?.aborted) {
    throw new DOMException("The operation was aborted.", "AbortError");
  }
}

async function consumeSseStream(response, onEvent, signal = null) {
  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  while (true) {
    throwIfAborted(signal);
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });

    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const rawEvent = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const dataLine = rawEvent
        .split("\n")
        .find((line) => line.startsWith("data: "));
      if (dataLine) {
        const payload = JSON.parse(dataLine.slice(6));
        const result = onEvent(payload);
        if (result === "done") return;
      }
      boundary = buffer.indexOf("\n\n");
    }
  }
}

async function sendToAgentClassic(message, onEvent, signal = null, planningMode = "auto", context = { projectId, threadId }) {
  onEvent({ type: "status", message: CONTENT.progress.planning });

  const response = await fetch(CONFIG.apiBase + CONFIG.chatEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      project_id: context.projectId,
      thread_id: context.threadId,
      planning_mode: planningMode,
    }),
    signal,
  });

  if (!response.ok) {
    const err = await response.text().catch(() => response.statusText);
    throw new Error(err || `HTTP ${response.status}`);
  }

  const data = await response.json();
  const reply = data.reply ?? data.content ?? "";
  onEvent({ type: "final_start" });
  onEvent({
    type: "done",
    reply,
    project_id: data.project_id ?? context.projectId,
    thread_id: data.thread_id ?? context.threadId,
    image_paths: data.image_paths || [],
  });
}

async function sendToAgentStream(message, onEvent, signal = null, planningMode = "auto", context = { projectId, threadId }) {
  const response = await fetch(CONFIG.apiBase + CONFIG.chatStreamEndpoint, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      message,
      project_id: context.projectId,
      thread_id: context.threadId,
      planning_mode: planningMode,
    }),
    signal,
  });

  if (response.status === 405 || response.status === 404) {
    console.warn("Stream endpoint unavailable, falling back to POST /api/chat");
    await sendToAgentClassic(message, onEvent, signal, planningMode, context);
    return;
  }

  await consumeSseStream(response, onEvent, signal);
}

function setLoading(loading) {
  isLoading = loading;
  if (!loading) isStopping = false;
  updateComposerControls();
}

function resetCurrentRunState() {
  currentAbortController = null;
  currentRunMessage = "";
  currentLiveRun = null;
}

function currentStopPayload() {
  const partialTrace = serializeTraceSegments(currentLiveRun?.traceSegments || []);
  return {
    project_id: currentLiveRun?.projectId ?? projectId,
    thread_id: currentLiveRun?.threadId ?? threadId,
    message: currentRunMessage,
    reason: "user_stop",
    reasoning: "",
    partial_trace: partialTrace,
  };
}

function finalizeStoppedRun(live) {
  if (!live?.root?.isConnected || !live.root.id) return;
  for (const item of live.runningTools.values()) {
    item.classList.remove("is-running");
    item.classList.add("is-done");
    const statusEl = item.querySelector(".tool-call-status");
    if (statusEl) {
      statusEl.textContent = CONTENT.progress.toolStoppedBadge;
      statusEl.classList.add("is-done");
    }
  }
  live.runningTools.clear();
  collapseProgressPanel(live);
  live.streamText.hidden = false;
  live.bubble.classList.add("markdown");
  live.streamText.classList.add("markdown");
  live.streamText.textContent = CONTENT.progress.stopped;
  live.root.id = "";
  scrollChatToBottom();
}

async function requestStopCurrentRun() {
  if (!isLoading || isStopping) return;

  isStopping = true;
  updateComposerControls();
  const live = currentLiveRun;
  const controller = currentAbortController;
  const payload = currentStopPayload();
  let stopError = null;

  if (payload.project_id && payload.thread_id) {
    try {
      const response = await fetch(CONFIG.apiBase + CONFIG.chatStopEndpoint, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!response.ok) {
        const err = await response.text().catch(() => response.statusText);
        throw new Error(err || `HTTP ${response.status}`);
      }
    } catch (err) {
      stopError = err;
    }
  }

  controller?.abort();
  finalizeStoppedRun(live);
  if (stopError) {
    appendMessage("system", CONTENT.errors.stopFailed(stopError.message));
  }
  refreshProjectFiles();
  setLoading(false);
  resetCurrentRunState();
  input.focus();
}

let slashMatches = [];
let slashActiveIndex = 0;

function hideSlashMenu() {
  slashMatches = [];
  slashActiveIndex = 0;
  if (!slashMenu) return;
  slashMenu.hidden = true;
  slashMenu.replaceChildren();
  input.setAttribute("aria-expanded", "false");
  input.removeAttribute("aria-activedescendant");
}

function renderSlashMenu() {
  if (!slashMenu) return;
  if (!slashMatches.length) {
    hideSlashMenu();
    return;
  }
  slashMenu.hidden = false;
  input.setAttribute("aria-expanded", "true");
  input.setAttribute("aria-activedescendant", `slash-command-${slashActiveIndex}`);
  slashMenu.replaceChildren(
    ...slashMatches.map((item, index) => {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.id = `slash-command-${index}`;
      btn.className = `slash-menu-item${index === slashActiveIndex ? " is-active" : ""}`;
      btn.setAttribute("role", "option");
      btn.setAttribute("aria-selected", index === slashActiveIndex ? "true" : "false");
      btn.dataset.index = String(index);
      const cmd = document.createElement("span");
      cmd.className = "slash-menu-cmd";
      cmd.textContent = item.command;
      const desc = document.createElement("span");
      desc.className = "slash-menu-desc";
      desc.textContent = item.description;
      btn.append(cmd, desc);
      btn.addEventListener("mousedown", (event) => {
        event.preventDefault();
        applySlashCommand(item.command);
      });
      return btn;
    }),
  );
}

function refreshSlashMenu() {
  const value = input.value;
  const cursor = input.selectionStart ?? value.length;
  const before = value.slice(0, cursor);
  if (before.includes("\n")) {
    hideSlashMenu();
    return;
  }
  slashMatches = matchSlashCommands(before);
  if (!slashMatches.length) {
    hideSlashMenu();
    return;
  }
  slashActiveIndex = Math.min(slashActiveIndex, slashMatches.length - 1);
  renderSlashMenu();
}

function applySlashCommand(command) {
  const value = input.value;
  const token = value.match(/^\/\S*/);
  const rest = (token ? value.slice(token[0].length) : value).replace(/^\s*/, "");
  input.value = rest ? `${command} ${rest}` : `${command} `;
  const caret = command.length + 1;
  input.setSelectionRange(caret, caret);
  hideSlashMenu();
  autoResize();
  input.focus();
}

async function handleSend(text, { preserveDraft = false, expectedProjectId = null } = {}) {
  if (CONFIG.demoMode) return false;
  const parsed = parseChatCommand(text);
  if (!parsed.displayMessage || isLoading || !projectReady) return;
  if (expectedProjectId && expectedProjectId !== projectId) return false;

  if (parsed.missingTask) {
    appendMessage("system", parsed.missingTaskMessage);
    input.focus();
    return;
  }

  if (parsed.planningMode === "force" && pendingPlanDraft) {
    appendMessage("system", CONTENT.chat.planBlockedByPending);
    input.focus();
    return;
  }
  if (parsed.planningMode === "skip" && pendingPlanDraft) {
    // The API archives the pending draft before direct execution. Hide the
    // approval card optimistically; the final refresh restores it on failure.
    renderPlanApproval(null);
  }

  const message = parsed.displayMessage;
  const requestedProjectId = projectId;
  setLoading(true);

  let resolvedProjectId;
  try {
    resolvedProjectId = await ensureProject();
  } catch (err) {
    appendMessage("system", CONTENT.project.createFailed(err.message));
    setLoading(false);
    input.focus();
    return;
  }
  // Project creation/checking yields to other UI actions. Never continue an old
  // selection (or typed message) in a project chosen during that await.
  if (projectId !== resolvedProjectId || (requestedProjectId && requestedProjectId !== resolvedProjectId)) {
    setLoading(false);
    void cellContinuations.drain();
    return false;
  }

  appendMessage("user", message);
  if (!preserveDraft) input.value = "";
  hideSlashMenu();
  autoResize();

  const live = appendAgentLive();
  live.projectId = projectId;
  live.threadId = threadId;
  currentAbortController = new AbortController();
  currentRunMessage = message;
  currentLiveRun = live;
  updateComposerControls();

  try {
    await sendToAgentStream(
      message,
      (event) => handleStreamEvent(live, event),
      currentAbortController.signal,
      parsed.planningMode,
      { projectId: live.projectId, threadId: live.threadId },
    );
    if (live.projectId !== projectId) return;
    refreshProjectFiles();
    if (!live.root.id) {
      // finalized in done handler
    } else if (!live.streaming && live.replyParts.length === 0) {
      removeAgentLive();
      appendMessage("system", CONTENT.errors.emptyReply);
    } else if (live.replyParts.length > 0) {
      finalizeAgentReply(live, live.replyParts.join(""));
    }
  } catch (err) {
    if (live.projectId !== projectId) return;
    if (err?.name === "AbortError" && (isStopping || live.root.id === "")) {
      finalizeStoppedRun(live);
    } else {
      removeAgentLive();
      appendMessage("system", CONTENT.errors.requestFailed(err.message));
    }
  } finally {
    if (currentLiveRun === live) {
      setLoading(false);
      resetCurrentRunState();
    }
    await refreshPlanApproval();
    input.focus();
    void cellContinuations.drain();
  }
}

function autoResize() {
  input.style.height = "auto";
  input.style.height = Math.min(input.scrollHeight, 160) + "px";
}

function bindEvents() {
  form.addEventListener("submit", (e) => {
    e.preventDefault();
    handleSend(input.value);
  });

  input.addEventListener("keydown", (e) => {
    if (!slashMenu?.hidden && slashMatches.length) {
      if (e.key === "ArrowDown") {
        e.preventDefault();
        slashActiveIndex = (slashActiveIndex + 1) % slashMatches.length;
        renderSlashMenu();
        return;
      }
      if (e.key === "ArrowUp") {
        e.preventDefault();
        slashActiveIndex =
          (slashActiveIndex - 1 + slashMatches.length) % slashMatches.length;
        renderSlashMenu();
        return;
      }
      if (e.key === "Tab" || (e.key === "Enter" && !e.shiftKey)) {
        e.preventDefault();
        applySlashCommand(slashMatches[slashActiveIndex].command);
        return;
      }
      if (e.key === "Escape") {
        e.preventDefault();
        hideSlashMenu();
        return;
      }
    }
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend(input.value);
    }
  });

  input.addEventListener("input", () => {
    autoResize();
    refreshSlashMenu();
  });

  input.addEventListener("focus", refreshSlashMenu);

  input.addEventListener("blur", () => {
    // Delay so mousedown on a menu item can apply first.
    window.setTimeout(() => hideSlashMenu(), 120);
  });

  stopBtn.addEventListener("click", requestStopCurrentRun);

  planApproveBtn.addEventListener("click", () => {
    if (!pendingPlanDraft || isLoading) return;
    const proposed = pendingPlanDraft.proposed_plan || {};
    handleSend(
      CONTENT.planApproval.approveMessage(
        pendingPlanDraft.draft_id,
        proposed.revision ?? 1,
      ),
    );
  });

  planRequestChangesBtn.addEventListener("click", () => {
    if (!pendingPlanDraft || isLoading) return;
    input.value = CONTENT.planApproval.revisionPrompt(pendingPlanDraft.draft_id);
    autoResize();
    input.focus();
    input.setSelectionRange(input.value.length, input.value.length);
  });

  suggestionsEl.addEventListener("click", (e) => {
    const chip = e.target.closest(".chip");
    if (!chip) return;
    const prompt = chip.dataset.prompt;
    if (CONFIG.demoMode) navigateDemo(prompt);
    else if (prompt) handleSend(prompt);
  });

  projectSwitchBtn.addEventListener("click", openProjectModal);
  projectModalClose.addEventListener("click", closeProjectModal);
  projectModalBackdrop.addEventListener("click", closeProjectModal);
  projectNewBtn.addEventListener("click", createAndSwitchProject);
}

async function bootstrap() {
  applyTheme(resolveInitialTheme(), { persist: false });
  bindThemeToggle(themeToggleBtn, themeToggleLabelEl);
  initPage();
  initFilesPanel();
  if (!CONFIG.demoMode) dichromaticWidget = initDichromaticWidget({
    getProjectId: () => projectId,
    ensureProject,
    onSaved: () => refreshProjectFiles(),
    prepareChat: (text) => {
      input.value = input.value.trim() ? `${input.value.trim()}\n\n${text}` : text;
      autoResize();
      input.focus();
    },
    continueChat: (item) => {
      cellContinuations.enqueue(item);
      void cellContinuations.drain();
    },
  });
  setProjectSelectHandler((id) => switchToProject(id));
  bindEvents();
  await checkHealth();
  if (CONFIG.demoMode) await initDemoShowcase();
  await initProject();
}

bootstrap().catch((error) => {
  setBackendNotReady();
  const status = document.getElementById("demo-history-status");
  if (status) status.textContent = `Could not load the recorded project: ${error.message}`;
  const description = document.getElementById("welcome-description");
  if (description) description.textContent = "The recorded workspace could not be loaded. Refresh this page to try again.";
  console.error("Could not initialize demo", error);
});
