#!/usr/bin/env python
# -*- coding: UTF-8 -*-

"""
游戏解说脚本生成
使用自定义 Prompt 生成游戏解说脚本
"""

import asyncio
import json
import os
import time
import traceback

import streamlit as st
from loguru import logger

from app.config import config
from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService
from app.utils import utils


def _normalize_progress_value(progress: float | int) -> int:
    """Normalize mixed progress inputs to Streamlit's 0-100 integer range."""
    try:
        value = float(progress)
    except (TypeError, ValueError):
        return 0

    if 0.0 <= value <= 1.0:
        value *= 100

    return max(0, min(100, int(round(value))))


def generate_script_gaming(params):
    """
    生成游戏解说脚本。
    使用用户自定义 Prompt 生成游戏解说
    """
    progress_bar = st.progress(0)
    status_text = st.empty()

    def update_progress(progress: float, message: str = ""):
        normalized_progress = _normalize_progress_value(progress)
        progress_bar.progress(normalized_progress)
        if message:
            status_text.text(f"🎮 {message}")
        else:
            status_text.text(f"📊 进度: {normalized_progress}%")

    try:
        with st.spinner("正在生成游戏解说脚本..."):
            if not params.video_origin_path:
                st.error("请先选择视频文件")
                return

            # 获取视觉模型配置
            vision_llm_provider = (
                st.session_state.get("vision_llm_provider") or config.app.get("vision_llm_provider", "gemini")
            ).lower()
            vision_api_key = (
                st.session_state.get(f"vision_{vision_llm_provider}_api_key")
                or config.app.get(f"vision_{vision_llm_provider}_api_key")
            )
            vision_model = (
                st.session_state.get(f"vision_{vision_llm_provider}_model_name")
                or config.app.get(f"vision_{vision_llm_provider}_model_name")
            )
            vision_base_url = (
                st.session_state.get(f"vision_{vision_llm_provider}_base_url")
                or config.app.get(f"vision_{vision_llm_provider}_base_url", "")
            )
            if not vision_api_key or not vision_model:
                raise ValueError(
                    f"未配置 {vision_llm_provider} 的 API Key 或模型名称。"
                    f"请在设置页面配置"
                )

            # 获取游戏解说参数
            gaming_custom_prompt = st.session_state.get('gaming_custom_prompt', '')
            gaming_video_theme = st.session_state.get('gaming_video_theme', '游戏精彩片段')
            frame_interval = st.session_state.get('gaming_frame_interval', 3)
            batch_size = st.session_state.get('gaming_batch_size', 10)
            vision_max_concurrency = st.session_state.get("vision_max_concurrency", 2)

            if not gaming_custom_prompt:
                st.error("请输入自定义 Prompt")
                return

            update_progress(10, "正在生成游戏解说脚本...")

            # 使用 DocumentaryFrameAnalysisService 生成脚本
            service = DocumentaryFrameAnalysisService()
            script_items = asyncio.run(
                service.generate_documentary_script(
                    video_path=params.video_origin_path,
                    video_theme=gaming_video_theme,
                    custom_prompt=gaming_custom_prompt,
                    frame_interval_input=frame_interval,
                    vision_batch_size=batch_size,
                    vision_llm_provider=vision_llm_provider,
                    progress_callback=update_progress,
                    vision_api_key=vision_api_key,
                    vision_model_name=vision_model,
                    vision_base_url=vision_base_url,
                    max_concurrency=vision_max_concurrency,
                    enable_checkpoint=True,
                )
            )

            logger.info(f"游戏解说脚本生成完成，共 {len(script_items)} 个片段")

            # 保存到 session state
            st.session_state["video_clip_json"] = script_items

            # 保存到文件
            task_id = f"gaming_{int(time.time())}"
            task_dir = utils.task_dir(task_id)
            os.makedirs(task_dir, exist_ok=True)
            script_path = os.path.join(task_dir, "script.json")
            with open(script_path, 'w', encoding='utf-8') as f:
                json.dump(script_items, f, ensure_ascii=False, indent=2)
            st.session_state['video_clip_json_path'] = script_path

            update_progress(100, "🎉 游戏解说脚本生成完成！")
            st.success(f"✅ 游戏解说脚本生成成功！共 {len(script_items)} 个片段")
            st.rerun()

    except Exception as err:
        st.error(f"❌ 生成过程中发生错误: {str(err)}")
        logger.exception(f"生成游戏解说脚本时发生错误\n{traceback.format_exc()}")
    finally:
        time.sleep(2)
        progress_bar.empty()
        status_text.empty()