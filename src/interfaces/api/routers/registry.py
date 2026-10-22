"""Expose registry declarations the Web UI needs to label and stream events."""

from __future__ import annotations

from fastapi import APIRouter

from agents.streaming.events import tool_label
from extensions import TOOL_CAPABILITIES, worker_definitions
from interfaces.api.schemas import RegistryResponse
from interfaces.commands import CHAT_COMMANDS

router = APIRouter(prefix="/api", tags=["registry"])


@router.get("/registry", response_model=RegistryResponse)
async def registry() -> RegistryResponse:
    return RegistryResponse(
        delegate_labels={spec.delegate_tool: tool_label(spec.delegate_tool) for spec in worker_definitions()},
        preserve_final_stream=sorted(
            name for name, capability in TOOL_CAPABILITIES.items() if capability.preserve_final_stream
        ),
        chat_commands=[item.public_record() for item in CHAT_COMMANDS],
    )
