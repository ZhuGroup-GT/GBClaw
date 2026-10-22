"""Load LLM API settings from the project `.key` file."""

from __future__ import annotations

import logging
from configparser import ConfigParser
from dataclasses import dataclass
from pathlib import Path

from core.paths import REPO_ROOT
from core.settings.env import get_env_bool

LOGGER = logging.getLogger(__name__)

TIER_NAMES = ("premium", "medium", "light")
SETTINGS_SECTION = "settings"
DEFAULT_MODEL_SECTION = "default"
MODEL_ROUTING_SECTION = "model_routing"
OPENAI_API_MODES = ("chat_completions", "responses")


THINKING_MODES = ("enabled", "disabled")
REASONING_EFFORTS = ("low", "medium", "high", "xhigh", "max")
DEFAULT_MAX_TOKENS_WITHOUT_THINKING = 8192
DEFAULT_MAX_TOKENS_WITH_THINKING = 32768
# Output tokens a host-side summary or extraction call may spend on reasoning
# when its model cannot turn reasoning off; see ``auxiliary_endpoint``.
DISABLED_REASONING_ALLOWANCE_TOKENS = 4096
DEFAULT_MAX_INPUT_TOKENS = 256 * 1024
# Soft floor: explicit max_tokens below this with thinking enabled is unsafe.
MIN_RECOMMENDED_MAX_TOKENS_WITH_THINKING = 16_384

# Canonical .key keys first; later names are accepted aliases if renamed upstream.
_THINKING_KEY_ALIASES = ("thinking", "thinking_mode", "enable_thinking")
_REASONING_EFFORT_KEY_ALIASES = ("reasoning_effort", "effort", "reasoning")


def _parser_get_aliased(
    parser: ConfigParser,
    section: str,
    aliases: tuple[str, ...],
    *,
    fallback: str = "",
) -> str:
    """Read the first present alias from *section* (empty string if none set)."""
    for key in aliases:
        if parser.has_option(section, key):
            return parser.get(section, key, fallback=fallback).strip()
    return fallback.strip()


def _normalize_thinking_value(raw: str) -> str:
    """Map free-form .key thinking values onto enabled|disabled."""
    value = raw.strip().lower()
    if not value:
        return "disabled"
    if value in THINKING_MODES:
        return value
    if value in {"1", "true", "yes", "on"}:
        return "enabled"
    if value in {"0", "false", "no", "off"}:
        return "disabled"
    raise ValueError(
        f"thinking must be one of: {', '.join(THINKING_MODES)} "
        f"(or true/false); got '{raw}'"
    )



def default_max_input_tokens() -> int:
    """Input window assumed when neither the endpoint nor the model profile states one."""
    return DEFAULT_MAX_INPUT_TOKENS * (4 if get_env_bool("GBCLAW_CONTEXT_1M") else 1)

@dataclass(frozen=True)
class LLMEndpoint:
    provider: str
    api_key: str
    base_url: str
    model: str
    temperature: float
    max_tokens: int | None
    max_input_tokens: int | None
    timeout: int
    thinking: str
    reasoning_effort: str | None
    api_mode: str

    def resolved_max_tokens(self) -> int:
        """Return explicit max_tokens from .key, or a default tied to thinking mode."""
        if self.max_tokens is not None:
            return self.max_tokens
        if self.thinking == "enabled":
            return DEFAULT_MAX_TOKENS_WITH_THINKING
        return DEFAULT_MAX_TOKENS_WITHOUT_THINKING


@dataclass(frozen=True)
class LLMConfig:
    use_tier_routing: bool
    model_routing: dict[str, str]
    default: LLMEndpoint | None = None
    premium: LLMEndpoint | None = None
    medium: LLMEndpoint | None = None
    light: LLMEndpoint | None = None

    def resolve(self, tier: str | None = None) -> LLMEndpoint:
        if not self.use_tier_routing:
            if self.default is None:
                raise ValueError(
                    f"Missing [{DEFAULT_MODEL_SECTION}] section in LLM config "
                    "(required when use_tier_routing = false)"
                )
            return self.default
        if tier is None:
            raise ValueError(
                "Tier is required when use_tier_routing = true; use ModelRouter.for_role() "
                "for role-based fallback."
            )
        name = tier.lower()
        if name not in TIER_NAMES:
            raise ValueError(f"Unknown LLM tier '{tier}'. Expected one of: {', '.join(TIER_NAMES)}")
        endpoint = getattr(self, name)
        if endpoint is None:
            raise ValueError(
                f"Missing [{name}] section in LLM config (required when use_tier_routing = true)"
            )
        return endpoint


@dataclass(frozen=True)
class ModelRouter:
    config: LLMConfig

    def for_role(self, role: str | None = None) -> LLMEndpoint:
        """Resolve the endpoint for an agent role, falling back to the configured default."""
        if not self.config.use_tier_routing:
            return self.config.resolve()

        normalized = (role or "").strip().lower()
        tier = (
            self.config.model_routing.get(normalized)
            or self.config.model_routing.get("default")
        )
        return self.config.resolve(tier)


