import importlib.util
import json
import math
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List


def ensure_package(package: str, import_name: str):
    if importlib.util.find_spec(import_name) is None:
        subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", package])


ensure_package("opencv-python-headless", "cv2")
ensure_package("pillow", "PIL")
ensure_package("numpy", "numpy")
ensure_package("pandas", "pandas")
ensure_package("kaggle-benchmarks", "kaggle_benchmarks")

import cv2
import kaggle_benchmarks as kbench
import numpy as np
import pandas as pd
from kaggle_benchmarks.content_types import images


INPUT_ROOT = Path("/kaggle/input")
WORK_ROOT = Path("/kaggle/working")
STORYBOARD_DIR = WORK_ROOT / "storyboards"
RESULT_DIR = WORK_ROOT / "results"
REPORT_JSONL = RESULT_DIR / "narrato_segment_understanding_report.jsonl"
SUMMARY_JSON = RESULT_DIR / "candidate_clips.json"

VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
SEGMENT_SECONDS = 14.0
SEGMENT_STRIDE_SECONDS = 10.0
MAX_SEGMENTS_PER_VIDEO = 48
SAMPLE_FRAMES_PER_SEGMENT = 8
MIN_EVENT_SCORE = 7.0

EVENT_TYPES = {
    "death_fail",
    "strong_reaction",
    "coop_command",
    "puzzle_progress",
    "live_interaction",
    "transition",
    "low_value",
}


@dataclass
class NarratoSegmentUnderstanding:
    summary: str
    event_type: str
    score: float
    confidence: float
    visual_evidence: str
    highlight_reason: str
    main_objects: list[str]
    main_actions: list[str]
    scene: str
    screen_text: str
    ost: int
    ost_reason: str
    narration: str
    recommended_start_sec: float
    recommended_end_sec: float


def append_jsonl(path: Path, record: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def find_video_files(input_root: Path = INPUT_ROOT) -> List[str]:
    return sorted(str(p) for p in input_root.rglob("*") if p.is_file() and p.suffix.lower() in VIDEO_EXTS)


def probe_video(video_path: str) -> dict:
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")
    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    cap.release()
    if fps <= 0 or total_frames <= 0:
        raise RuntimeError(f"视频元信息异常: {video_path}")
    return {
        "video_path": video_path,
        "fps": fps,
        "total_frames": total_frames,
        "duration": total_frames / fps,
        "width": width,
        "height": height,
    }


def build_video_segments(video_files: list[str]) -> pd.DataFrame:
    rows = []
    for video_path in video_files:
        duration = float(probe_video(video_path)["duration"])
        segment_index = 0
        start_sec = 0.0
        while start_sec < duration:
            end_sec = min(start_sec + SEGMENT_SECONDS, duration)
            if end_sec - start_sec >= 3:
                rows.append(
                    {
                        "video_path": video_path,
                        "segment_index": segment_index,
                        "start_sec": round(start_sec, 2),
                        "end_sec": round(end_sec, 2),
                    }
                )
                segment_index += 1
            if segment_index >= MAX_SEGMENTS_PER_VIDEO:
                break
            start_sec += SEGMENT_STRIDE_SECONDS
    return pd.DataFrame(rows)


def read_frame_at_time(cap, fps: float, sec: float):
    cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(sec * fps)))
    ok, frame = cap.read()
    if not ok:
        return None
    return frame


def resize_keep_ratio(frame, target_width: int = 360):
    h, w = frame.shape[:2]
    scale = target_width / max(1, w)
    return cv2.resize(frame, (target_width, max(1, int(h * scale))))


def make_segment_storyboard(
    video_path: str,
    start_sec: float,
    end_sec: float,
    segment_index: int,
    output_dir: Path = STORYBOARD_DIR,
) -> dict:
    output_dir.mkdir(parents=True, exist_ok=True)
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频: {video_path}")

    fps = cap.get(cv2.CAP_PROP_FPS)
    total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    video_duration = total_frames / fps
    start_sec = max(0.0, float(start_sec))
    end_sec = min(float(end_sec), video_duration)
    seg_duration = end_sec - start_sec
    timestamps = [start_sec + seg_duration * (i + 1) / (SAMPLE_FRAMES_PER_SEGMENT + 1) for i in range(SAMPLE_FRAMES_PER_SEGMENT)]

    frames = []
    valid_timestamps = []
    for t in timestamps:
        frame = read_frame_at_time(cap, fps, t)
        if frame is None:
            continue
        frames.append(resize_keep_ratio(frame))
        valid_timestamps.append(round(t, 2))
    cap.release()

    if not frames:
        raise RuntimeError(f"没有抽出有效帧: {video_path}, start={start_sec}, end={end_sec}")

    max_h = max(f.shape[0] for f in frames)
    normalized = []
    for frame in frames:
        h, _ = frame.shape[:2]
        if h < max_h:
            frame = cv2.copyMakeBorder(frame, 0, max_h - h, 0, 0, cv2.BORDER_CONSTANT, value=(255, 255, 255))
        normalized.append(frame)

    cols = 4
    rows = math.ceil(len(normalized) / cols)
    blank = np.ones_like(normalized[0]) * 255
    grid_rows = []
    for r in range(rows):
        row_imgs = []
        for c in range(cols):
            idx = r * cols + c
            row_imgs.append(normalized[idx] if idx < len(normalized) else blank)
        grid_rows.append(np.hstack(row_imgs))

    storyboard = np.vstack(grid_rows)
    source_name = Path(video_path).stem
    out_path = output_dir / f"{source_name}_seg{int(segment_index):04d}_{int(start_sec * 1000)}.jpg"
    cv2.imwrite(str(out_path), storyboard)
    return {
        "storyboard_path": str(out_path),
        "timestamps": valid_timestamps,
        "start_sec": start_sec,
        "end_sec": end_sec,
    }


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


