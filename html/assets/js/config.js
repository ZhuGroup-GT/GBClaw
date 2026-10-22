/** Public, read-only research showcase settings. */
export const DEMO_PROJECT_IDS = Object.freeze(["Demo-GPT"]);

export function selectedDemoProjectId(search = globalThis.location?.search || "") {
  const requested = new URLSearchParams(search).get("project");
  return DEMO_PROJECT_IDS.includes(requested) ? requested : "Demo-GPT";
}

export const CONFIG = {
  demoMode: true,
  demoProjectId: selectedDemoProjectId(),
  demoProjectIds: DEMO_PROJECT_IDS,
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
