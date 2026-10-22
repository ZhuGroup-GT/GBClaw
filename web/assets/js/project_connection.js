import { CONFIG } from "./config.js";

const connections = new Map();
const openingOrOwned = new Set();

export function closeProjectConnections() {
  for (const connection of openingOrOwned) connection.close();
}

export function projectHeaders(projectId) {
  const connection = connections.get(projectId);
  if (!connection?.token || connection.socket.readyState !== 1) {
    throw new Error("Project connection lost. Reopen the project to continue.");
  }
  return { "X-GBclaw-Project-Token": connection.token };
}

export function connectProject(projectId, { onLost = () => {}, WebSocketImpl = globalThis.WebSocket } = {}) {
  const url = new URL(`${CONFIG.apiBase}${CONFIG.projectsEndpoint}/${encodeURIComponent(projectId)}/connection`, window.location.href);
  url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
  return new Promise((resolve, reject) => {
    const socket = new WebSocketImpl(url.href);
    let settled = false;
    let intentionalClose = false;
    const connection = {
      socket,
      token: null,
      project: null,
      close() {
        intentionalClose = true;
        forget();
        socket.close();
      },
    };
    openingOrOwned.add(connection);
    function forget() {
      connection.token = null;
      openingOrOwned.delete(connection);
      if (connections.get(projectId) === connection) connections.delete(projectId);
    }
    function fail(message) {
      if (settled) return;
      settled = true;
      clearTimeout(timeout);
      reject(new Error(message));
      connection.close();
    }
    const timeout = setTimeout(() => fail("Project connection timed out. Check the service and retry."), 10000);
    socket.onmessage = (event) => {
      if (settled) return;
      let data;
      try { data = JSON.parse(event.data); } catch { fail("Invalid project connection response."); return; }
      if (data.type === "error") { fail(data.detail || "Project is occupied by another frontend."); return; }
      if (data.type !== "claimed" || !data.token || data.project?.project_id !== projectId) {
        fail("Cannot verify project ownership."); return;
      }
      settled = true;
      clearTimeout(timeout);
      connection.token = data.token;
      connection.project = data.project;
      connections.set(projectId, connection);
      resolve(connection);
    };
    socket.onerror = () => fail("Cannot connect to the project. Check the service and retry.");
    socket.onclose = () => {
      const hadOwnership = Boolean(connection.token);
      forget();
      fail("Project connection closed. Reopen the project to continue.");
      if (hadOwnership && !intentionalClose) onLost();
    };
  });
}
