/** Backend declarations the UI reads at startup (GET /api/registry). */

import { CONFIG } from "./config.js";

const registry = {
  delegateLabels: Object.freeze({}),
  preserveFinalStream: new Set(),
  chatCommands: Object.freeze([]),
};

/**
 * Load the registry once before rendering. Without it the UI falls back to
 * humanized tool names, and the server still parses slash commands.
 */
export async function loadRegistry() {
  try {
    const res = await fetch(CONFIG.apiBase + CONFIG.registryEndpoint);
    if (!res.ok) return;
    const data = await res.json();
    registry.delegateLabels = Object.freeze({ ...(data.delegate_labels || {}) });
    registry.preserveFinalStream = new Set(data.preserve_final_stream || []);
    registry.chatCommands = Object.freeze(
      (data.chat_commands || []).map((item) =>
        Object.freeze({
          command: item.command,
          planningMode: item.planning_mode,
          description: item.description,
          missingTaskMessage: item.missing_task_message,
        }),
      ),
    );
  } catch {
    // backend not running — keep the empty defaults
  }
}

export function delegateLabel(name) {
  return registry.delegateLabels[name] || "";
}

export function preservesFinalStream(name) {
  return registry.preserveFinalStream.has(String(name || ""));
}

export function chatCommands() {
  return registry.chatCommands;
}
