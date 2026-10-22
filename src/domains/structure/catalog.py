"""The Structure worker's default tool set."""

from __future__ import annotations

from domains.structure.csl.tools import STRUCTURE_AGENT_TOOLS
from domains.structure.dichromatic.tools import make_dichromatic_map_tools


def structure_agent_tools() -> list:
    """Tool provider for the Structure worker (see domains/structure/extension.py).

    A project-bound delegate substitutes run-recording CSL tools for these.
    """
    return [*STRUCTURE_AGENT_TOOLS, *make_dichromatic_map_tools()]
