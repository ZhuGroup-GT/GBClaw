"""Fixed project lookup keys (run ID syntax lives in core.identifiers)."""
from __future__ import annotations

# Fixed lookup keys of the approved and approval-pending project plan pointers.
ACTIVE_PLAN_LOOKUP_KEY = "project:active_plan"
PENDING_PLAN_LOOKUP_KEY = "project:pending_plan"
PLAN_POINTER_LOOKUP_KEYS = (ACTIVE_PLAN_LOOKUP_KEY, PENDING_PLAN_LOOKUP_KEY)
