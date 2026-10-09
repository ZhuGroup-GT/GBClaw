/** Page copy — edit text here without touching layout or logic. */
export const CONTENT = {
  page: {
    title: "GBClaw — DemoProj Showcase",
    lang: "en",
  },

  brand: {
    logoSrc: "assets/icon/GBClaw-on-white.png",
    logoAlt: "GBClaw",
    name: "GBClaw",
    tagline: "DemoProj showcase · Zhu Group at Georgia Tech",
  },

  status: {
    title: "Recorded workspace status",
    connected: "Demo ready",
    loading: "Loading demo",
    backendNotReady: "Demo unavailable",
  },

  welcome: {
    title: "Explore DemoProj",
    description:
      "A recorded grain boundary research workspace. Follow an FCC Ni (110)/(111) study through its conversation, saved project memory, and simulation outputs.",
    suggestions: [
      { label: "Project overview", prompt: "summary" },
      { label: "Recorded conversation", prompt: "history" },
      { label: "Results gallery", prompt: "gallery" },
      { label: "Dichromatic map", prompt: "map" },
    ],
  },

  chat: {
    logLabel: "Recorded DemoProj conversation",
    inputPlaceholder: "Recorded workspace — live chat is unavailable",
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
    switch: "Project details",
    modalTitle: "DemoProj workspace",
    modalHint: "This demonstration presents the saved DemoProj workspace. Its conversation and outputs are available for read-only browsing.",
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
    noProject: "The recorded project files are loading.",
    pickProject: "Recorded workspace:",
    emptyDir: "This folder is empty.",
    loadFailed: (message) => `Could not list files: ${message}`,
    previewFailed: (message) => `Could not load file: ${message}`,
  },

  labels: {
    user: "Researcher",
    agent: "GBClaw",
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
    toolWaitingHint: "The tool is running — results will appear when it returns.",
    composingHint: "Turning tool results into a reply for you.",
    stopped: "Stopped by user. Partial progress was saved.",
    stopping: "Stopping current run…",
  },

  footer: "GBClaw © 2026 · Zhu Group, Georgia Tech · DemoProj research showcase",

  errors: {
    requestFailed: (message) => `Request failed: ${message}`,
    emptyReply: "Agent returned an empty reply.",
    stopFailed: (message) => `Stop requested locally, but backend cancellation was not confirmed: ${message}`,
    streamUnavailable:
      "Streaming endpoint unavailable. Restart the backend with ./start_web.sh and hard-refresh the page (Ctrl+Shift+R).",
  },
};
