"""
SegmentPlan 构建器 — OST policy 集中化 + 统一时间轴。

职责：
    1. ost_to_policy(ost)      → 四个布尔策略
    2. build_segment_plan(json) → SegmentPlan[]
    3. validate_segment_plans() → 校验合法性
"""

from typing import List

from app.models.schema import SegmentPlan
from loguru import logger


# ──────────────────────────────────────────────────────────────────────────────
# OST → Policy
# ──────────────────────────────────────────────────────────────────────────────

_POLICY_TABLE = {
    0: {"need_tts": True,  "keep_original_audio": False, "duck_original_audio": False, "need_subtitle": True},
    1: {"need_tts": False, "keep_original_audio": True,  "duck_original_audio": False, "need_subtitle": False},
    2: {"need_tts": True,  "keep_original_audio": True,  "duck_original_audio": True,  "need_subtitle": True},
}

# 带音量的完整策略表（供 edit_manifest.json 和 EditSegment 使用）
_AUDIO_POLICY_TABLE = {
    0: {
        "need_tts": True,
        "keep_original_audio": False,
        "duck_original_audio": False,
        "original_volume": 0.0,
        "tts_volume": 1.0,
        "subtitle_enabled": True,
    },
    1: {
        "need_tts": False,
        "keep_original_audio": True,
        "duck_original_audio": False,
        "original_volume": 1.0,
        "tts_volume": 0.0,
        "subtitle_enabled": False,
    },
    2: {
        "need_tts": True,
        "keep_original_audio": True,
        "duck_original_audio": True,
        "original_volume": 0.25,
        "tts_volume": 1.0,
        "subtitle_enabled": True,
    },
}


def ost_to_policy(ost: int) -> dict:
    """
    OST 值 → 四个布尔策略。非法值默认回退到 0。

    返回 dict:
        need_tts, keep_original_audio, duck_original_audio, need_subtitle
    """
    if ost not in _POLICY_TABLE:
        logger.warning(f"非法 OST 值 {ost}，回退到 0")
        ost = 0
    return _POLICY_TABLE[ost].copy()


def ost_to_audio_policy(ost: int) -> dict:
    """
    OST 值 → 完整音频策略（含音量）。非法值默认回退到 0。

    返回 dict:
        need_tts, keep_original_audio, duck_original_audio,
        original_volume, tts_volume, subtitle_enabled
    """
    if ost not in _AUDIO_POLICY_TABLE:
        logger.warning(f"非法 OST 值 {ost}，回退到 0")
        ost = 0
    return _AUDIO_POLICY_TABLE[ost].copy()


# ──────────────────────────────────────────────────────────────────────────────
# Timestamp 解析
# ──────────────────────────────────────────────────────────────────────────────

def _parse_timestamp_seconds(ts: str) -> float:
    """
    解析时间戳字符串为秒数。

    支持格式：
        'HH:MM:SS'
        'HH:MM:SS,mmm'
        'HH:MM:SS.mmm'
        'MM:SS'
    """
    ts = ts.strip()
    ms = 0.0

    # 毫秒分隔符
    if ',' in ts:
        main, frac = ts.split(',', 1)
        ms = float('0.' + frac) if frac else 0.0
        ts = main
    elif '.' in ts:
        main, frac = ts.split('.', 1)
        ms = float('0.' + frac) if frac else 0.0
        ts = main

    parts = ts.split(':')
    parts = [int(p) for p in parts]

    if len(parts) == 3:
        return parts[0] * 3600 + parts[1] * 60 + parts[2] + ms
    elif len(parts) == 2:
        return parts[0] * 60 + parts[1] + ms
    else:
        raise ValueError(f"无法解析时间戳: {ts}")


def _parse_range(timestamp: str) -> tuple[float, float]:
    """
    解析 'HH:MM:SS-HH:MM:SS' 格式的范围字符串。

    返回 (start_seconds, end_seconds)
    """
    if '-' not in timestamp:
        raise ValueError(f"时间戳格式错误，缺少 '-': {timestamp}")

    start_str, end_str = timestamp.split('-', 1)
    return _parse_timestamp_seconds(start_str), _parse_timestamp_seconds(end_str)


# ──────────────────────────────────────────────────────────────────────────────
# Build SegmentPlan
# ──────────────────────────────────────────────────────────────────────────────

