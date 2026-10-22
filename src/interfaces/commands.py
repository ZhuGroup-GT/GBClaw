"""Parse user-facing planning commands before they reach the orchestrator."""

from __future__ import annotations

import re
from dataclasses import dataclass

from planning.mode import PlanningMode, coerce_planning_mode


@dataclass(frozen=True)
class ChatCommand:
    """A planning command shared by the API, the CLI and the Web UI."""

    command: str
    planning_mode: PlanningMode
    description: str
    example: str

    @property
    def missing_task_message(self) -> str:
        return f"Add a task after {self.command}, for example: {self.command} {self.example}."

    def public_record(self) -> dict[str, str]:
        return {"command": self.command, "planning_mode": self.planning_mode.value,
                "description": self.description, "missing_task_message": self.missing_task_message}


CHAT_COMMANDS = (
    ChatCommand("/plan", PlanningMode.FORCE, "Force the planning agent for this request",
                "compare tilt GB candidates"),
    ChatCommand("/noplan", PlanningMode.SKIP, "Execute this request without a project plan",
                "build and render a compact GB"),
)


def chat_command(planning_mode: PlanningMode) -> ChatCommand:
    return next(item for item in CHAT_COMMANDS if item.planning_mode is planning_mode)


_PLAN_PREFIX_RE = re.compile(r"^/plan(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)
_NOPLAN_PREFIX_RE = re.compile(r"^/noplan(?:\s+(.*))?$", re.IGNORECASE | re.DOTALL)


@dataclass(frozen=True)
class ParsedChatCommand:
    """Normalized chat payload after stripping UI commands."""

    message: str
    display_message: str
    planning_mode: PlanningMode


def parse_chat_command(
    message: str,
    *,
    planning_mode: PlanningMode | str = PlanningMode.AUTO,
) -> ParsedChatCommand:
    """Strip a planning command and resolve the per-turn planning policy.

    ``display_message`` keeps the original user text (including ``/plan`` or
    ``/noplan``) for chat history. ``message`` is what the orchestrator model
    receives.
    """

    raw = str(message or "")
    stripped = raw.strip()
    requested_mode = coerce_planning_mode(planning_mode)

    plan_match = _PLAN_PREFIX_RE.match(stripped)
    noplan_match = _NOPLAN_PREFIX_RE.match(stripped)
    command_mode = PlanningMode.AUTO
    match = None
    if plan_match is not None:
        command_mode = PlanningMode.FORCE
        match = plan_match
    elif noplan_match is not None:
        command_mode = PlanningMode.SKIP
        match = noplan_match

    if match is not None:
        if (
            requested_mode is not PlanningMode.AUTO
            and requested_mode is not command_mode
        ):
            raise ValueError(
                f"Planning command conflicts with planning_mode={requested_mode.value!r}"
            )
        requested_mode = command_mode
        task = (match.group(1) or "").strip()
    else:
        task = stripped

    return ParsedChatCommand(
        message=task,
        display_message=stripped,
        planning_mode=requested_mode,
    )


__all__ = ["CHAT_COMMANDS", "ChatCommand", "ParsedChatCommand", "PlanningMode", "chat_command",
           "parse_chat_command"]
