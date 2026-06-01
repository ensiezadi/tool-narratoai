#!/usr/bin/env python3
"""Validate a NarratoAI highlight script before rendering."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any


TIMESTAMP_RE = re.compile(
    r"(?P<sh>\d{2}):(?P<sm>\d{2}):(?P<ss>\d{2}),(?P<sms>\d{3})"
    r"-"
    r"(?P<eh>\d{2}):(?P<em>\d{2}):(?P<es>\d{2}),(?P<ems>\d{3})"
)

HOOK_EVENTS = {"strong_reaction", "death_fail", "puzzle_progress", "coop_command"}


def parse_timestamp(value: str) -> tuple[float, float] | None:
    match = TIMESTAMP_RE.fullmatch((value or "").strip())
    if not match:
        return None

    groups = match.groupdict()
    start = (
        int(groups["sh"]) * 3600
        + int(groups["sm"]) * 60
        + int(groups["ss"])
        + int(groups["sms"]) / 1000
    )
    end = (
        int(groups["eh"]) * 3600
        + int(groups["em"]) * 60
        + int(groups["es"])
        + int(groups["ems"]) / 1000
    )
    return float(start), float(end)


def load_script(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as fp:
        payload = json.load(fp)
    if isinstance(payload, dict):
        items = payload.get("items") or payload.get("video_clip_json") or payload.get("clips")
    else:
        items = payload
    if not isinstance(items, list):
        raise ValueError("script JSON must be a list or contain an items/video_clip_json/clips list")
    return [item for item in items if isinstance(item, dict)]


def check_script(
    items: list[dict[str, Any]],
    *,
    min_score: float,
    min_duration: float,
    max_duration: float,
    hook_max_duration: float,
) -> tuple[list[str], list[str], dict[str, Any]]:
    errors: list[str] = []
    warnings: list[str] = []
    event_counter: Counter[str] = Counter()
    durations: list[float] = []

    previous_start = -1.0
    for index, item in enumerate(items, start=1):
        label = f"#{item.get('_id', index)}"
        parsed = parse_timestamp(str(item.get("timestamp") or ""))
        if parsed is None:
            errors.append(f"{label}: timestamp 格式不合法")
            continue

        start, end = parsed
        duration = end - start
        durations.append(duration)
        if duration <= 0:
            errors.append(f"{label}: duration <= 0")
        if start < previous_start:
            warnings.append(f"{label}: 时间线不是递增顺序")
        previous_start = start

        if duration < min_duration:
            warnings.append(f"{label}: 片段偏短 {duration:.1f}s，可能需要合并")
        if duration > max_duration:
            warnings.append(f"{label}: 片段偏长 {duration:.1f}s，可能需要拆分")

        score = float(item.get("score") or 0)
        if score < min_score:
            warnings.append(f"{label}: score={score:.1f} 低于阈值 {min_score:.1f}")

        ost = item.get("OST")
        if ost not in (0, 1, 2):
            errors.append(f"{label}: OST 必须是 0/1/2")

        narration = str(item.get("narration") or "").strip()
        if ost in (0, 2) and not narration:
            errors.append(f"{label}: OST={ost} 但 narration 为空")
        if ost == 1 and narration:
            warnings.append(f"{label}: OST=1 会跳过 TTS，但 narration 不为空")

        if not str(item.get("picture") or item.get("visual_evidence") or "").strip():
            errors.append(f"{label}: 缺少 picture/visual_evidence")

        event_type = str(item.get("event_type") or "").strip()
        event_counter[event_type or "unknown"] += 1

    if not items:
        errors.append("脚本为空")
    else:
        first = items[0]
        first_parsed = parse_timestamp(str(first.get("timestamp") or ""))
        first_event = str(first.get("event_type") or "").strip()
        if first_event not in HOOK_EVENTS:
            warnings.append(f"#1: 首段 event_type={first_event or 'unknown'}，hook 可能不够强")
        if first_parsed is not None and first_parsed[1] - first_parsed[0] > hook_max_duration:
            warnings.append(f"#1: 首段超过 {hook_max_duration:.1f}s，建议压短 hook")

    summary = {
        "clips": len(items),
        "total_duration": round(sum(durations), 2),
        "min_duration": round(min(durations), 2) if durations else 0,
        "max_duration": round(max(durations), 2) if durations else 0,
        "event_types": dict(event_counter),
    }
    return errors, warnings, summary


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("script_path", type=Path)
    parser.add_argument("--min-score", type=float, default=7.0)
    parser.add_argument("--min-duration", type=float, default=6.0)
    parser.add_argument("--max-duration", type=float, default=15.0)
    parser.add_argument("--hook-max-duration", type=float, default=15.0)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    args = parser.parse_args()

    items = load_script(args.script_path)
    errors, warnings, summary = check_script(
        items,
        min_score=args.min_score,
        min_duration=args.min_duration,
        max_duration=args.max_duration,
        hook_max_duration=args.hook_max_duration,
    )

    payload = {
        "script_path": str(args.script_path),
        "ok": not errors,
        "summary": summary,
        "errors": errors,
        "warnings": warnings,
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        status = "PASS" if not errors else "FAIL"
        print(f"Highlight script check: {status}")
        print(f"- clips: {summary['clips']}")
        print(f"- total_duration: {summary['total_duration']}s")
        print(f"- duration_range: {summary['min_duration']}s - {summary['max_duration']}s")
        print(f"- event_types: {summary['event_types']}")
        if errors:
            print("\nErrors:")
            for error in errors:
                print(f"- {error}")
        if warnings:
            print("\nWarnings:")
            for warning in warnings:
                print(f"- {warning}")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
