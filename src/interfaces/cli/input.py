"""Terminal input: readline line editing, slash-command completion and multi-line messages.

Each message starts with one readline-edited line, so the arrow keys, Ctrl+A/E/W,
Tab completion of slash commands and a per-project history behave as in a shell.
A message continues on the next line when the line ends with a backslash or
opens a ``` fence; a pasted block arrives whole because bracketed paste is on.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from interfaces.commands import CHAT_COMMANDS

LOGGER = logging.getLogger(__name__)

PROMPT = "> "
CONTINUATION_PROMPT = "… "
FENCE = "```"
HISTORY_LENGTH = 1000

# One blurb per command, shown by /help and Tab completion. Keys are exact slash tokens.
SLASH_COMMAND_HELP: dict[str, str] = {
    "/help": "show this help",
    **{item.command: f"{item.description[0].lower()}{item.description[1:]}; a task must follow"
       for item in CHAT_COMMANDS},
    "/history": "show recent turns of this project (optional N, default 10)",
    "/projects": "list all projects",
    "/switch": "switch to another project",
    "/thread": "show the project and thread IDs",
    "/clear": "clear the screen",
    "/quit": "exit the CLI (also /exit, /q)",
}
COMMAND_ALIASES: dict[str, str] = {"/exit": "/quit", "/q": "/quit"}
SLASH_COMMANDS: frozenset[str] = frozenset({*SLASH_COMMAND_HELP, *COMMAND_ALIASES})
_COMPLETIONS = tuple(sorted(SLASH_COMMAND_HELP))

INPUT_HELP = (
    "  input:\n"
    "    End a line with \\ to continue on the next line. A line that is ``` opens a block\n"
    "    which the next ``` line closes. Pasted text is sent as one message. To send a\n"
    "    message that begins with \"/\", start it with a space.\n"
    "    Tab completes commands; ↑/↓ recall earlier messages of this project.\n"
    "    Ctrl+C stops a running turn (twice: exit without waiting). At the prompt, Ctrl+C\n"
    "    twice within two seconds or Ctrl+D exits.\n"
)


def canonical_command(token: str) -> str | None:
    """The command *token* names (aliases resolved), or None when it is not one."""
    lowered = token.lower()
    lowered = COMMAND_ALIASES.get(lowered, lowered)
    return lowered if lowered in SLASH_COMMAND_HELP else None


def format_slash_help() -> str:
    width = max(len(cmd) for cmd in _COMPLETIONS)
    lines = ["  commands:"]
    lines += [f"    {cmd:<{width}}  {SLASH_COMMAND_HELP[cmd]}" for cmd in _COMPLETIONS]
    return "\n".join(lines) + "\n\n" + INPUT_HELP


def _readline() -> ModuleType | None:
    """GNU readline when the terminal can use it, else None."""
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        return None
    try:
        import readline
    except ImportError:
        return None
    return readline


def setup_readline() -> bool:
    """Install slash-command completion and bracketed paste. Returns True when active."""
    readline = _readline()
    if readline is None:
        return False

    def completer(text: str, state: int) -> str | None:
        buffer = readline.get_line_buffer()
        # Only the first token of a line can be a command.
        if buffer[: readline.get_begidx()].strip() or not text.startswith("/"):
            return None
        matches = [cmd for cmd in _COMPLETIONS if cmd.startswith(text.lower())]
        return matches[state] if state < len(matches) else None

    readline.set_completer(completer)
    delims = readline.get_completer_delims()
    if "/" in delims:
        readline.set_completer_delims(delims.replace("/", ""))
    for binding in ("tab: complete", "set enable-bracketed-paste on", "set show-all-if-ambiguous on"):
        try:
            readline.parse_and_bind(binding)
        except Exception:  # an unknown variable on an exotic readline build
            LOGGER.debug("readline binding %r rejected", binding)
    readline.set_history_length(HISTORY_LENGTH)
    return True


class LineHistory:
    """Readline history kept per project, under the project's chat directory."""

    def __init__(self) -> None:
        self._path: Path | None = None
        self._readline = _readline()

    def activate(self, path: Path) -> None:
        """Save the current project's history, then load *path*."""
        self.save()
        self._path = path
        if self._readline is None:
            return
        self._readline.clear_history()
        try:
            if path.is_file():
                self._readline.read_history_file(str(path))
        except OSError as exc:
            LOGGER.debug("Cannot read CLI history %s: %s", path, exc)

    def save(self) -> None:
        if self._readline is None or self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._readline.write_history_file(str(self._path))
        except OSError as exc:
            LOGGER.debug("Cannot write CLI history %s: %s", self._path, exc)


def _fence_open(lines: list[str]) -> bool:
    return sum(1 for line in lines if line.strip().startswith(FENCE)) % 2 == 1


def collect_message(input_fn: Callable[[str], str] = input) -> str | None:
    """Read one message. Returns None on EOF and "" for a blank line.

    A line ending with a backslash continues on the next line (the backslash is
    dropped); a ``` line opens a block that continues until a ``` line closes
    it. Pasted text that already contains newlines is one message, subject to
    the same rules on its last line. A bare "/" means /help. KeyboardInterrupt
    on the first line propagates to the caller; on a continuation line it
    discards the draft and returns "".
    """
    try:
        first = input_fn(PROMPT)
    except EOFError:
        return None
    if not first.strip():
        return ""
    if first.strip() == "/":
        return "/help"
    lines = first.split("\n")
    while True:
        if not _fence_open(lines):
            if not lines[-1].endswith("\\"):
                break
            lines[-1] = lines[-1][:-1]
        try:
            more = input_fn(CONTINUATION_PROMPT)
        except EOFError:
            break
        except KeyboardInterrupt:
            print("\n  (input discarded)")
            return ""
        lines.extend(more.split("\n"))
    return "\n".join(lines).rstrip("\n")


__all__ = [
    "COMMAND_ALIASES", "CONTINUATION_PROMPT", "LineHistory", "PROMPT", "SLASH_COMMANDS",
    "SLASH_COMMAND_HELP", "canonical_command", "collect_message", "format_slash_help",
    "setup_readline",
]
