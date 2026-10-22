"""Terminal rendering of chat stream events."""

from __future__ import annotations

import os
import re
import shutil
import sys
import textwrap
import threading
from pathlib import Path

from core.llm.reasoning import withheld_reasoning_note
from core.stream_events import StreamEventType

_DEFAULT_TERM_WIDTH = 100
_MIN_TERM_WIDTH = 40
TOOL_INPUT_LINES = 1
TOOL_OUTPUT_LINES = 3

use_color: bool = "NO_COLOR" not in os.environ and sys.stdout.isatty()

_term_width: int = max(_MIN_TERM_WIDTH, shutil.get_terminal_size((_DEFAULT_TERM_WIDTH, 20)).columns)


def refresh_term_width() -> None:
    global _term_width
    _term_width = max(_MIN_TERM_WIDTH, shutil.get_terminal_size((_DEFAULT_TERM_WIDTH, 20)).columns)


def _ansi(code: str, text: str) -> str:
    return f"\x1b[{code}m{text}\x1b[0m" if use_color and text else text


def _bold(text: str) -> str:
    return _ansi("1", text)


def _dim(text: str) -> str:
    return _ansi("2", text)


def _dim_italic(text: str) -> str:
    return _ansi("2;3", text)


def _cyan(text: str) -> str:
    return _ansi("36", text)


def _red(text: str) -> str:
    return _ansi("31", text)


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _preview(text: object, *, max_lines: int, width: int) -> list[str]:
    """The first non-blank lines of *text*, each cut to *width*, plus a count of the rest."""
    lines = [line for line in str(text or "").splitlines() if line.strip()]
    width = max(width, 12)
    shown = [textwrap.shorten(line, width=width, placeholder="…") for line in lines[:max_lines]]
    if len(lines) > max_lines:
        shown.append(f"… (+{len(lines) - max_lines} lines)")
    return shown or ["(empty)"]


class Renderer:
    """Turn stream events into terminal text, one call per event.

    The renderer tracks whether the cursor is at the start of a line so status
    lines, tool calls, reasoning and the answer never run into one another.
    Worker events (those with ``parent_id``) are indented under their delegate.
    """

    def __init__(self, project_root: Path | None = None) -> None:
        self.project_root = project_root
        self.reset()

    def reset(self) -> None:
        self._at_line_start = True
        self._thinking = False
        self._answer_streamed = False

    def render(self, event: dict) -> str:
        text = self._render(event)
        if text:
            self._at_line_start = text.endswith("\n")
        return text

    def _break(self) -> str:
        return "" if self._at_line_start else "\n"

    @staticmethod
    def _indent(event: dict) -> str:
        return "      " if event.get("parent_id") else "  "

    def _render(self, event: dict) -> str:
        etype = event.get("type", "")
        if etype == StreamEventType.THINKING:
            return self._thinking_text(str(event.get("text", "")), event)
        self._thinking = False
        if etype == StreamEventType.STATUS:
            return self._break() + self._indent(event) + _dim(f"… {event.get('message', '')}") + "\n"
        if etype == StreamEventType.THINKING_WITHHELD:
            note = withheld_reasoning_note(int(event.get("reasoning_tokens") or 0))
            return self._break() + self._indent(event)[2:] + _dim_italic(f"│ ({note})") + "\n"
        if etype == StreamEventType.FINAL_START:
            self._answer_streamed = False
            return self._break() + "\n" + _bold("GBclaw:") + " "
        if etype == StreamEventType.TOKEN:
            text = str(event.get("text", ""))
            if text:
                self._answer_streamed = True
            return text
        if etype == StreamEventType.RESET_STREAM:
            self._answer_streamed = False
            return self._break() + _dim("(regenerating…)") + "\n"
        if etype == StreamEventType.TOOL_START:
            return self._tool_start(event)
        if etype == StreamEventType.TOOL_END:
            return self._tool_end(event)
        if etype == StreamEventType.DONE:
            return self._done(event)
        if etype == StreamEventType.ERROR:
            return self._break() + _red(f"! {event.get('message', '')}") + "\n"
        return ""

    def _thinking_text(self, text: str, event: dict) -> str:
        safe = _strip_ansi(text)
        if not safe:
            return ""
        prefix = self._indent(event)[2:] + _dim_italic("│ ")
        lead = ""
        if not self._thinking:
            self._thinking = True
            lead = self._break() + prefix
        elif self._at_line_start:
            lead = prefix
        trailing = safe.endswith("\n")
        body = safe[:-1] if trailing else safe
        return lead + body.replace("\n", "\n" + prefix) + ("\n" if trailing else "")

    def _tool_start(self, event: dict) -> str:
        label = str(event.get("label") or event.get("name") or "tool")
        if event.get("parent_name"):
            label = f"{event['parent_name']} › {label}"
        indent = self._indent(event)
        width = _term_width - len(indent) - len(label) - 6
        preview = _preview(event.get("input", ""), max_lines=TOOL_INPUT_LINES, width=width)
        return self._break() + indent + _cyan(f"▸ {label}") + "  " + _dim(" ".join(preview)) + "\n"

    def _tool_end(self, event: dict) -> str:
        indent = self._indent(event) + "  "
        lines = _preview(event.get("output", ""), max_lines=TOOL_OUTPUT_LINES, width=_term_width - len(indent) - 2)
        paint = _red if str(event.get("status") or "") == "error" else _dim
        text = "\n".join(indent + ("↳ " if index == 0 else "  ") + line for index, line in enumerate(lines))
        return self._break() + paint(text) + "\n"

    def _done(self, event: dict) -> str:
        out = self._break()
        reply = str(event.get("reply") or "").strip()
        status = str(event.get("status") or "completed")
        if status == "paused":
            out += "\n" + _bold(_red("Paused:")) + " " + reply + "\n"
        elif reply and not self._answer_streamed:
            out += "\n" + _bold("GBclaw:") + " " + reply + "\n"
        images = [path for path in event.get("image_paths") or [] if isinstance(path, str)]
        if images:
            out += "\n" + _dim("  images:") + "\n"
            out += "".join(f"    {self._location(path)}\n" for path in images)
        self._answer_streamed = False
        return out + _dim("[done — saved to project]") + "\n"

    def _location(self, path: str) -> str:
        return str(self.project_root / path) if self.project_root is not None else path


PRINT_LOCK = threading.Lock()


def safe_print(text: str) -> None:
    if not text:
        return
    with PRINT_LOCK:
        try:
            sys.stdout.write(text)
            sys.stdout.flush()
        except (BrokenPipeError, ValueError):
            # The user's terminal may have closed (e.g. tmux detach).
            # Treat as a soft exit.
            raise KeyboardInterrupt


__all__ = ["PRINT_LOCK", "Renderer", "refresh_term_width", "safe_print", "use_color"]
