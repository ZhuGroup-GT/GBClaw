/** Browser slash-command matching and parsing over the backend's command list. */

import { chatCommands } from "./registry.js?v=1";

export function matchSlashCommands(text) {
  const token = String(text || "");
  if (!/^\/\S*$/.test(token)) return [];
  const normalized = token.toLowerCase();
  return chatCommands().filter((item) => item.command.startsWith(normalized));
}

export function parseChatCommand(text) {
  const displayMessage = String(text || "").trim();
  const match = displayMessage.match(/^(\/\S+)(?:\s+([\s\S]*))?$/);
  const definition = match
    ? chatCommands().find(
        (item) => item.command === String(match[1] || "").toLowerCase(),
      )
    : null;
  if (!definition) {
    return {
      displayMessage,
      planningMode: "auto",
      missingTask: false,
      missingTaskMessage: "",
    };
  }

  const task = String(match[2] || "").trim();
  return {
    displayMessage,
    planningMode: definition.planningMode,
    missingTask: !task,
    missingTaskMessage: definition.missingTaskMessage,
  };
}
