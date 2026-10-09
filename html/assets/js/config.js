/** Public, read-only DemoProj showcase settings. */
export const CONFIG = {
  demoMode: true,
  demoProjectId: "DemoProj",
  apiBase: "",
  chatEndpoint: "/api/chat",
  chatStreamEndpoint: "/api/chat/stream",
  chatStopEndpoint: "/api/chat/stop",
  healthEndpoint: "/api/health",
  projectsEndpoint: "/api/projects",
  /** Directory list: adaptive poll, backs off when unchanged. */
  fileListPollMinMs: 2000,
  fileListPollMaxMs: 10000,
  /** Text preview (live files): fixed interval; content reload only when stat changes. */
  filePreviewPollMs: 500,
};
