# NarratoAI 视频处理流水线重构 Plan

> OST 从 flag 升级为 policy，时间轴统一为 target timeline。

---

## 目标

1. **OST policy 集中化** — `need_tts / keep_original_audio / duck_original_audio / need_subtitle` 由 OST 值唯一决定，不再散落在各模块
2. **时间轴统一** — 所有模块对齐 `target_start / target_end`（最终视频时间轴），不跟原视频时间轴
3. **TTS 时长校验** — 生成后检查是否超过片段时长，超长则提前提速/截短
4. **三轨音频模型** — 原声轨、TTS 轨、BGM 轨独立管理，OST 只控制混合策略

---

## 文件变更清单

| 文件 | 变更类型 | 说明 |
|------|----------|------|
| `app/models/schema.py` | 新增 | `SegmentPlan` dataclass |
| `app/services/segment_plan.py` | **新建** | `build_segment_plan()` + `ost_to_policy()` |
| `app/services/task.py` | 重构 | 流水线改为 SegmentPlan 驱动 |
| `app/services/clip_video.py` | 改造 | 接收 SegmentPlan[]，用 target timeline |
| `app/services/audio_merger.py` | 改造 | 接收 SegmentPlan[]，按策略混合 |
| `app/services/subtitle_merger.py` | 改造 | 接收 SegmentPlan[]，只对 OST 0/2 生成字幕 |
| `app/services/voice.py` | 增强 | TTS 时长校验 + 超时处理 |

---

## Phase 1: SegmentPlan 数据结构

**文件**: `app/models/schema.py`

```python
from dataclasses import dataclass
from typing import Literal

@dataclass
class SegmentPlan:
    index: int
    source_start: float      # 原视频起始时间（秒）
    source_end: float        # 原视频结束时间（秒）
    target_start: float      # 最终视频起始时间（秒）
    target_end: float        # 最终视频结束时间（秒）
    ost: Literal[0, 1, 2]
    narration: str           # 解说文本
    need_tts: bool
    keep_original_audio: bool
    duck_original_audio: bool
    need_subtitle: bool
    tts_audio_path: str = ""       # TTS 生成后填入
    tts_duration: float = 0.0      # TTS 音频时长
    video_clip_path: str = ""      # 裁剪后视频路径
    subtitle_file: str = ""        # 字幕文件路径
```

---

## Phase 2: Policy 与 SegmentPlan 构建

**文件**: `app/services/segment_plan.py`（新建）

```python
def ost_to_policy(ost: int) -> dict:
    """OST → 四个布尔策略，唯一真值表"""

def build_segment_plan(script_segments: list[dict]) -> list[SegmentPlan]:
    """
    script JSON → SegmentPlan[]
    - 过滤无效片段（duration <= 0）
    - OST 非法值默认为 0
    - 计算 target_start/target_end（累加）
    - 填充 policy 字段
    """

def validate_segment_plans(plans: list[SegmentPlan]) -> list[str]:
    """校验：时间不重叠、文本非空、时长合理"""
```

**OST 真值表**:

| OST | need_tts | keep_original | duck_original | need_subtitle |
|-----|----------|---------------|---------------|---------------|
| 0   | ✅        | ❌              | ❌               | ✅              |
| 1   | ❌        | ✅              | ❌               | ❌              |
| 2   | ✅        | ✅              | ✅               | ✅              |

---

## Phase 3: TTS 时长校验

**文件**: `app/services/voice.py`

在 `tts_multiple()` 返回后，对每个结果增加：

```python
def check_tts_duration(plan: SegmentPlan, tts_duration: float) -> str:
    """
    返回 "ok" | "slight_overshoot" | "severe_overshoot"
    - ok: tts_duration <= segment_duration
    - slight_overshoot: 超出 0~20%，建议提高语速 1.05x~1.20x
    - severe_overshoot: 超出 >20%，需要 LLM 压缩文案
    """
```

超出处理策略（Phase 3 只记录，不自动重试）：
- 轻微超出：日志警告，记录建议语速
- 严重超出：日志错误，跳过该片段或截断 TTS

---

## Phase 4: 重构 task.py 流水线

**文件**: `app/services/task.py`

### 新流水线顺序

```text
script JSON
  ↓
normalize_script()          # 校验字段、补默认值
  ↓
build_segment_plan()        # → SegmentPlan[]
  ↓
validate_segment_plans()    # 检查合法性
  ↓
generate_tts(plans)         # 只处理 need_tts=True 的
  ↓
check_tts_duration()        # 时长校验
  ↓
clip_video_by_plans()       # 按 source_start/end 裁剪
  ↓
build_audio_timeline()      # 三轨混合，对齐 target timeline
  ↓
build_subtitle_timeline()   # 对齐 target timeline
  ↓
concat_video_clips()        # 拼接裁剪后的视频
  ↓
mux_final_video()           # 合并视频+音频+字幕+BGM
```

### 关键改动

**旧代码**（散落各处的 OST 判断）：
```python
tts_segments = [seg for seg in list_script if seg['OST'] in [0, 2]]
# ... 30 行后又判断 if segment['OST'] == 1 ...
```

**新代码**（policy 集中）：
```python
plans = build_segment_plan(list_script)
tts_plans = [p for p in plans if p.need_tts]
# 下游只看 need_tts / keep_original_audio，不直接判断 ost
```

---

## Phase 5: 改造 clip_video.py

**改动点**:
- `clip_video_unified()` 接收 `SegmentPlan[]` 代替 `script_list + tts_results`
- 用 `plan.source_start / plan.source_end` 裁剪
- 用 `plan.keep_original_audio` 决定是否去声（代替直接判断 `ost == 0`）
- 裁剪后写入 `plan.video_clip_path`

---

## Phase 6: 改造 audio_merger.py

**改动点**:
- 接收 `SegmentPlan[]` 代替 `list_script`
- 对齐 `target_start / target_end`
- OST 2 片段做 ducking（原声降至 0.15~0.35）
- OST 0 片段原声静音
- 淡入淡出 80~150ms

---

## Phase 7: 改造 subtitle_merger.py

**改动点**:
- 接收 `SegmentPlan[]`
- 只对 `need_subtitle=True` 的片段生成字幕
- 字幕时间对齐 `target_start + tts_relative_time`
- OST 1 片段默认不生成字幕

---

## 实施顺序

```
Phase 1  → Phase 2  → Phase 3  → Phase 4  → Phase 5  → Phase 6  → Phase 7
schema     segment    voice      task       clip_video  audio_merger subtitle
           _plan                              .py        .py          _merger.py
```

每个 Phase 完成后跑一次现有测试，确保不 break。

---

## 风险与边界

| 风险 | 对策 |
|------|------|
| 现有 script JSON 格式不含 start/end 字段 | `build_segment_plan()` 兼容 timestamp 字符串格式，自动解析 |
| TTS 超时没有自动重试 | Phase 3 只记录日志，不自动重试（避免无限循环） |
| OST 2 ducking 参数未定 | 先用 0.25，后续通过 config 可调 |
| 现有两个 start_subclip 函数 | 重构后只保留一个，`start_subclip_unified` 合并进 `start_subclip` |
| 字幕与 TTS 不同步 | 强制用 target timeline 对齐，TTS 返回后才能生成字幕 |