def build_segment_plan(script_segments: list[dict]) -> List[SegmentPlan]:
    """
    script JSON → SegmentPlan[]

    输入格式（每个 dict）:
        {
            "_id": str,
            "timestamp": "HH:MM:SS-HH:MM:SS",
            "narration": str,
            "picture": str,
            "OST": int (0/1/2)
        }

    处理规则：
        - OST 非法值默认为 0
        - duration <= 0 的片段跳过
        - narration 为空时 need_tts 强制为 False
        - target_start/target_end 累加计算
    """
    plans: List[SegmentPlan] = []
    target_cursor = 0.0

    for i, seg in enumerate(script_segments):
        # ── 解析时间 ──
        timestamp = seg.get("timestamp", "")
        if not timestamp:
            logger.warning(f"片段 {i} 缺少 timestamp，跳过")
            continue

        try:
            source_start, source_end = _parse_range(timestamp)
        except (ValueError, TypeError) as e:
            logger.warning(f"片段 {i} timestamp 解析失败: {e}，跳过")
            continue

        duration = source_end - source_start
        if duration <= 0:
            logger.warning(f"片段 {i} duration <= 0 ({duration}s)，跳过")
            continue

        # ── OST policy ──
        ost = seg.get("OST", 0)
        if not isinstance(ost, int) or ost not in (0, 1, 2):
            logger.warning(f"片段 {i} OST={ost} 非法，回退到 0")
            ost = 0

        policy = ost_to_policy(ost)

        # ── 文本为空时禁用 TTS ──
        narration = (seg.get("narration") or seg.get("text") or "").strip()
        need_tts = policy["need_tts"] and bool(narration)

        # ── 构建 SegmentPlan ──
        plan = SegmentPlan(
            index=i,
            source_start=source_start,
            source_end=source_end,
            target_start=target_cursor,
            target_end=target_cursor + duration,
            ost=ost,
            narration=narration,
            need_tts=need_tts,
            keep_original_audio=policy["keep_original_audio"],
            duck_original_audio=policy["duck_original_audio"],
            need_subtitle=policy["need_subtitle"] and need_tts,  # 无 TTS 则无字幕
        )
        plans.append(plan)
        target_cursor += duration

    logger.info(f"SegmentPlan 构建完成: {len(plans)} 个片段，总时长 {target_cursor:.1f}s")
    return plans


# ──────────────────────────────────────────────────────────────────────────────
# 验证
# ──────────────────────────────────────────────────────────────────────────────

def validate_segment_plans(plans: List[SegmentPlan]) -> List[str]:
    """
    校验 SegmentPlan 列表的合法性。

    返回警告列表（空 = 全部通过）。
    """
    warnings: List[str] = []

    if not plans:
        warnings.append("SegmentPlan 列表为空")
        return warnings

    for i, plan in enumerate(plans):
        # 时间重叠检查
        if i > 0:
            prev = plans[i - 1]
            if plan.target_start < prev.target_end - 0.01:
                warnings.append(
                    f"片段 {i} target_start ({plan.target_start:.2f}) "
                    f"< 前一片段 target_end ({prev.target_end:.2f})，存在重叠"
                )

        # TTS 片段文本非空
        if plan.need_tts and not plan.narration:
            warnings.append(f"片段 {i} need_tts=True 但 narration 为空")

        # 时长检查
        if plan.target_duration > 300:
            warnings.append(f"片段 {i} 时长 {plan.target_duration:.1f}s > 5min，可能过长")

    if warnings:
        for w in warnings:
            logger.warning(f"SegmentPlan 校验: {w}")
    else:
        logger.info("SegmentPlan 校验通过")

    return warnings


# ──────────────────────────────────────────────────────────────────────────────
# EditSegment 构建器
# ──────────────────────────────────────────────────────────────────────────────

