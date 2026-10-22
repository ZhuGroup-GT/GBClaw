/** Read-only equivalents of the workspace APIs, backed by exported demo data. */
import { CONFIG } from "./config.js?v=3";

const nativeFetch = globalThis.fetch.bind(globalThis);
const projectsRoot = new URL("../../../projects/", import.meta.url);
const dataRoot = new URL("../../data/", import.meta.url);
const jsonCache = new Map();
const PREVIEW_BYTES = 256 * 1024;

function cleanPath(path = "") {
  if (typeof path !== "string" || /[\\\x00-\x1f\x7f]/.test(path)) return null;
  if (path === "") return "";
  const parts = path.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) return null;
  return parts.map(encodeURIComponent).join("/");
}

export function projectFileUrl(projectId, path) {
  const encoded = cleanPath(path);
  if (!CONFIG.demoProjectIds.includes(projectId) || encoded === null || !encoded) return "";
  return new URL(`${encodeURIComponent(projectId)}/${encoded}`, projectsRoot).href;
}

async function readJson(name) {
  if (!jsonCache.has(name)) {
    const promise = nativeFetch(new URL(name, dataRoot)).then(async (response) => {
      if (!response.ok) throw new Error(`Could not load demo data (${response.status}).`);
      return response.json();
    }).catch((error) => { jsonCache.delete(name); throw error; });
    jsonCache.set(name, promise);
  }
  return jsonCache.get(name);
}

export async function loadDemoProjects() {
  const projects = await readJson("projects.json");
  if (!Array.isArray(projects)) throw new Error("Invalid showcase project index.");
  const ids = new Set();
  return projects.filter((project) => CONFIG.demoProjectIds.includes(project?.project_id)).map((project) => {
    if (ids.has(project.project_id) || project.data_path !== "") {
      throw new Error("Invalid showcase project entry.");
    }
    ids.add(project.project_id);
    return project;
  });
}

async function readProjectJson(projectId, name) {
  if (!CONFIG.demoProjectIds.includes(projectId)) throw new Error("This project is not published in the showcase.");
  const projects = await loadDemoProjects();
  const project = projects.find((entry) => entry.project_id === projectId);
  if (!project) throw new Error("This project is not published in the showcase.");
  return readJson(`${project.data_path}${name}`);
}

export async function loadDemo(projectId = CONFIG.demoProjectId) {
  const demo = await readProjectJson(projectId, "demo.json");
  if (demo.project?.project_id !== projectId) throw new Error("The recorded data belongs to a different project.");
  return demo;
}

function json(value, status = 200) {
  return new Response(JSON.stringify(value), {
    status, headers: { "Content-Type": "application/json; charset=utf-8" },
  });
}

async function readPreview(url, size, signal) {
  const response = await nativeFetch(url, {
    headers: { Range: `bytes=0-${PREVIEW_BYTES - 1}` }, signal,
  });
  if (!response.ok) throw new Error(`Could not read file (${response.status}).`);
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let content = "";
  let bytes = 0;
  try {
    while (bytes < PREVIEW_BYTES) {
      const { value, done } = await reader.read();
      if (done) break;
      const chunk = value.subarray(0, PREVIEW_BYTES - bytes);
      content += decoder.decode(chunk, { stream: true });
      bytes += chunk.length;
    }
    content += decoder.decode();
  } finally {
    await reader.cancel().catch(() => {});
  }
  if (size > PREVIEW_BYTES) {
    content += "\n\n[Preview limited to 256 KB. Download the file to read the complete data.]";
  }
  return content;
}

/** Explicit transport import keeps the public app from calling any live API. */
export async function demoFetch(resource, options = {}) {
  const url = new URL(resource instanceof Request ? resource.url : resource, globalThis.location?.href || "http://demo.invalid/");
  if (!url.pathname.startsWith("/api/")) return nativeFetch(resource, options);
  const method = (options.method || (resource instanceof Request ? resource.method : "GET")).toUpperCase();
  if (method !== "GET" && method !== "HEAD") return json({ detail: "This recorded demo is read-only." }, 405);
  if (url.pathname === CONFIG.healthEndpoint) {
    await loadDemo();
    return json({ status: "ok", mode: "demo" });
  }
  if (url.pathname === CONFIG.projectsEndpoint) {
    const projects = await loadDemoProjects();
    return json({ projects: await Promise.all(projects.map(async (entry) => (await loadDemo(entry.project_id)).project)) });
  }
  const prefix = `${CONFIG.projectsEndpoint}/`;
  if (!url.pathname.startsWith(prefix)) return json({ detail: "Unknown showcase route." }, 404);
  const [encodedProjectId, ...segments] = url.pathname.slice(prefix.length).split("/");
  let projectId;
  try { projectId = decodeURIComponent(encodedProjectId); } catch { return json({ detail: "Invalid project ID." }, 400); }
  if (!CONFIG.demoProjectIds.includes(projectId) || !(await loadDemoProjects()).some((entry) => entry.project_id === projectId)) {
    return json({ detail: "This project is not published in the showcase." }, 404);
  }
  const demo = await loadDemo(projectId);
  const route = segments.length ? `/${segments.join("/")}` : "";
  if (!route) return json(demo.project);
  if (route === "/memory") return json({ ...demo.memory, pending_plan: null });
  if (route === "/history") {
    const history = await readProjectJson(projectId, "history.json");
    const offset = Math.max(0, Number.parseInt(url.searchParams.get("offset"), 10) || 0);
    const limit = Math.min(200, Math.max(1, Number.parseInt(url.searchParams.get("limit"), 10) || 200));
    return json({ project_id: projectId, offset, limit,
      turns: history.turns.slice(offset, offset + limit), has_more: offset + limit < history.turns.length });
  }
  if (route.startsWith("/history/")) {
    let id;
    try { id = decodeURIComponent(route.slice("/history/".length)); } catch { return json({ detail: "Invalid recorded turn." }, 400); }
    const history = await readProjectJson(projectId, "history.json");
    if (!history.turns.some((turn) => turn.turn_id === id)) return json({ detail: "Unknown recorded turn." }, 404);
    return json(await readProjectJson(projectId, `turns/${encodeURIComponent(id)}.json`));
  }
  if (route === "/files" || route.startsWith("/files/")) {
    const path = url.searchParams.get("path") || "";
    if (cleanPath(path) === null) return json({ detail: "Invalid project path." }, 400);
    const isDirectory = Object.hasOwn(demo.directories, path);
    const entry = Object.hasOwn(demo.files, path) ? demo.files[path] : null;
    if (!isDirectory && !entry) return json({ detail: "File not present in this demo." }, 404);
    const fingerprint = `demo:${projectId}:${demo.project.updated_at}:${path}`;
    if (route === "/files") {
      if (!isDirectory) return json({ detail: "Expected a directory." }, 400);
      return json({ path, entries: demo.directories[path], fingerprint });
    }
    if (route === "/files/stat") return json({ ...(entry || { path, entry_type: "dir" }), watch: false, fingerprint });
    if (route === "/files/content") {
      if (!entry?.is_text) return json({ detail: "Download this file to view its binary data." }, 415);
      return json({ ...entry, content: await readPreview(projectFileUrl(projectId, path), entry.size, options.signal), watch: false });
    }
    if ((route === "/files/raw" || route === "/files/download") && entry) {
      return nativeFetch(projectFileUrl(projectId, path), options);
    }
  }
  return json({ detail: "This feature requires a live research session and is unavailable in the demo." }, 404);
}
