"""Per-turn planning policy shared by API, CLI, and agent middleware."""

from __future__ import annotations

from enum import Enum


class PlanningMode(str, Enum):
    """Control whether durable project planning is automatic, required, or skipped."""

    AUTO = "auto"
    FORCE = "force"
    SKIP = "skip"


def coerce_planning_mode(value: PlanningMode | str | None) -> PlanningMode:
    """Return a validated planning mode, defaulting missing values to ``auto``."""

    if value is None or value == "":
        return PlanningMode.AUTO
    if isinstance(value, PlanningMode):
        return value
    return PlanningMode(str(value).strip().lower())


__all__ = ["PlanningMode", "coerce_planning_mode"]