def _read_section(parser: ConfigParser, section: str) -> LLMEndpoint:
    if not parser.has_section(section):
        raise ValueError(f"Missing [{section}] section in LLM config")

    required = ("provider", "api_key", "base_url", "model")
    missing = [key for key in required if not parser.get(section, key, fallback="").strip()]
    if missing:
        raise ValueError(f"[{section}] is missing required fields: {', '.join(missing)}")

    thinking_raw = _parser_get_aliased(
        parser, section, _THINKING_KEY_ALIASES, fallback="disabled"
    )
    thinking = _normalize_thinking_value(thinking_raw)

    reasoning_effort = _parser_get_aliased(
        parser, section, _REASONING_EFFORT_KEY_ALIASES, fallback=""
    ).lower() or None
    if reasoning_effort and reasoning_effort not in REASONING_EFFORTS:
        raise ValueError(
            f"[{section}] reasoning_effort must be one of: {', '.join(REASONING_EFFORTS)} "
            f"(got '{reasoning_effort}')"
        )

    api_mode = parser.get(
        section, "api_mode", fallback="chat_completions"
    ).strip().lower().replace("-", "_")
    if api_mode == "chat":
        api_mode = "chat_completions"
    if api_mode not in OPENAI_API_MODES:
        raise ValueError(
            f"[{section}] api_mode must be one of: {', '.join(OPENAI_API_MODES)} "
            f"(got '{api_mode}')"
        )

    max_tokens_raw = parser.get(section, "max_tokens", fallback="").strip()
    max_tokens = int(max_tokens_raw) if max_tokens_raw else None
    if max_tokens is not None and max_tokens <= 0:
        raise ValueError(f"[{section}] max_tokens must be positive")
    max_input_tokens_raw = parser.get(
        section, "max_input_tokens", fallback=""
    ).strip()
    max_input_tokens = (
        int(max_input_tokens_raw) if max_input_tokens_raw else default_max_input_tokens()
    )
    if max_input_tokens <= 0:
        raise ValueError(f"[{section}] max_input_tokens must be positive")

    if (
        thinking == "enabled"
        and max_tokens is not None
        and max_tokens < MIN_RECOMMENDED_MAX_TOKENS_WITH_THINKING
    ):
        LOGGER.warning(
            "[%s] thinking=enabled with max_tokens=%s is likely to burn the "
            "entire output budget on reasoning (recommended: omit max_tokens "
            "for default %s, or set >= %s; prefer reasoning_effort=low).",
            section,
            max_tokens,
            DEFAULT_MAX_TOKENS_WITH_THINKING,
            MIN_RECOMMENDED_MAX_TOKENS_WITH_THINKING,
        )

    return LLMEndpoint(
        provider=parser.get(section, "provider").strip(),
        api_key=parser.get(section, "api_key").strip(),
        base_url=parser.get(section, "base_url").strip(),
        model=parser.get(section, "model").strip(),
        temperature=parser.getfloat(section, "temperature", fallback=0.3),
        max_tokens=max_tokens,
        max_input_tokens=max_input_tokens,
        timeout=parser.getint(section, "timeout", fallback=120),
        thinking=thinking,
        reasoning_effort=reasoning_effort,
        api_mode=api_mode,
    )


def _read_routing(parser: ConfigParser) -> bool:
    """Read routing flags from [settings], with legacy fallback to [default]."""
    if parser.has_section(SETTINGS_SECTION):
        section = SETTINGS_SECTION
    elif parser.has_section(DEFAULT_MODEL_SECTION):
        section = DEFAULT_MODEL_SECTION
    else:
        raise ValueError(f"LLM config must contain a [{SETTINGS_SECTION}] section")

    use_tier_routing = parser.get(section, "use_tier_routing", fallback="false").strip().lower()
    return use_tier_routing in {"1", "true", "yes", "on"}


def _read_model_routing(parser: ConfigParser) -> dict[str, str]:
    if not parser.has_section(MODEL_ROUTING_SECTION):
        raise ValueError(
            f"[{MODEL_ROUTING_SECTION}] section is required when use_tier_routing = true"
        )

    routing: dict[str, str] = {}
    for role, tier in parser.items(MODEL_ROUTING_SECTION):
        normalized_role = role.strip().lower()
        normalized_tier = tier.strip().lower()
        if not normalized_role or not normalized_tier:
            continue
        if normalized_tier not in TIER_NAMES:
            raise ValueError(
                f"[{MODEL_ROUTING_SECTION}] {normalized_role} must be one of: "
                f"{', '.join(TIER_NAMES)} (got '{normalized_tier}')"
            )
        routing[normalized_role] = normalized_tier
    if "default" not in routing:
        raise ValueError(f"[{MODEL_ROUTING_SECTION}] default is required")
    return routing


def load_llm_config(key_path: Path | str | None = None) -> LLMConfig:
    path = Path(key_path) if key_path is not None else REPO_ROOT / ".key"
    if not path.is_file():
        raise FileNotFoundError(f"LLM config not found: {path}")

    parser = ConfigParser()
    parser.read(path)

    tier_routing_on = _read_routing(parser)

    if tier_routing_on:
        model_routing = _read_model_routing(parser)
        return LLMConfig(
            use_tier_routing=True,
            model_routing=model_routing,
            premium=_read_section(parser, "premium"),
            medium=_read_section(parser, "medium"),
            light=_read_section(parser, "light"),
        )

    if not parser.has_section(DEFAULT_MODEL_SECTION):
        raise ValueError(
            f"Missing [{DEFAULT_MODEL_SECTION}] section in LLM config "
            "(required when use_tier_routing = false)"
        )

    return LLMConfig(
        use_tier_routing=False,
        model_routing={},
        default=_read_section(parser, DEFAULT_MODEL_SECTION),
    )
