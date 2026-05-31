"""
Kaggle 视觉模型提供商实现

历史兼容 provider。

新的推荐链路是完整视频离线任务：
1. NarratoAI 导出完整视频任务包
2. Kaggle GPU 抽取关键帧并运行 Qwen2-VL/Qwen2.5-VL
3. 导入 analysis_result.json，继续本地文案和剪辑流程

本类保留给仍在使用 OpenAI 兼容远端端点的旧配置。
"""

from loguru import logger

from .openai_compatible_provider import OpenAICompatibleVisionProvider


class KaggleVisionProvider(OpenAICompatibleVisionProvider):
    """
    Kaggle 视觉模型提供商。

    与 OpenAI 兼容接口完全相同（发送 base64 图片），
    只是 provider_name 和默认配置不同。
    """

    @property
    def provider_name(self) -> str:
        return "kaggle"

    @property
    def supported_models(self) -> list:
        return [
            "Qwen/Qwen2-VL-7B-Instruct-GPTQ-Int4",
            "Qwen/Qwen2.5-VL-32B-Instruct-GPTQ-Int4",
            "Qwen/Qwen2.5-VL-72B-Instruct-GPTQ",
            "microsoft/PegASUS-Diagram-32B-GPTQ-Int4",
        ]

    def _validate_config(self):
        # Kaggle notebook 无需 API key
        if not self.model_name:
            from .exceptions import ConfigurationError

            raise ConfigurationError("Kaggle provider 需要填写模型名称", "model_name")

    def _initialize(self):
        logger.info(f"Kaggle 视觉模型提供商已初始化，模型: {self.model_name}")


__all__ = ["KaggleVisionProvider"]
