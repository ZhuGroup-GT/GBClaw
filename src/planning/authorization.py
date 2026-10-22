"""Conservative explicit plan decisions, never keyword substring authorization."""
from __future__ import annotations

import re

_DRAFT_ID = re.compile(r"\bdraft-[a-z0-9]+\b", re.IGNORECASE)
_APPROVE = re.compile(
    r"(?:please\s+)?(?:i\s+)?(?:explicitly\s+)?approve(?:\s+(?:this|the))?"
    r"(?:\s+(?:project\s+)?plan(?:\s+draft)?)?"
    r"|(?:i\s+)?agree(?:\s+and\s+proceed)?"
    r"|(?:approved|proceed|execute)(?:\s+(?:with\s+)?(?:the\s+)?(?:approved\s+)?plan)?"
    r"|(?:我|我们)?(?:明确)?(?:同意|批准)(?:执行)?(?:(?:这个|该)?(?:计划|方案))?"
    r"|(?:确认|可以|开始|按计划)执行(?:这个计划)?|执行这个计划",
    re.IGNORECASE,
)
_REJECT = re.compile(
    r"(?:please\s+)?(?:i\s+)?(?:reject|disagree)(?:\s+(?:with\s+)?(?:this|the))?"
    r"(?:\s+(?:plan|draft))?"
    r"|(?:do not|don't|not)\s+approve(?:\s+(?:this|the))?(?:\s+plan)?"
    r"|(?:我|我们)?(?:不同意|不批准|拒绝)(?:(?:这个|该)?(?:计划|方案))?",
    re.IGNORECASE,
)


def explicit_plan_decision(text: str, draft_id: str, *, approve: bool) -> bool:
    normalized = " ".join(text.casefold().split())
    if not normalized or len(normalized) > 1000:
        return False
    mentioned = set(_DRAFT_ID.findall(normalized))
    if mentioned and mentioned != {draft_id.casefold()}:
        return False
    normalized = _DRAFT_ID.sub("", normalized).strip(" \t.!。！:：")
    parts = [part.strip() for part in re.split(r"[,，;；、]", normalized)]
    pattern = _APPROVE if approve else _REJECT
    return bool(parts) and all(part and pattern.fullmatch(part) for part in parts)
