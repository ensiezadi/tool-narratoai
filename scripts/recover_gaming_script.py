#!/usr/bin/env python3
# -*- coding: UTF-8 -*-
"""
快速从已有的 frame_analysis artifact 生成游戏解说脚本
用于断点后续——当 vision 分析已完成（63 batches 成功）但文案生成中断时
"""

import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add project to path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.generate_narration_script import generate_narration, parse_frame_analysis_to_markdown


def generate_from_artifact(artifact_path: str, output_path: str,
                          video_theme: str = "双影奇境 硬核攻略",
                          custom_prompt: str = ""):
    """
    从已有的 frame_analysis artifact 生成解说脚本

    Args:
        artifact_path: frame_analysis JSON 文件路径
        output_path: 输出 script.json 路径
        video_theme: 视频主题
        custom_prompt: 自定义创作要求
    """
    with open(artifact_path, 'r', encoding='utf-8') as f:
        artifact = json.load(f)

    batches = artifact.get('batches', [])
    success_batches = [b for b in batches if b.get('status') == 'success']

    print(f"Total batches: {len(batches)}, Success: {len(success_batches)}")

    # Build markdown from successful batches
    lines = ["# 视频帧分析结果\n"]
    lines.append(f"视频主题：{video_theme}\n")

    for b in success_batches:
        time_range = b.get('time_range', '')
        obs_list = b.get('frame_observations', [])
        summary = b.get('overall_activity_summary', '')

        lines.append(f"\n## 时间段: {time_range}\n")
        for obs in obs_list:
            ts = obs.get('timestamp', '')
            text = obs.get('observation', '')
            if ts:
                lines.append(f"- [{ts}] {text}\n")
            else:
                lines.append(f"- {text}\n")
        if summary:
            lines.append(f"**总述**: {summary}\n")

    markdown_output = ''.join(lines)

    # Build narration input
    context_lines = [f"视频主题：{video_theme}"]
    if custom_prompt:
        context_lines.append(f"补充创作要求：{custom_prompt}")
    context_block = '\n'.join(context_lines)
    narration_input = f"{markdown_output.rstrip()}\n\n## 创作上下文\n{context_block}\n"

    # Generate narration
    print("Generating narration...")
    text_api_key = os.getenv("NARRATOAI_TEXT_OPENAI_API_KEY", "")
    text_base_url = os.getenv("NARRATOAI_TEXT_OPENAI_BASE_URL", "https://api.minimaxi.com/v1")
    text_model = os.getenv("NARRATOAI_TEXT_OPENAI_MODEL", "MiniMax-M2.7")
    if not text_api_key:
        raise RuntimeError("Missing NARRATOAI_TEXT_OPENAI_API_KEY")

    narration_raw = generate_narration(
        narration_input,
        text_api_key,
        base_url=text_base_url,
        model=text_model,
    )

    print(f"Narration raw length: {len(narration_raw)} chars")

    # Parse narration JSON
    narration_items = _parse_narration_items(narration_raw)
    print(f"Parsed narration items: {len(narration_items)}")

    # Build video_clip_json
    clips = []
    for i, item in enumerate(narration_items):
        if i >= len(success_batches):
            break
        batch = success_batches[i]
        time_range = batch.get('time_range', '')

        # Build picture text from observations
        obs_list = batch.get('frame_observations', [])
        obs_text = ' | '.join([o.get('observation', '')[:50] for o in obs_list[:3]])

        clips.append({
            'timestamp': time_range,
            'picture': obs_text,
            'narration': item.get('narration', ''),
            'OST': 2,
        })

    # Save
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(clips, f, ensure_ascii=False, indent=2)

    print(f"Saved {len(clips)} clips to {output_path}")
    return clips


def _repair_narration_payload(narration_raw: str):
    """修复 narration JSON 解析"""
    import re
    def load_json_candidate(payload: str):
        try:
            parsed = json.loads(payload)
            return parsed if isinstance(parsed, dict) else None
        except Exception:
            return None

    cleaned = (narration_raw or '').strip()
    if not cleaned:
        return None

    candidates = [cleaned]
    candidates.append(cleaned.replace('{{', '{').replace('}}', '}'))

    json_block = re.search(r'```json\s*(.*?)\s*```', cleaned, re.DOTALL)
    if json_block:
        candidates.append(json_block.group(1).strip())

    start = cleaned.find('{')
    end = cleaned.rfind('}')
    if start >= 0 and end > start:
        candidates.append(cleaned[start:end+1])

    for candidate in candidates:
        parsed = load_json_candidate(candidate)
        if parsed is not None:
            return parsed
    return None


def _parse_narration_items(narration_raw: str):
    """解析 narration JSON 为 items 列表"""
    parsed = _repair_narration_payload(narration_raw)
    if not parsed:
        print(f"Failed to parse narration JSON, raw length: {len(narration_raw)}")
        print(f"Raw preview: {narration_raw[:300]}")
        return []

    items = []
    if isinstance(parsed, dict):
        raw_items = parsed.get('items')
        if isinstance(raw_items, list):
            items = [item for item in raw_items if isinstance(item, dict)]

    if not items:
        print(f"No items found in parsed JSON, keys: {list(parsed.keys()) if isinstance(parsed, dict) else type(parsed)}")
    return items


if __name__ == '__main__':
    artifact_path = '/Users/ezadiensi/hermes-agent/ensiezadi/NarratoAI/storage/temp/analysis/frame_analysis_20260514_193113.json'
    output_dir = '/Users/ezadiensi/hermes-agent/ensiezadi/NarratoAI/storage/tasks/gaming_recovered'
    os.makedirs(output_dir, exist_ok=True)
    output_path = os.path.join(output_dir, 'script.json')

    clips = generate_from_artifact(
        artifact_path=artifact_path,
        output_path=output_path,
        video_theme='双影奇境 硬核攻略',
        custom_prompt='硬核攻略风格，专业冷静，干练，多用游戏术语',
    )
    print(f"\n✅ Done! Generated {len(clips)} clips")
