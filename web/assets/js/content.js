/** Page copy — edit text here without touching layout or logic. */
export const CONTENT = {
  page: {
    title: "GBClaw — Grain Boundary Research Agent",
    lang: "en",
  },

  brand: {
    logoSrc: "/brand-assets/GBClaw-icon.svg",
    logoDarkSrc: "/brand-assets/GBClaw-icon-light.svg",
    logoAlt: "GBClaw",
    name: "GBClaw",
    tagline: "Grain Boundary Research Assistant Developed by Zhu Group at Georgia Tech",
  },

  status: {
    title: "Backend connection status",
    connected: "Connected",
    backendNotReady: "Backend not ready",
  },

  welcome: {
    title: "Grain Boundary Research Assistant",
    description:
      "GB Claw helps researchers with CSL analysis, structure generation, and related GB workflows. " +
      "Send a message to start a new project, or open an existing one from the sidebar.",
    suggestions: [
      { label: "What is a CSL grain boundary?", prompt: "What is a CSL grain boundary?" },
      { label: "How do I generate a Σ5 symmetric tilt GB?", prompt: "How do I generate a Σ5 symmetric tilt GB?" },
      { label: "What are GBclaw's main capabilities?", prompt: "What are GBclaw's main capabilities?" },
    ],
  },

  chat: {
    logLabel: "Chat history",
    inputPlaceholder: "Ask a question — type / for commands, Enter to send…",
    inputAriaLabel: "Message input",
    sendAriaLabel: "Send",
    stopAriaLabel: "Stop current run",
    stopTitle: "Stop current run",
    hintPrefix: "API endpoint: ",
    hintEndpoint: "POST /api/chat/stream",
    hintProjectPrefix: " · Project: ",
    hintThreadPrefix: " · Thread: ",
    threadPlaceholder: "—",
    planBlockedByPending:
      "A plan draft is already awaiting approval. Approve, reject, or request changes before starting /plan.",
  },

  planApproval: {
    title: (title) => `Plan awaiting approval: ${title}`,
    meta: (draftId, revision, stepCount) =>
      `${draftId} · proposed revision ${revision} · ${stepCount} step${stepCount === 1 ? "" : "s"}`,
    approve: "Approve & execute",
    requestChanges: "Disagree / request changes",
    approveMessage: (draftId, revision) =>
      `I explicitly approve project plan draft ${draftId}, proposed revision ${revision}. Activate it and execute the approved plan in dependency order.`,
    revisionPrompt: (draftId) =>
      `I do not approve project plan draft ${draftId}. Please revise it as follows: `,
    loadFailed: (message) => `Could not load pending plan approval: ${message}`,
  },

  project: {
    label: "Project",
    switch: "Switch",
    modalTitle: "Open project workspace",
    modalHint: "Choose an existing project under projects/, or start a new session.",
    newProject: "New session",
    close: "Close",
    loading: "Loading…",
    newSession: "new session",
    newSessionReady: "Started a new session. Your first message will create a project workspace.",
    switched: (id) => `Switched to project \`${id}\`.`,
    created: (id) => `Created project \`${id}\`.`,
    emptyList: "No saved projects yet.",
    loadFailed: (message) => `Could not load projects: ${message}`,
    initFailed: (message) => `Could not initialize project workspace: ${message}`,
    createFailed: (message) => `Could not create project: ${message}`,
    delete: "Delete",
    deleteConfirm: (id) =>
      `Delete project "${id}"?\n\nThis permanently removes its folder under projects/.`,
    deleted: (id) => `Deleted project \`${id}\`.`,
    deleteFailed: (message) => `Could not delete project: ${message}`,
  },

  files: {
    title: "Project files",
    root: "root",
    parent: "..",
    refresh: "Refresh",
    download: "Download",
    closePreview: "Close preview",
    liveBadge: "live",
    loading: "Loading…",
    noProject: "Send a message to create a project, or open one below.",
    pickProject: "Open an existing project:",
    emptyDir: "This folder is empty.",
    loadFailed: (message) => `Could not list files: ${message}`,
    previewFailed: (message) => `Could not load file: ${message}`,
  },

  citation: {
    lead: "If you use this architecture in your work, or find it helpful, please cite:",
    authors: "Y. Liu, K. Ding, X. Gong, Z. Wang, Y. Yang, Y. Che, W. Chen, J. Kacher, O. Pierron, S. Ogata, T. Zhu, ",
    title: "GBClaw: An agentic AI framework for atomistic grain boundary research and mechanistic discovery",
    venue: ", arXiv preprint arXiv:2610.28629 (2026). ",
    doi: "https://doi.org/10.48550/arXiv.2610.28629",
  },

  labels: {
    user: "You",
    agent: "GBclaw",
    system: "System",
  },

  progress: {
    planning: "Planning your request…",
    reasoning: "Reasoning through the task…",
    waitingForTool: (label) => `Waiting for ${label} to finish…`,
    composing: "Writing final answer…",
    streamingAnswer: "Streaming answer…",
    toolDoneBadge: "Done",
    toolStoppedBadge: "Stopped",
    toolRunning: "Running",
    viewTrace: (detail) =>
      detail ? `View execution trace (${detail})` : "View execution trace",
    reasoningSection: "Reasoning",
    toolStepsSection: "Tool calls",
    delegatedTask: "Delegated task",
    agentResult: "Agent result",
    reasoningHint: "GBclaw is deciding what to do next.",
    reasoningWithheld: (tokens) =>
      tokens
        ? `Reasoned for ${tokens} tokens; the provider returned no summary.`
        : "Reasoned; the provider returned no summary.",
    toolWaitingHint: "The tool is running — results will appear when it returns.",
    composingHint: "Turning tool results into a reply for you.",
    stopped: "Stopped by user. Partial progress was saved.",
    stopping: "Stopping current run…",
  },

  footer: "GBClaw © 2026 — FastAPI + LangChain",

  errors: {
    requestFailed: (message) => `Request failed: ${message}`,
    emptyReply: "Agent returned an empty reply.",
    stopFailed: (message) => `Stop requested locally, but backend cancellation was not confirmed: ${message}`,
    streamUnavailable:
      "Streaming endpoint unavailable. Restart the backend with ./start_web.sh and hard-refresh the page (Ctrl+Shift+R).",
  },
};
