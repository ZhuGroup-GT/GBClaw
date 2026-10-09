/** Read-only equivalents of the workspace APIs, backed by exported demo data. */
import { CONFIG } from "./config.js";

const nativeFetch = globalThis.fetch.bind(globalThis);
const projectRoot = new URL("../../../projects/DemoProj/", import.meta.url);
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
  if (projectId !== CONFIG.demoProjectId || encoded === null || !encoded) return "";
  return new URL(encoded, projectRoot).href;
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

export const loadDemo = () => readJson("demo.json");

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
  const demo = await loadDemo();
  if (url.pathname === CONFIG.projectsEndpoint) return json({ projects: [demo.project] });
  const prefix = `${CONFIG.projectsEndpoint}/${encodeURIComponent(CONFIG.demoProjectId)}`;
  if (url.pathname !== prefix && !url.pathname.startsWith(`${prefix}/`)) {
    return json({ detail: "This showcase contains DemoProj only." }, 404);
  }
  const route = url.pathname.slice(prefix.length);
  if (!route) return json(demo.project);
  if (route === "/memory") return json({ ...demo.memory, pending_plan: null });
  if (route === "/history") {
    const history = await readJson("history.json");
    const offset = Math.max(0, Number.parseInt(url.searchParams.get("offset"), 10) || 0);
    const limit = Math.min(200, Math.max(1, Number.parseInt(url.searchParams.get("limit"), 10) || 200));
    return json({ project_id: CONFIG.demoProjectId, offset, limit,
      turns: history.turns.slice(offset, offset + limit), has_more: offset + limit < history.turns.length });
  }
  if (route.startsWith("/history/")) {
    const id = route.slice("/history/".length);
    const history = await readJson("history.json");
    if (!history.turns.some((turn) => turn.turn_id === id)) return json({ detail: "Unknown recorded turn." }, 404);
    return json(await readJson(`turns/${encodeURIComponent(id)}.json`));
  }
  if (route === "/files" || route.startsWith("/files/")) {
    const path = url.searchParams.get("path") || "";
    if (cleanPath(path) === null) return json({ detail: "Invalid project path." }, 400);
    const isDirectory = Object.hasOwn(demo.directories, path);
    const entry = Object.hasOwn(demo.files, path) ? demo.files[path] : null;
    if (!isDirectory && !entry) return json({ detail: "File not present in this demo." }, 404);
    const fingerprint = `demo:${demo.project.updated_at}:${path}`;
    if (route === "/files") {
      if (!isDirectory) return json({ detail: "Expected a directory." }, 400);
      return json({ path, entries: demo.directories[path], fingerprint });
    }
    if (route === "/files/stat") return json({ ...(entry || { path, entry_type: "dir" }), watch: false, fingerprint });
    if (route === "/files/content") {
      if (!entry?.is_text) return json({ detail: "Download this file to view its binary data." }, 415);
      return json({ ...entry, content: await readPreview(projectFileUrl(CONFIG.demoProjectId, path), entry.size, options.signal), watch: false });
    }
    if ((route === "/files/raw" || route === "/files/download") && entry) {
      return nativeFetch(projectFileUrl(CONFIG.demoProjectId, path), options);
    }
  }
  return json({ detail: "This feature requires a live research session and is unavailable in the demo." }, 404);
}
