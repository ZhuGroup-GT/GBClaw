"""Reject invented, withdrawn and source-misclassified summary citations."""
import re
from project_memory.storage.repository import MemoryRepository
from .navigation import strip_history_navigation
_FACT_REFERENCE_RE = re.compile(r"\bfact\s*:\s*`([^`]+)`", re.IGNORECASE)

def _validate_fact_references(
    summary: str,
    memory: MemoryRepository,
) -> None:
    # Host historical-navigation excerpts are discovery data, never active
    # claims. Their source archive may legitimately mention withdrawn facts.
    summary = strip_history_navigation(summary)
    cited = set(_FACT_REFERENCE_RE.findall(summary))
    known = memory.existing_fact_lookup_keys(cited)
    unknown = sorted(cited - known)
    if unknown:
        raise ValueError(
            "semantic summary cited fact lookup keys absent from active SQLite facts: "
            + ", ".join(unknown)
        )
    headings = {
        "## Tool results": "tool_result",
        "## Conversation-derived context": "conversation",
        "## Agent reports": "agent_report",
        "## Workflow state": "workflow_state",
    }
    sections: dict[str, list[str]] = {}
    current = None
    for line in summary.splitlines():
        if line.strip() in headings:
            current = headings[line.strip()]
            if current in sections:
                raise ValueError("semantic summary repeats an evidence-source section")
            sections[current] = []
        elif current is not None:
            sections[current].append(line)
        elif _FACT_REFERENCE_RE.search(line):
            raise ValueError("semantic summary fact citations require an evidence-source section")
    if set(sections) != set(headings.values()):
        raise ValueError("semantic summary requires all four evidence-source sections")
    section_of = {source_type: heading for heading, source_type in headings.items()}
    for source_type, lines in sections.items():
        for key in set(_FACT_REFERENCE_RE.findall("\n".join(lines))):
            facts = memory.query_facts(lookup_key=key, include_retracted=True, limit=100)
            if any(fact.status.value == "retracted" for fact in facts):
                raise ValueError(f"semantic summary cites a retracted fact: {key!r}")
            if not facts or any(fact.source_type.value != source_type for fact in facts):
                recorded = sorted({fact.source_type.value for fact in facts})
                raise ValueError(
                    f"semantic summary source mismatch for {key!r}: cited under "
                    f"'{section_of[source_type]}' but recorded as {'/'.join(recorded) or 'nothing'}; "
                    f"cite it only under {' or '.join(repr(section_of[v]) for v in recorded) or 'no section'}"
                )


