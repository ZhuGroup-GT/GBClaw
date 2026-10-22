"""Agent entry points, loaded lazily to keep domain registration import-safe."""
from importlib import import_module

_EXPORTS = {
    "build_llm": ("core.llm.factory", "build_llm"),
    "build_run_config": ("agents.invoke", "build_run_config"),
    "create_orchestrator_agent": ("agents.orchestrator.graph", "create_orchestrator_agent"),
    "extract_reply": ("agents.invoke", "extract_reply"),
    "invoke_agent": ("agents.invoke", "invoke_agent"),
}


def __getattr__(name):
    if name not in _EXPORTS:
        raise AttributeError(name)
    module, attribute = _EXPORTS[name]
    value = getattr(import_module(module), attribute)
    globals()[name] = value
    return value

__all__ = [
    "build_llm",
    "build_run_config",
    "create_orchestrator_agent",
    "extract_reply",
    "invoke_agent",
]
