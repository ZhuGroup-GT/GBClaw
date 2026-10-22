/** API settings — edit when FastAPI backend routes change. */
export const CONFIG = {
  apiBase: "",
  chatEndpoint: "/api/chat",
  chatStreamEndpoint: "/api/chat/stream",
  chatStopEndpoint: "/api/chat/stop",
  healthEndpoint: "/api/health",
  projectsEndpoint: "/api/projects",
  registryEndpoint: "/api/registry",
  /** Directory list: adaptive poll, backs off when unchanged. */
  fileListPollMinMs: 2000,
  fileListPollMaxMs: 10000,
  /** Text preview (live files): fixed interval; content reload only when stat changes. */
  filePreviewPollMs: 500,
};