def _build_edit_segment(seg: dict) -> "EditSegment":
    """
    单个 manifest segment dict → EditSegment。
    兼容 edit_manifest.json 和 script.json 两种格式。
    """
    from app.models.schema import EditSegment

    source = seg.get("source", {})
    if isinstance(source, str):
        source = {}

    narration = seg.get("narration", {})
    if isinstance(narration, str):
        narration = {"text": narration}

    audio = seg.get("audio_policy", {})
    if isinstance(audio, (int, float)):
        audio = {"ost": int(audio)}

    subtitle = seg.get("subtitle", {})
    if isinstance(subtitle, str):
        subtitle = {"text": subtitle}

    # audio_policy 可能来自 manifest，也可能由 ost 推导
    ost = audio.get("ost", seg.get("ost", seg.get("OST", 0)))
    policy = ost_to_audio_policy(ost)

    # manifest 中的 audio_policy 覆盖默认值
    for key in ("keep_original_audio", "duck_original_audio", "original_volume", "tts_volume"):
        if key in audio:
            policy[key] = audio[key]

    return EditSegment(
        id=seg.get("id", seg.get("_id", "")),
        source_video=source.get("video_path", seg.get("source_video", "")),
        source_start=source.get("start", seg.get("start", 0.0)),
        source_end=source.get("end", seg.get("end", 0.0)),
        text=narration.get("text", seg.get("narration", "") if isinstance(seg.get("narration"), str) else ""),
        ost=ost,
        need_tts=narration.get("need_tts", policy["need_tts"]),
        keep_original_audio=policy["keep_original_audio"],
        duck_original_audio=policy["duck_original_audio"],
        original_volume=policy["original_volume"],
        tts_volume=policy["tts_volume"],
        subtitle_enabled=subtitle.get("enabled", policy["subtitle_enabled"]),
        summary=(
            seg.get("visual", {}).get("summary", "")
            if isinstance(seg.get("visual"), dict)
            else seg.get("summary", "")
        ),
        scene=(
            seg.get("visual", {}).get("scene", "")
            if isinstance(seg.get("visual"), dict)
            else seg.get("scene", "")
        ),
    )


def load_edit_segments(path: str) -> "list[EditSegment]":
    """
    从 script.json 或 edit_manifest.json 加载 EditSegment 列表。

    自动判断格式：
        - 顶层是 list → script.json
        - 顶层是 dict 且有 "segments" → edit_manifest.json
    """
    import json

    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        segments = data
    elif isinstance(data, dict):
        segments = data.get("segments", [])
    else:
        raise ValueError(f"无法识别的文件格式: {path}")

    return [_build_edit_segment(seg) for seg in segments]


def edit_segments_to_script(edit_segments: "list[EditSegment]") -> list[dict]:
    """
    EditSegment[] → 兼容当前 NarratoAI task.py 的 script JSON 格式。

    输出格式与原 list_script 一致，可直接传给 build_segment_plan()。
    """
    script = []
    for seg in edit_segments:
        def _fmt(s):
            h = int(s // 3600)
            m = int((s % 3600) // 60)
            sec = s % 60
            return f"{h:02d}:{m:02d}:{sec:05.2f}"

        script.append({
            "_id": seg.id,
            "timestamp": f"{_fmt(seg.source_start)}-{_fmt(seg.source_end)}",
            "narration": seg.text,
            "picture": seg.summary,
            "OST": seg.ost,
            "scene": seg.scene,
        })
    return script


# ──────────────────────────────────────────────────────────────────────────────
# TTS 时长校验
# ──────────────────────────────────────────────────────────────────────────────

def check_tts_duration(plan: SegmentPlan, tts_duration: float) -> str:
    """
    检查 TTS 时长是否超出片段目标时长。

    返回:
        "ok"               — TTS 时长在片段时长以内
        "slight_overshoot" — 超出 0~20%，建议提高语速
        "severe_overshoot" — 超出 >20%，需要压缩文案或截断
    """
    seg_duration = plan.target_duration
    if seg_duration <= 0:
        return "ok"

    ratio = tts_duration / seg_duration

    if ratio <= 1.0:
        return "ok"
    elif ratio <= 1.2:
        return "slight_overshoot"
    else:
        return "severe_overshoot"


def log_tts_duration_report(plans: List[SegmentPlan]) -> dict:
    """
    批量校验所有 need_tts 片段的 TTS 时长，返回统计摘要。

    前提：plans 中的 tts_duration 字段已填充。
    """
    stats = {"ok": 0, "slight_overshoot": 0, "severe_overshoot": 0, "skipped": 0}

    for plan in plans:
        if not plan.need_tts or plan.tts_duration <= 0:
            stats["skipped"] += 1
            continue

        result = check_tts_duration(plan, plan.tts_duration)
        stats[result] += 1

        if result == "slight_overshoot":
            suggested_rate = plan.tts_duration / plan.target_duration
            logger.warning(
                f"片段 {plan.index} TTS 轻微超时: "
                f"{plan.tts_duration:.1f}s > {plan.target_duration:.1f}s，"
                f"建议语速 {suggested_rate:.2f}x"
            )
        elif result == "severe_overshoot":
            logger.error(
                f"片段 {plan.index} TTS 严重超时: "
                f"{plan.tts_duration:.1f}s > {plan.target_duration:.1f}s，"
                f"需压缩文案或截断"
            )

    logger.info(
        f"TTS 时长校验: ok={stats['ok']}, "
        f"轻微超时={stats['slight_overshoot']}, "
        f"严重超时={stats['severe_overshoot']}, "
        f"跳过={stats['skipped']}"
    )
    return stats
