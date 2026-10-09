/** Canonical browser slash-command registry and parser. */

export const CHAT_COMMANDS = Object.freeze([
  Object.freeze({
    command: "/plan",
    planningMode: "force",
    description: "Force the planning agent for this request",
    missingTaskMessage:
      "Add a task after /plan, for example: /plan compare tilt GB candidates.",
  }),
  Object.freeze({
    command: "/noplan",
    planningMode: "skip",
    description: "Execute this request without a project plan",
    missingTaskMessage:
      "Add a task after /noplan, for example: /noplan build and render a compact GB.",
  }),
]);

export function matchSlashCommands(text) {
  const token = String(text || "");
  if (!/^\/\S*$/.test(token)) return [];
  const normalized = token.toLowerCase();
  return CHAT_COMMANDS.filter((item) => item.command.startsWith(normalized));
}

export function parseChatCommand(text) {
  const displayMessage = String(text || "").trim();
  const match = displayMessage.match(/^(\/\S+)(?:\s+([\s\S]*))?$/);
  const definition = match
    ? CHAT_COMMANDS.find(
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
