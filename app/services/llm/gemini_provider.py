"""
Google Gemini 原生视觉模型提供商
"""

from typing import List, Dict, Any, Union
from pathlib import Path
import PIL.Image
from loguru import logger

from .base import VisionModelProvider
from .exceptions import APICallError, AuthenticationError, RateLimitError, ContentFilterError


class GeminiVisionProvider(VisionModelProvider):
    """Google Gemini 原生视觉模型提供商"""

    @property
    def provider_name(self) -> str:
        return "gemini"

    @property
    def supported_models(self) -> List[str]:
        return [
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "gemini-2.0-flash",
            "gemini-2.0-flash-lite",
            "gemini-3-flash",
            "gemini-3-flash-lite",
            "gemini-3-pro",
        ]

    def _initialize(self):
        try:
            import google.genai as genai
            self._genai = genai
        except ImportError:
            raise ImportError("请安装 google-genai: pip install google-genai")

    def _build_client(self):
        return self._genai.Client(api_key=self.api_key)

    async def _make_api_call(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Gemini 原生提供商不使用此方法，仅为满足抽象基类要求"""
        raise NotImplementedError("Gemini 原生提供商不支持文本API调用")

    async def analyze_images(
        self,
        images: List[Union[str, Path, PIL.Image.Image]],
        prompt: str,
        batch_size: int = 10,
        max_concurrency: int = 1,
        **kwargs,
    ) -> List[str]:
        logger.info(f"开始使用 Gemini ({self.model_name}) 分析 {len(images)} 张图片")

        processed_images = self._prepare_images(images)
        if not processed_images:
            return []

        results = []
        for i in range(0, len(processed_images), batch_size):
            batch = processed_images[i:i + batch_size]
            result = await self._analyze_batch(batch, prompt, **kwargs)
            results.append(result)

        return results

    async def _analyze_batch(
        self, batch: List[PIL.Image.Image], prompt: str, **kwargs
    ) -> str:
        client = self._build_client()

        contents = [prompt]
        for img in batch:
            contents.append(img)

        try:
            response = client.models.generate_content(
                model=self.model_name,
                contents=contents,
            )
            return response.text if hasattr(response, 'text') else str(response)
        except Exception as e:
            logger.error(f"Gemini API 调用失败: {str(e)}")
            raise APICallError(f"Gemini API 调用失败: {str(e)}")

    def test_connection(self) -> tuple:
        """测试 Gemini API 连接

        Returns:
            tuple: (success: bool, message: str)
        """
        try:
            client = self._build_client()
            response = client.models.generate_content(
                model=self.model_name,
                contents=["Hello"],
            )
            if hasattr(response, 'text') and response.text:
                return True, f"Gemini 连接成功！模型: {self.model_name}"
            return False, "Gemini 返回空响应"
        except Exception as e:
            return False, f"Gemini 连接失败: {str(e)}"
