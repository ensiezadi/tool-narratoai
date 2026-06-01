"""Shared config defaults used by both bootstrap and WebUI fallbacks."""

from __future__ import annotations

DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_OPENAI_COMPATIBLE_PROVIDER = "openai"

DEFAULT_VISION_LLM_PROVIDER = DEFAULT_OPENAI_COMPATIBLE_PROVIDER
DEFAULT_VISION_OPENAI_MODEL_NAME = "Qwen/Qwen3.5-122B-A10B"

DEFAULT_TEXT_LLM_PROVIDER = DEFAULT_OPENAI_COMPATIBLE_PROVIDER
DEFAULT_TEXT_OPENAI_MODEL_NAME = "Pro/zai-org/GLM-5"

DEFAULT_AGENT_GATEWAY_BASE_URL = "http://127.0.0.1:23333/v1"
DEFAULT_AGENT_GATEWAY_MODEL_NAME = "gpt-4o"
DEFAULT_HERMES_CLI_COMMAND = "hermes"
DEFAULT_AGENT_WIKI_PATH = "docs/wiki/narratoai-agent-notes.md"
DEFAULT_AGENT_ROLES = (
    "剪辑导演: 负责判断节奏、爆点密度和成片结构。\n"
    "视觉审片: 负责核对画面证据、误识别和低价值片段。\n"
    "文案润色: 负责把解说改成双影奇境游戏解说口吻。"
)
DEFAULT_AGENT_COLLABORATION_RULES = (
    "省 token 协作规则:\n"
    "1. 简单执行类任务直接给结论和路径，不复述过程。\n"
    "2. 任务超过 3 步、涉及外部依赖或会改多个模块时，先给 3-5 条计划。\n"
    "3. Hermes 反馈默认控制在 5 条以内；报告类先给结论，再按需展开。\n"
    "4. 只有选型决策、踩坑修复、接口约定、稳定工作流才写入 wiki。\n"
    "5. 密钥、Token、一次性日志、无复用价值的寒暄不写入 wiki。"
)

DEFAULT_LLM_APP_CONFIG = {
    "vision_llm_provider": DEFAULT_VISION_LLM_PROVIDER,
    "vision_openai_model_name": DEFAULT_VISION_OPENAI_MODEL_NAME,
    "vision_openai_api_key": "",
    "vision_openai_base_url": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    "text_llm_provider": DEFAULT_TEXT_LLM_PROVIDER,
    "text_openai_model_name": DEFAULT_TEXT_OPENAI_MODEL_NAME,
    "text_openai_api_key": "",
    "text_openai_base_url": DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    "agent_gateway_enabled": False,
    "agent_execution_mode": "hermes_cli",
    "agent_gateway_type": "hermes_cli",
    "agent_gateway_base_url": DEFAULT_AGENT_GATEWAY_BASE_URL,
    "agent_gateway_api_key": "",
    "agent_gateway_model_name": DEFAULT_AGENT_GATEWAY_MODEL_NAME,
    "hermes_cli_command": DEFAULT_HERMES_CLI_COMMAND,
    "hermes_cli_workdir": "",
    "agent_multi_agent_enabled": False,
    "agent_roles": DEFAULT_AGENT_ROLES,
    "agent_collaboration_rules": DEFAULT_AGENT_COLLABORATION_RULES,
    "agent_wiki_enabled": True,
    "agent_wiki_path": DEFAULT_AGENT_WIKI_PATH,
    "agent_wiki_min_chars": 120,
}


def build_default_app_config(app_config: dict | None = None) -> dict:
    """Force the shared LLM defaults into a fresh app config."""
    merged = dict(app_config or {})
    merged.update(DEFAULT_LLM_APP_CONFIG)
    return merged


def merge_missing_app_defaults(app_config: dict | None = None) -> dict:
    """Backfill missing keys without overriding saved user values."""
    merged = dict(app_config or {})
    for key, value in DEFAULT_LLM_APP_CONFIG.items():
        merged.setdefault(key, value)
    return merged


def normalize_openai_compatible_model_name(
    model_name: str,
    provider: str = DEFAULT_OPENAI_COMPATIBLE_PROVIDER,
) -> str:
    """Strip only the internal OpenAI-compatible provider prefix if present."""
    normalized = (model_name or "").strip()
    provider_prefix = f"{provider}/"
    if normalized.lower().startswith(provider_prefix):
        return normalized[len(provider_prefix):]
    return normalized


def get_openai_compatible_ui_values(
    full_model_name: str,
    default_model: str,
    provider: str = DEFAULT_OPENAI_COMPATIBLE_PROVIDER,
) -> tuple[str, str]:
    """Keep the UI provider fixed while preserving the full model identifier."""
    current_model = normalize_openai_compatible_model_name(
        full_model_name or default_model,
        provider=provider,
    )
    return provider, current_model or default_model