@kbench.task(
    name="NarratoAI Segment Event Understanding",
    description="Identify edit-worthy game/live-stream events from sampled segment storyboards.",
)
def narrato_segment_understanding(
    llm,
    video_path: str,
    segment_index: int,
    start_sec: float,
    end_sec: float,
) -> dict:
    storyboard_info = make_segment_storyboard(video_path, start_sec, end_sec, int(segment_index))
    img = images.from_path(storyboard_info["storyboard_path"])

    prompt = f"""
你正在为 NarratoAI 做游戏/直播短视频剪辑识别。

当前视频片段时间范围：
start_sec = {start_sec}
end_sec = {end_sec}

下面这张图片是该片段内部按时间顺序抽取的关键帧拼图。
你的任务不是写普通摘要，而是判断这段是否值得剪进短视频。

请输出结构化结果：
1. summary: 中文概括片段发生了什么。
2. event_type: 只能是 death_fail / strong_reaction / coop_command / puzzle_progress / live_interaction / transition / low_value。
3. score: 0-10 的剪辑价值分。低价值赶路/等待/菜单操作给 0-3；有明确视觉事件给 7+。
4. confidence: 0-1 的置信度。
5. visual_evidence: 必须写清楚可见画面证据。没有画面证据时写空字符串，并把 event_type 设为 low_value。
6. highlight_reason: 为什么它值得或不值得剪。
7. main_objects: 主要人物、物体、地点元素。
8. main_actions: 主要动作变化。
9. scene: 场景类型。
10. screen_text: 只提取原始视频画面真实文字。
11. ost: 0=纯 AI 解说，1=保留原声不加旁白，2=保留低音量原声同时叠加 AI 解说。
12. ost_reason: 一句话解释 OST。
13. narration: 如果适合加旁白，写一句 18-38 个中文字符的自然解说；如果必须保留原声，可写空字符串。
14. recommended_start_sec / recommended_end_sec: 推荐剪辑入点和出点，必须在当前片段范围内；好片段保留前后 1-3 秒。

重要规则：
- 不要只因为字幕或文字看起来有趣就判高分，必须有视觉证据。
- 菜单、加载、普通行走、无变化画面通常是 low_value。
- 死亡、失败、掉落、强反应、关键机关推进、明显合作动作才适合高分。
- 不要输出 Markdown，不要输出多余解释。
"""

    result = llm.prompt(prompt, image=img, schema=NarratoSegmentUnderstanding)

    event_type = result.event_type if result.event_type in EVENT_TYPES else "low_value"
    score = clamp(result.score, 0, 10)
    confidence = clamp(result.confidence, 0, 1)
    rec_start = clamp(result.recommended_start_sec, start_sec, end_sec)
    rec_end = clamp(result.recommended_end_sec, start_sec, end_sec)
    if rec_end <= rec_start:
        rec_start, rec_end = start_sec, end_sec
    if not str(result.visual_evidence or "").strip():
        event_type = "low_value"
        score = min(score, 3.0)

    record = {
        "source_video": Path(video_path).name,
        "video_path": video_path,
        "segment_index": int(segment_index),
        "start": float(start_sec),
        "end": float(end_sec),
        "recommended_start": rec_start,
        "recommended_end": rec_end,
        "summary": result.summary,
        "event_type": event_type,
        "score": score,
        "confidence": confidence,
        "visual_evidence": result.visual_evidence,
        "highlight_reason": result.highlight_reason,
        "main_objects": result.main_objects,
        "main_actions": result.main_actions,
        "scene": result.scene,
        "screen_text": result.screen_text,
        "ost": int(result.ost) if int(result.ost) in (0, 1, 2) else 2,
        "ost_reason": result.ost_reason,
        "text": result.narration,
        "storyboard": storyboard_info["storyboard_path"],
        "frame_timestamps": storyboard_info["timestamps"],
    }
    append_jsonl(REPORT_JSONL, record)

    kbench.assertions.assert_true(event_type in EVENT_TYPES, expectation="event_type 必须在允许集合内。")
    kbench.assertions.assert_true(0 <= score <= 10, expectation="score 必须在 0-10 内。")
    kbench.assertions.assert_true(0 <= confidence <= 1, expectation="confidence 必须在 0-1 内。")
    return record


video_files = find_video_files()
if not video_files:
    raise RuntimeError("没有找到视频文件，请确认 benchmark task 已附加 Kaggle Dataset。")

segment_df = build_video_segments(video_files)
if segment_df.empty:
    raise RuntimeError("没有生成任何片段。")

runs = narrato_segment_understanding.evaluate(
    llm=[kbench.llm],
    evaluation_data=segment_df,
    max_attempts=2,
    retry_delay=5,
    remove_run_files=False,
)

records = []
if REPORT_JSONL.exists():
    with open(REPORT_JSONL, "r", encoding="utf-8") as f:
        records = [json.loads(line) for line in f if line.strip()]

selected = [
    r
    for r in records
    if float(r.get("score", 0)) >= MIN_EVENT_SCORE
    and r.get("event_type") != "low_value"
    and str(r.get("visual_evidence", "")).strip()
]

SUMMARY_JSON.parent.mkdir(parents=True, exist_ok=True)
with open(SUMMARY_JSON, "w", encoding="utf-8") as f:
    json.dump({"records": records, "selected": selected}, f, ensure_ascii=False, indent=2)
