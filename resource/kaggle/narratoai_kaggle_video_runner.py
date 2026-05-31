#!/usr/bin/env python3
"""Run NarratoAI video understanding on Kaggle GPU.

The runner reads a full source video from a Kaggle Dataset task package,
extracts coarse and refined keyframes, asks a Qwen2VL/Qwen2.5-VL style model for
editing events, and writes artifacts that NarratoAI can import locally.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import shutil
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

import torch
from PIL import Image


EVENT_TYPES = {
    "death_fail",
    "strong_reaction",
    "coop_command",
    "puzzle_progress",
    "live_interaction",
    "transition",
    "low_value",
}

VIDEO_UNDERSTANDING_PROMPT = """
你是游戏直播短视频剪辑的视觉理解模型。请分析我提供的 {frame_count} 张连续关键帧。

你必须同时完成两件事：
1. 逐帧描述关键视觉信息：角色、动作、场景、失败反馈、跳跃/掉落、UI提示、过场动画。
2. 提取可剪辑事件，而不是只写普通画面描述。

事件类型只能使用：
- death_fail：死亡、掉落、失败、翻车、明显受击或失败反馈
- strong_reaction：主播强反应、吐槽、惊呼、笑点反应
- coop_command：合作指挥、双人配合、走位/跳跃/机关协作
- puzzle_progress：解谜推进、剧情推进、发现关键物体/出口/门/无人机
- live_interaction：弹幕、礼物、才艺、直播任务打断
- transition：场景切换、过场、可作为转场的片段
- low_value：赶路、等待、画面无明显事件

重要规则：
- 不要只因为字幕好笑就选择事件，必须有画面证据、反馈、动作、反应或剧情推进。
- 如果画面没有明显事件，请标为 low_value。
- 推荐剪辑范围需要在动作前后预留 2-5 秒。
- 如果字幕上下文有帮助，可以写入 subtitle_evidence。

