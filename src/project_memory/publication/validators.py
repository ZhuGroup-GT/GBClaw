"""Built-in validators referenced by trusted domain declarations."""

from __future__ import annotations


def validate_coding_publication(run, result: dict, facts: list) -> None:
    """Coding evidence is publishable only after recorded successful validation."""
    validation = result.get("validation")
    if not isinstance(validation, dict) or validation.get("passed") is not True:
        raise ValueError("Coding memory requires recorded validation.passed=true")
