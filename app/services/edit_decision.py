"""
Edit-decision helpers for turning model output into usable clip scripts.

This module is intentionally deterministic: model prompts decide what may be
interesting, while these checks keep obviously weak or awkward clips out of the
final script.
"""

from __future__ import annotations

import re
from typing import Any

from loguru import logger


GENERIC_NARRATION_PREFIXES = (
    "这段视频展示",
    "视频片段展示",
    "画面展示",
    "现在我们来到了",
    "我们现在看到",
    "接下来我们看到",
)


def normalize_edit_script(
    items: list[dict[str, Any]],
    *,
    min_score: float = 7.0,
    min_duration: float = 1.5,
    max_narrated_duration: float = 14.0,
    max_original_duration: float = 26.0,
) -> list[dict[str, Any]]:
    """Normalize candidate clips into the script schema consumed by the editor.

    Rules:
    - Drop invalid timestamps and scored candidates below ``min_score``.
    - Keep each clip short enough to feel like an edit, not a batch summary.
    - Downgrade empty narrated clips to OST=1 so TTS is not forced.
    - Preserve useful evidence fields for review/debugging.
    """
    normalized: list[dict[str, Any]] = []

    for index, raw_item in enumerate(items):
        if not isinstance(raw_item, dict):
            continue

        timestamp = str(raw_item.get("timestamp", "") or "").strip()
        parsed_range = parse_timestamp_range(timestamp)
        if parsed_range is None:
            logger.warning(f"跳过时间戳无效的候选片段: {timestamp}")
            continue

        start_seconds, end_seconds = parsed_range
        duration = end_seconds - start_seconds
        if duration < min_duration:
            logger.warning(f"跳过过短候选片段: {timestamp} ({duration:.2f}s)")
            continue

        score = _coerce_float(raw_item.get("score"))
        if score is not None and score < min_score:
            logger.info(f"跳过低分候选片段: {timestamp}, score={score:.1f}")
            continue

        item = dict(raw_item)
        item["_id"] = str(item.get("_id") or item.get("clip_id") or f"clip_{len(normalized) + 1:04d}")

        narration = str(item.get("narration") or item.get("narration_hint") or "").strip()
        item["narration"] = narration

        ost = _normalize_ost(item.get("OST", 2))
        if ost in (0, 2) and not narration:
            ost = 1
        item["OST"] = ost

        max_duration = max_original_duration if ost == 1 else max_narrated_duration
        if duration > max_duration:
            item["timestamp"] = format_timestamp_range(start_seconds, start_seconds + max_duration)
            item.setdefault("edit_warnings", []).append(
                f"trimmed_from_{duration:.1f}s_to_{max_duration:.1f}s"
            )

        if _looks_like_batch_summary(narration):
            item.setdefault("edit_warnings", []).append("generic_batch_narration")

        if _has_weak_visual_evidence(item):
            item.setdefault("edit_warnings", []).append("weak_visual_evidence")

        item.setdefault("picture", str(item.get("picture") or item.get("visual_evidence") or "").strip())
        normalized.append(item)

    if not normalized and items:
        logger.warning("剪辑决策过滤后为空，回退保留原始可解析片段")
        return _fallback_parseable_items(items, max_narrated_duration=max_narrated_duration)

    logger.info(f"剪辑决策规范化完成: {len(items)} -> {len(normalized)}")
    return normalized


def parse_timestamp_range(timestamp: str) -> tuple[float, float] | None:
    if "-" not in timestamp:
        return None
    start_text, end_text = timestamp.split("-", 1)
    start_seconds = parse_timestamp_seconds(start_text)
    end_seconds = parse_timestamp_seconds(end_text)
    if start_seconds is None or end_seconds is None or end_seconds <= start_seconds:
        return None
    return start_seconds, end_seconds


def parse_timestamp_seconds(timestamp: str) -> float | None:
    text = (timestamp or "").strip().replace(".", ",")
    if not text:
        return None

    milliseconds = 0
    if "," in text:
        text, ms_text = text.split(",", 1)
        ms_digits = re.sub(r"\D", "", ms_text)
        if ms_digits:
            milliseconds = int(ms_digits[:3].ljust(3, "0"))

    parts = text.split(":")
    try:
        values = [int(part) for part in parts]
    except ValueError:
        return None

    if len(values) == 3:
        hours, minutes, seconds = values
    elif len(values) == 2:
        hours, minutes, seconds = 0, values[0], values[1]
    elif len(values) == 1:
        hours, minutes, seconds = 0, 0, values[0]
    else:
        return None

    return hours * 3600 + minutes * 60 + seconds + milliseconds / 1000.0


def format_timestamp_range(start_seconds: float, end_seconds: float) -> str:
    return f"{format_timestamp(start_seconds)}-{format_timestamp(end_seconds)}"


def format_timestamp(total_seconds: float) -> str:
    total_seconds = max(0.0, total_seconds)
    hours = int(total_seconds // 3600)
    minutes = int((total_seconds % 3600) // 60)
    seconds = int(total_seconds % 60)
    milliseconds = int(round((total_seconds - int(total_seconds)) * 1000))
    if milliseconds >= 1000:
        seconds += 1
        milliseconds -= 1000
    if seconds >= 60:
        minutes += 1
        seconds -= 60
    if minutes >= 60:
        hours += 1
        minutes -= 60
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _fallback_parseable_items(
    items: list[dict[str, Any]],
    *,
    max_narrated_duration: float,
) -> list[dict[str, Any]]:
    fallback: list[dict[str, Any]] = []
    for raw_item in items:
        if not isinstance(raw_item, dict):
            continue
        parsed_range = parse_timestamp_range(str(raw_item.get("timestamp", "") or ""))
        if parsed_range is None:
            continue
        start_seconds, end_seconds = parsed_range
        item = dict(raw_item)
        item["_id"] = str(item.get("_id") or item.get("clip_id") or f"clip_{len(fallback) + 1:04d}")
        item["timestamp"] = format_timestamp_range(
            start_seconds,
            min(end_seconds, start_seconds + max_narrated_duration),
        )
        item["OST"] = _normalize_ost(item.get("OST", 2))
        item.setdefault("edit_warnings", []).append("fallback_after_empty_filter")
        fallback.append(item)
    return fallback


def _coerce_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_ost(value: Any) -> int:
    try:
        ost = int(value)
    except (TypeError, ValueError):
        return 2
    return ost if ost in (0, 1, 2) else 2


def _looks_like_batch_summary(narration: str) -> bool:
    text = narration.strip()
    return any(text.startswith(prefix) for prefix in GENERIC_NARRATION_PREFIXES)


def _has_weak_visual_evidence(item: dict[str, Any]) -> bool:
    if item.get("visual_evidence"):
        return False
    has_scoring = "score" in item or "source_event_ids" in item or "event_type" in item or "role" in item
    if not has_scoring:
        return False
    picture = str(item.get("picture") or "").strip()
    return not picture or len(picture) < 8