请只返回 JSON，不要附加解释文字。JSON 结构必须是：
{{
  "frame_observations": [
    {{"timestamp": "00:00:00,000", "observation": "画面描述"}}
  ],
  "overall_activity_summary": "本批次发生的主要活动",
  "events": [
    {{
      "event_type": "death_fail",
      "time_range": "00:00:00,000-00:00:08,000",
      "score": 8.5,
      "confidence": 0.8,
      "visual_evidence": "为什么这是可剪辑事件",
      "subtitle_evidence": "相关字幕，没有则为空",
      "recommended_clip": {{
        "start": "00:00:00,000",
        "end": "00:00:10,000",
        "reason": "动作前后预留的原因"
      }}
    }}
  ]
}}
""".strip()


def timestamp_from_seconds(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    milliseconds = int(round((seconds - math.floor(seconds)) * 1000))
    total = int(math.floor(seconds))
    if milliseconds >= 1000:
        total += 1
        milliseconds -= 1000
    h = total // 3600
    m = (total % 3600) // 60
    s = total % 60
    return f"{h:02d}:{m:02d}:{s:02d},{milliseconds:03d}"


def seconds_from_timestamp(timestamp: str) -> float:
    text = (timestamp or "").strip()
    if not text:
        return 0.0
    try:
        if "," in text:
            time_part, ms_part = text.split(",", 1)
            milliseconds = int(re.sub(r"\D", "", ms_part)[:3].ljust(3, "0"))
        else:
            time_part = text
            milliseconds = 0
        parts = [int(part) for part in time_part.split(":") if part]
        while len(parts) < 3:
            parts.insert(0, 0)
        hours, minutes, seconds = parts[-3], parts[-2], parts[-1]
        return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000
    except Exception:
        return 0.0


def parse_time_range(time_range: str) -> tuple[float, float]:
    if "-" not in (time_range or ""):
        start = seconds_from_timestamp(time_range)
        return start, start
    start_text, end_text = time_range.split("-", 1)
    return seconds_from_timestamp(start_text), seconds_from_timestamp(end_text)


def keyframe_name(index: int, seconds: float) -> str:
    token = timestamp_from_seconds(seconds).replace(":", "").replace(",", "")
    return f"keyframe_{index:06d}_{token}.jpg"


def extract_keyframes(
    video_path: Path,
    output_dir: Path,
    interval_seconds: float,
    max_analysis_seconds: float | None = None,
    analysis_ranges_seconds: list[list[float]] | None = None,
) -> list[dict[str, Any]]:
    import cv2

    output_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    fps = capture.get(cv2.CAP_PROP_FPS) or 25.0
    total_frames = int(capture.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
    duration = total_frames / fps if fps else 0.0

    frames: list[dict[str, Any]] = []
    index = 0
    ranges = []
    for item in analysis_ranges_seconds or []:
        if isinstance(item, list) and len(item) == 2:
            start, end = max(0.0, float(item[0])), min(duration, float(item[1]))
            if end >= start:
                ranges.append((start, end))
    if not ranges:
        scan_end_seconds = duration
        if max_analysis_seconds is not None and max_analysis_seconds > 0:
            scan_end_seconds = min(duration, max_analysis_seconds)
        ranges = [(0.0, scan_end_seconds)]

    for start_seconds, end_seconds in ranges:
        current_second = start_seconds
        while current_second <= max(end_seconds, 0.0):
            capture.set(cv2.CAP_PROP_POS_MSEC, current_second * 1000)
            ok, frame = capture.read()
            if not ok:
                break
            rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
            image = Image.fromarray(rgb)
            image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
            path = output_dir / keyframe_name(index, current_second)
            image.save(path, format="JPEG", quality=90)
            frames.append(
                {
                    "path": str(path),
                    "timestamp": timestamp_from_seconds(current_second),
                    "seconds": current_second,
                }
            )
            index += 1
            current_second += interval_seconds

    capture.release()
    if not frames:
        raise RuntimeError("No keyframes extracted")
    return frames


def extract_refined_keyframes(
    video_path: Path,
    output_dir: Path,
    start_seconds: float,
    end_seconds: float,
    interval_seconds: float,
) -> list[dict[str, Any]]:
    import cv2

    output_dir.mkdir(parents=True, exist_ok=True)
    capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"Cannot open video: {video_path}")

    frames: list[dict[str, Any]] = []
    current_second = max(0.0, start_seconds)
    index = 0
    while current_second <= end_seconds:
        capture.set(cv2.CAP_PROP_POS_MSEC, current_second * 1000)
        ok, frame = capture.read()
        if not ok:
            break
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((1024, 1024), Image.Resampling.LANCZOS)
        path = output_dir / keyframe_name(index, current_second)
        image.save(path, format="JPEG", quality=90)
        frames.append(
            {
                "path": str(path),
                "timestamp": timestamp_from_seconds(current_second),
                "seconds": current_second,
            }
        )
        index += 1
        current_second += interval_seconds

    capture.release()
    return frames


def chunked(items: list[Any], size: int) -> list[list[Any]]:
    return [items[index : index + size] for index in range(0, len(items), size)]


def strip_code_fence(text: str) -> str:
    cleaned = (text or "").strip()
    cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    return cleaned.strip()


def parse_srt(path: Path | None) -> list[dict[str, Any]]:
    if not path or not path.exists():
        return []
    content = path.read_text(encoding="utf-8", errors="ignore")
    blocks = re.split(r"\n\s*\n", content.strip())
    subtitles = []
    for block in blocks:
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if len(lines) < 2:
            continue
        time_line = next((line for line in lines if "-->" in line), "")
        if not time_line:
            continue
        start_text, end_text = [item.strip() for item in time_line.split("-->", 1)]
        text = " ".join(line for line in lines if "-->" not in line and not line.isdigit())
        subtitles.append(
            {
                "start": seconds_from_timestamp(start_text),
                "end": seconds_from_timestamp(end_text),
                "text": text,
            }
        )
    return subtitles


def subtitles_for_range(subtitles: list[dict[str, Any]], start: float, end: float) -> str:
    texts = []
    for item in subtitles:
        if item["end"] >= start and item["start"] <= end:
            texts.append(item["text"])
    return " / ".join(texts[:8])


def build_prompt(
    *,
    frame_count: int,
    frame_timestamps: list[str] | None = None,
    video_theme: str = "",
    custom_prompt: str = "",
    subtitle_context: str = "",
) -> str:
    prompt = VIDEO_UNDERSTANDING_PROMPT.format(frame_count=frame_count)
    extra_lines = []
    if frame_timestamps:
        extra_lines.append(
            "输入图像按顺序对应的绝对时间戳：" + "、".join(frame_timestamps)
            + "。所有 time_range 和 recommended_clip 必须使用这些时间附近的绝对时间。"
        )
    if video_theme.strip():
        extra_lines.append(f"视频主题：{video_theme.strip()}")
    if custom_prompt.strip():
        extra_lines.append(custom_prompt.strip())
    if subtitle_context.strip():
        extra_lines.append(f"字幕上下文：{subtitle_context.strip()}")
    if not extra_lines:
        return prompt
    return prompt + "\n\n补充分析要求：\n" + "\n".join(f"- {line}" for line in extra_lines)


def load_model(model_name: str):
    from transformers import AutoProcessor

    try:
        from transformers import Qwen2_5_VLForConditionalGeneration

        model_cls = Qwen2_5_VLForConditionalGeneration
    except Exception:
        try:
            from transformers import Qwen2VLForConditionalGeneration

            model_cls = Qwen2VLForConditionalGeneration
        except Exception:
            from transformers import AutoModelForVision2Seq

            model_cls = AutoModelForVision2Seq

    dtype = torch.float16 if torch.cuda.is_available() else torch.float32
    model = model_cls.from_pretrained(
        model_name,
        torch_dtype=dtype,
        device_map="auto",
        trust_remote_code=True,
    ).eval()
    processor = AutoProcessor.from_pretrained(model_name, trust_remote_code=True)
    return model, processor


def analyze_batch(model, processor, frame_batch: list[dict[str, Any]], prompt: str, max_new_tokens: int) -> str:
    images = [Image.open(item["path"]).convert("RGB") for item in frame_batch]
    content = [{"type": "text", "text": prompt}]
    content.extend({"type": "image", "image": image} for image in images)
    messages = [{"role": "user", "content": content}]
    text = processor.apply_chat_template(messages, tokenize=False, add_generation_prompt=True)
    inputs = processor(text=[text], images=images, padding=True, return_tensors="pt").to(model.device)
    with torch.no_grad():
        generated_ids = model.generate(**inputs, max_new_tokens=max_new_tokens, do_sample=False)
    trimmed = [out_ids[len(in_ids) :] for in_ids, out_ids in zip(inputs.input_ids, generated_ids)]
    return processor.batch_decode(trimmed, skip_special_tokens=True, clean_up_tokenization_spaces=False)[0]


def normalize_event(
    raw_event: dict[str, Any],
    *,
    batch_index: int,
    batch_start: float,
    batch_end: float,
    subtitle_context: str,
) -> dict[str, Any]:
    event_type = str(raw_event.get("event_type") or "low_value").strip()
    if event_type not in EVENT_TYPES:
        event_type = "low_value"

    time_range = str(raw_event.get("time_range") or "").strip()
    if "-" not in time_range:
        time_range = f"{timestamp_from_seconds(batch_start)}-{timestamp_from_seconds(batch_end)}"
    start, end = parse_time_range(time_range)
    outside_batch = end < batch_start - 1.0 or start > batch_end + 1.0
    if outside_batch:
        start = batch_start
        end = max(batch_end, batch_start + 3.0)
    if end <= start:
        end = max(batch_end, start + 3.0)

    score = clamp_float(raw_event.get("score", 0), 0.0, 10.0)
    confidence = clamp_float(raw_event.get("confidence", 0.5), 0.0, 1.0)

    recommended = raw_event.get("recommended_clip")
    if not isinstance(recommended, dict):
        recommended = {}
    rec_start = seconds_from_timestamp(str(recommended.get("start") or "")) if recommended.get("start") else max(0, start - 2)
    rec_end = seconds_from_timestamp(str(recommended.get("end") or "")) if recommended.get("end") else end + 3
    if rec_end <= rec_start or rec_end < batch_start - 5.0 or rec_start > batch_end + 5.0:
        rec_start = max(0, start - 3)
        rec_end = end + 3

    return {
        "event_id": f"evt_{batch_index:04d}_{abs(hash((event_type, time_range))) % 10000:04d}",
        "event_type": event_type,
        "time_range": f"{timestamp_from_seconds(start)}-{timestamp_from_seconds(end)}",
        "score": score,
        "confidence": confidence,
        "visual_evidence": str(raw_event.get("visual_evidence") or raw_event.get("reason") or ""),
        "subtitle_evidence": str(raw_event.get("subtitle_evidence") or subtitle_context or ""),
        "recommended_clip": {
            "start": timestamp_from_seconds(rec_start),
            "end": timestamp_from_seconds(rec_end),
            "reason": str(recommended.get("reason") or "模型推荐剪辑范围"),
        },
        "batch_index": batch_index,
    }


def clamp_float(value: Any, minimum: float, maximum: float) -> float:
    try:
        parsed = float(value)
    except Exception:
        parsed = minimum
    return max(minimum, min(maximum, parsed))


def normalize_response(
    raw_response: str,
    *,
    frame_batch: list[dict[str, Any]],
    batch_index: int,
    subtitle_context: str,
) -> tuple[list[dict[str, str]], str, list[dict[str, Any]], str]:
    batch_start = float(frame_batch[0]["seconds"])
    batch_end = float(frame_batch[-1]["seconds"])
    try:
        payload = json.loads(strip_code_fence(raw_response))
        observations = payload.get("frame_observations", [])
        summary = str(payload.get("overall_activity_summary", "") or "")
        if not isinstance(observations, list):
            observations = []
        normalized_observations = []
        for index, frame in enumerate(frame_batch):
            entry = observations[index] if index < len(observations) else {}
            if isinstance(entry, dict):
                text = str(entry.get("observation", "") or "")
                timestamp = str(entry.get("timestamp", "") or frame["timestamp"])
            else:
                text = str(entry or "")
                timestamp = frame["timestamp"]
            normalized_observations.append({"timestamp": timestamp, "observation": text})

        raw_events = payload.get("events", [])
        if not isinstance(raw_events, list):
            raw_events = []
        events = [
            normalize_event(
                event,
                batch_index=batch_index,
                batch_start=batch_start,
                batch_end=batch_end,
                subtitle_context=subtitle_context,
            )
            for event in raw_events
            if isinstance(event, dict)
        ]
        if not events:
            events = build_heuristic_events(
                observations=normalized_observations,
                summary=summary,
                batch_index=batch_index,
                batch_start=batch_start,
                batch_end=batch_end,
                subtitle_context=subtitle_context,
            )
        return normalized_observations, summary, events, "success"
    except Exception:
        fallback = raw_response[:1000]
        observations = [
            {"timestamp": frame["timestamp"], "observation": fallback if index == 0 else ""}
            for index, frame in enumerate(frame_batch)
        ]
        events = build_heuristic_events(
            observations=observations,
            summary=fallback,
            batch_index=batch_index,
            batch_start=batch_start,
            batch_end=batch_end,
            subtitle_context=subtitle_context,
        )
        return observations, fallback, events, "parse_error"


def build_heuristic_events(
    *,
    observations: list[dict[str, str]],
    summary: str,
    batch_index: int,
    batch_start: float,
    batch_end: float,
    subtitle_context: str,
) -> list[dict[str, Any]]:
    text = " ".join([summary, subtitle_context] + [item.get("observation", "") for item in observations])
    keyword_groups = [
        ("death_fail", ["死", "杀", "失败", "翻车", "掉", "摔", "炸", "倒地", "受击"]),
        ("strong_reaction", ["我靠", "卧槽", "为什么", "不会", "离谱", "笑", "崩溃"]),
        ("puzzle_progress", ["无人机", "出口", "出去", "进去", "门", "机关", "解谜", "找到"]),
        ("live_interaction", ["送礼", "弹幕", "才艺", "直播", "观众"]),
        ("coop_command", ["你来", "我来", "跳", "走", "左", "右", "WS", "配合", "一起"]),
    ]
    events = []
    for event_type, keywords in keyword_groups:
        hits = [word for word in keywords if word in text]
        if not hits:
            continue
        score = min(9.0, 6.0 + len(hits) * 0.7)
        events.append(
            {
                "event_id": f"evt_{batch_index:04d}_{event_type}",
                "event_type": event_type,
                "time_range": f"{timestamp_from_seconds(batch_start)}-{timestamp_from_seconds(batch_end)}",
                "score": score,
                "confidence": 0.45,
                "visual_evidence": summary[:240],
                "subtitle_evidence": subtitle_context,
                "recommended_clip": {
                    "start": timestamp_from_seconds(max(0, batch_start - 2)),
                    "end": timestamp_from_seconds(batch_end + 3),
                    "reason": f"关键词命中: {', '.join(hits[:5])}",
                },
                "batch_index": batch_index,
            }
        )
    if events:
        return events
    return [
        {
            "event_id": f"evt_{batch_index:04d}_low_value",
            "event_type": "low_value",
            "time_range": f"{timestamp_from_seconds(batch_start)}-{timestamp_from_seconds(batch_end)}",
            "score": 2.0,
            "confidence": 0.4,
            "visual_evidence": summary[:240],
            "subtitle_evidence": subtitle_context,
            "recommended_clip": {
                "start": timestamp_from_seconds(batch_start),
                "end": timestamp_from_seconds(batch_end),
                "reason": "未发现明显可剪辑事件",
            },
            "batch_index": batch_index,
        }
    ]


def dedupe_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    sorted_events = sorted(events, key=lambda item: (seconds_from_timestamp(item["time_range"].split("-", 1)[0]), -item["score"]))
    kept: list[dict[str, Any]] = []
    for event in sorted_events:
        start, end = parse_time_range(event.get("time_range", ""))
        duplicate = False
        for existing in kept:
            existing_start, existing_end = parse_time_range(existing.get("time_range", ""))
            overlap = min(end, existing_end) - max(start, existing_start)
            if event.get("event_type") == existing.get("event_type") and overlap > 0:
                duplicate = True
                if event.get("score", 0) > existing.get("score", 0):
                    existing.update(event)
                break
        if not duplicate:
            kept.append(event)
    return kept


def build_candidate_clips(events: list[dict[str, Any]], config: dict[str, Any]) -> list[dict[str, Any]]:
    target = config.get("target_duration_seconds", [60, 90])
    max_duration = float(target[1] if isinstance(target, list) and len(target) > 1 else 90)
    min_score = 7.0
    candidates = [event for event in events if event.get("event_type") != "low_value" and float(event.get("score", 0)) >= min_score]
    if not candidates:
        candidates = [event for event in events if event.get("event_type") != "low_value"][:8]

    selected: list[dict[str, Any]] = []

    def add_event(event: dict[str, Any] | None) -> None:
        if not event:
            return
        if any(event["event_id"] == item["event_id"] for item in selected):
            return
        selected.append(event)

    high_impact = [event for event in candidates if event["event_type"] in {"death_fail", "strong_reaction"}]
    add_event(max(high_impact, key=lambda item: item.get("score", 0), default=None))

    for event_type in ["death_fail", "strong_reaction", "puzzle_progress", "live_interaction", "coop_command"]:
        typed = sorted([event for event in candidates if event["event_type"] == event_type], key=lambda item: item.get("score", 0), reverse=True)
        limit = 3 if event_type == "death_fail" else 2
        for event in typed[:limit]:
            add_event(event)

    final_fail = max(
        [event for event in candidates if event["event_type"] in {"death_fail", "coop_command"}],
        key=lambda item: seconds_from_timestamp(item["time_range"].split("-", 1)[0]),
        default=None,
    )
    add_event(final_fail)

    for event in sorted(candidates, key=lambda item: item.get("score", 0), reverse=True):
        add_event(event)

    clips = []
    total_duration = 0.0
    for event in selected:
        rec = event.get("recommended_clip") if isinstance(event.get("recommended_clip"), dict) else {}
        start_ts = rec.get("start") or event["time_range"].split("-", 1)[0]
        end_ts = rec.get("end") or event["time_range"].split("-", 1)[-1]
        start = seconds_from_timestamp(start_ts)
        end = seconds_from_timestamp(end_ts)
        if end <= start:
            end = start + 6
        duration = end - start
        if clips and total_duration + duration > max_duration:
            continue
        total_duration += duration
        clips.append(
            {
                "clip_id": f"clip_{len(clips) + 1:04d}",
                "source_event_ids": [event["event_id"]],
                "timestamp": f"{timestamp_from_seconds(start)}-{timestamp_from_seconds(end)}",
                "role": event["event_type"],
                "score": event.get("score", 0),
                "picture": event.get("visual_evidence") or event.get("subtitle_evidence") or event["event_type"],
                "narration_hint": "",
                "OST": 1,
            }
        )
    clips.sort(key=lambda item: seconds_from_timestamp(item["timestamp"].split("-", 1)[0]))
    return clips


def build_quality_report(
    events: list[dict[str, Any]],
    clips: list[dict[str, Any]],
    config: dict[str, Any],
    *,
    successful_batch_count: int = 0,
    total_batch_count: int = 0,
) -> dict[str, Any]:
    fail_count = sum(1 for clip in clips if clip.get("role") == "death_fail")
    reaction_count = sum(1 for clip in clips if clip.get("role") == "strong_reaction")
    scores = [float(clip.get("score", 0)) for clip in clips]
    base_score = sum(scores) / len(scores) if scores else 0.0
    if fail_count >= int(config.get("requirements", {}).get("min_fail_events", 3)):
        base_score += 0.5
    if reaction_count >= int(config.get("requirements", {}).get("min_strong_reactions", 2)):
        base_score += 0.3
    publish_score = round(min(10.0, base_score), 2)
    failed_batch_count = max(0, total_batch_count - successful_batch_count)
    success_ratio = successful_batch_count / total_batch_count if total_batch_count else 0.0
    inference_pass = total_batch_count > 0 and success_ratio >= 0.8
    issues = []
    if successful_batch_count == 0:
        issues.append("视觉模型未成功分析任何批次，候选片段不可用于成片")
    elif not inference_pass:
        issues.append("视觉模型失败批次过多，候选片段仅供人工复核")
    if publish_score < 8.0:
        issues.append("候选剪辑分数不足，建议人工复查或重新筛选")
    return {
        "publish_score": publish_score,
        "pass": inference_pass and publish_score >= float(config.get("quality_gate", {}).get("pass_score", 8.0)),
        "manual_review": successful_batch_count > 0 and publish_score >= float(config.get("quality_gate", {}).get("manual_review_score", 7.0)),
        "vision_inference_pass": inference_pass,
        "vision_successful_batch_count": successful_batch_count,
        "vision_failed_batch_count": failed_batch_count,
        "vision_success_ratio": round(success_ratio, 4),
        "fail_event_count": fail_count,
        "strong_reaction_count": reaction_count,
        "candidate_clip_count": len(clips),
        "event_count": len(events),
        "reasons": [
            f"候选片段数: {len(clips)}",
            f"失败/翻车事件: {fail_count}",
            f"强反应事件: {reaction_count}",
            f"视觉成功批次: {successful_batch_count}/{total_batch_count}",
        ],
        "issues": issues,
    }


def write_json(path: Path, payload: Any) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def create_rough_cut(video_path: Path, clips: list[dict[str, Any]], output_path: Path) -> bool:
    if not clips or not shutil.which("ffmpeg"):
        return False
    temp_dir = output_path.parent / "_rough_cut_segments"
    temp_dir.mkdir(parents=True, exist_ok=True)
    segment_paths = []
    for index, clip in enumerate(clips):
        start, end = parse_time_range(clip["timestamp"])
        segment_path = temp_dir / f"segment_{index:03d}.mp4"
        command = [
            "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-ss",
            str(start),
            "-to",
            str(end),
            "-i",
            str(video_path),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-pix_fmt",
            "yuv420p",
            str(segment_path),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode == 0 and segment_path.exists():
            segment_paths.append(segment_path)
    if not segment_paths:
        return False
    concat_path = temp_dir / "concat.txt"
    concat_path.write_text("\n".join(f"file '{path}'" for path in segment_paths), encoding="utf-8")
    command = [
        "ffmpeg",
        "-y",
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "concat",
        "-safe",
        "0",
        "-i",
        str(concat_path),
        "-c",
        "copy",
        str(output_path),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, check=False)
    return completed.returncode == 0 and output_path.exists()


def resolve_task_file(root: Path, value: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path
    for base in [root, *root.parents]:
        candidate = base / path
        if candidate.exists():
            return candidate
    return root / path


def run(config_path: Path) -> Path:
    task_config = json.loads(config_path.read_text(encoding="utf-8"))
    root = config_path.parent
    video_path = resolve_task_file(root, task_config["video_file"])
    subtitle_file = task_config.get("subtitle_file") or ""
    subtitle_path = resolve_task_file(root, subtitle_file) if subtitle_file else None

    preferred_output_dir = Path(os.getenv("NARRATOAI_KAGGLE_OUTPUT_DIR", "/kaggle/working/narratoai_outputs"))
    output_dir = preferred_output_dir if preferred_output_dir.parent.exists() else root / "kaggle_output"
    output_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = output_dir / "keyframes"
    refined_dir = output_dir / "refined_keyframes"

    interval = float(task_config.get("frame_interval_seconds", 3))
    refine_interval = float(task_config.get("refine_interval_seconds", 0.75))
    batch_size = max(1, int(task_config.get("batch_size", 8)))
    model_name = task_config.get("model_name") or "Qwen/Qwen2.5-VL-7B-Instruct"
    max_new_tokens = int(task_config.get("max_new_tokens", 1024))
    max_analysis_seconds = task_config.get("max_analysis_seconds")
    if max_analysis_seconds is not None:
        max_analysis_seconds = float(max_analysis_seconds)
    analysis_ranges_seconds = task_config.get("analysis_ranges_seconds")
    subtitles = parse_srt(subtitle_path)

    print(f"Extracting coarse keyframes from {video_path} every {interval}s")
    frames = extract_keyframes(
        video_path,
        frames_dir,
        interval,
        max_analysis_seconds,
        analysis_ranges_seconds,
    )
    batches = chunked(frames, batch_size)
    print(f"Extracted {len(frames)} frames in {len(batches)} batches")

    print(f"Loading model: {model_name}")
    model, processor = load_model(model_name)

    batch_results = []
    all_events: list[dict[str, Any]] = []
    for batch_index, frame_batch in enumerate(batches):
        start_seconds = float(frame_batch[0]["seconds"])
        end_seconds = float(frame_batch[-1]["seconds"])
        time_range = f"{timestamp_from_seconds(start_seconds)}-{timestamp_from_seconds(end_seconds)}"
        subtitle_context = subtitles_for_range(subtitles, start_seconds, end_seconds)
        prompt = build_prompt(
            frame_count=len(frame_batch),
            frame_timestamps=[item["timestamp"] for item in frame_batch],
            video_theme=task_config.get("video_theme", ""),
            custom_prompt=task_config.get("custom_prompt", ""),
            subtitle_context=subtitle_context,
        )
        print(f"Analyzing batch {batch_index + 1}/{len(batches)}: {time_range}")
        try:
            raw_response = analyze_batch(model, processor, frame_batch, prompt, max_new_tokens)
            observations, summary, events, status = normalize_response(
                raw_response,
                frame_batch=frame_batch,
                batch_index=batch_index,
                subtitle_context=subtitle_context,
            )
            error_message = ""
            if status != "success":
                retry_prompt = (
                    prompt
                    + "\n\n上一次返回无法解析。请严格只返回合法 JSON 对象，不要返回标点重复、Markdown 或解释文字。"
                )
                raw_response = analyze_batch(model, processor, frame_batch, retry_prompt, max_new_tokens)
                observations, summary, events, status = normalize_response(
                    raw_response,
                    frame_batch=frame_batch,
                    batch_index=batch_index,
                    subtitle_context=subtitle_context,
                )
                error_message = "" if status == "success" else "Model output was not valid event JSON after retry"
        except Exception as exc:
            raw_response = ""
            observations = []
            summary = ""
            events = build_heuristic_events(
                observations=[],
                summary="",
                batch_index=batch_index,
                batch_start=start_seconds,
                batch_end=end_seconds,
                subtitle_context=subtitle_context,
            )
            status = "failed"
            error_message = str(exc)

        all_events.extend(events)
        batch_results.append(
            {
                "batch_index": batch_index,
                "status": status,
                "time_range": time_range,
                "raw_response": raw_response,
                "frame_observations": observations,
                "overall_activity_summary": summary,
                "events": events,
                "error_message": error_message,
            }
        )

    events = dedupe_events(all_events)
    successful_batch_indexes = {
        result["batch_index"] for result in batch_results if result.get("status") == "success"
    }
    vision_events = [
        event for event in events if event.get("batch_index") in successful_batch_indexes
    ]

    high_score_events = [event for event in vision_events if event.get("event_type") != "low_value" and event.get("score", 0) >= 7]
    for event in high_score_events[:24]:
        start, end = parse_time_range(event["time_range"])
        refine_start = max(0.0, start - 5)
        refine_end = end + 5
        event_dir = refined_dir / event["event_id"]
        event["refined_keyframes"] = extract_refined_keyframes(
            video_path,
            event_dir,
            refine_start,
            refine_end,
            refine_interval,
        )

    clips = build_candidate_clips(vision_events, task_config)
    quality_report = build_quality_report(
        events,
        clips,
        task_config,
        successful_batch_count=len(successful_batch_indexes),
        total_batch_count=len(batch_results),
    )

    artifact = {
        "artifact_version": "narratoai-kaggle-video-understanding-v1",
        "generated_at": datetime.now().isoformat(),
        "task_name": task_config.get("task_name", ""),
        "video_file": task_config.get("video_file", ""),
        "frame_interval_seconds": interval,
        "refine_interval_seconds": refine_interval,
        "vision_batch_size": batch_size,
        "vision_llm_provider": "kaggle",
        "vision_model_name": model_name,
        "batches": batch_results,
        "events": events,
    }
    event_timeline = {
        "video": os.path.basename(str(video_path)),
        "task_name": task_config.get("task_name", ""),
        "events": events,
    }
    candidate_clips = {
        "task_name": task_config.get("task_name", ""),
        "target_duration_seconds": task_config.get("target_duration_seconds", [60, 90]),
        "clips": clips,
    }

    write_json(output_dir / "analysis_result.json", artifact)
    write_json(output_dir / "event_timeline.json", event_timeline)
    write_json(output_dir / "candidate_clips.json", candidate_clips)
    write_json(output_dir / "quality_report.json", quality_report)

    task_name = task_config.get("task_name")
    if task_name:
        write_json(root / f"{task_name}_analysis_result.json", artifact)
        write_json(root / f"{task_name}_event_timeline.json", event_timeline)
        write_json(root / f"{task_name}_candidate_clips.json", candidate_clips)

    rough_cut_path = output_dir / "rough_cut.mp4"
    if create_rough_cut(video_path, clips, rough_cut_path):
        print(f"Wrote rough cut: {rough_cut_path}")

    print(f"Wrote outputs to {output_dir}")
    return output_dir / "candidate_clips.json"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="task_config.json")
    args = parser.parse_args()
    run(Path(args.config).resolve())


if __name__ == "__main__":
    main()
