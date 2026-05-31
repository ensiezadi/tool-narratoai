# Kaggle Video Understanding Workflow

This document records the intended design for using Kaggle GPU as NarratoAI's
video-understanding layer. The goal is to help future contributors preserve the
same direction when implementing or refactoring this feature.

## Core Positioning

Kaggle should not be treated as an online API server and should not own final
video assembly. Its job is to run GPU-heavy video understanding over the full
source video and return structured editing evidence.

NarratoAI remains responsible for:

- script and narration planning
- TTS generation
- subtitle generation
- local final video assembly
- human confirmation in the WebUI
- final publish-quality review

The target flow is:

```text
local full video
-> Kaggle Dataset
-> Kaggle Notebook video understanding
-> event_timeline.json + candidate_clips.json
-> NarratoAI import
-> editable video_clip_json
-> WebUI confirmation
-> final video
-> model quality review
```

## Why This Layer Matters

Subtitle-only editing is not reliable for game/live-stream material. A subtitle
line can be funny while the picture has no visible event. The video understanding
layer must therefore produce visual evidence before a segment is allowed into a
highlight cut.

The first useful artifact is not "keyframes" alone. The first useful artifact is
a scored event timeline: death/failure, strong reaction, cooperative command,
puzzle progress, live interaction, and low-value filler.

## MVP Scope

The first implementation should deliver a working loop, not a perfect automatic
editor. Prefer a dedicated reusable Kaggle Dataset such as
`<username>/narratoai-video-understanding-tasks`; create it once, then update
it with `kaggle datasets version` for each new editing task.

1. Build a local Kaggle Dataset task directory from a source video.
2. Upload it with the Kaggle CLI.
3. Run a Kaggle Notebook that extracts keyframes and uses Qwen2VL/Qwen2.5-VL.
4. Produce `event_timeline.json`, `candidate_clips.json`, and optional
   `rough_cut.mp4`.
5. Import the result into NarratoAI.
6. Convert selected candidate clips to the existing `video_clip_json` format.
7. Let the WebUI generate the final video through the current local pipeline.
8. Run a final model review before marking the video publishable.

## Local Kaggle Task Package

Recommended generated structure:

```text
storage/kaggle_tasks/<task_name>/
├── input/
│   ├── part2.mp4
│   └── part2.srt
├── task_config.json
├── dataset-metadata.json
└── narratoai_video_understanding.ipynb
```

Example `task_config.json`:

```json
{
  "task_name": "part2_highlight",
  "video_file": "input/part2.mp4",
  "subtitle_file": "input/part2.srt",
  "frame_interval_seconds": 3,
  "refine_interval_seconds": 0.75,
  "batch_size": 8,
  "model_name": "Qwen/Qwen2.5-VL-7B-Instruct",
  "target_duration_seconds": [60, 90],
  "clip_style": "gaming_live_highlight",
  "requirements": {
    "min_fail_events": 3,
    "min_strong_reactions": 2,
    "ending": "death_fail_or_coop_fail"
  }
}
```

Example `dataset-metadata.json`:

```json
{
  "title": "NarratoAI part2 highlight task",
  "id": "chrisezadi/narratoai-part2-highlight-task",
  "licenses": [
    {
      "name": "CC0-1.0"
    }
  ]
}
```

Kaggle CLI commands:

```bash
# First time only:
kaggle datasets create -p storage/kaggle_tasks/<task_name> --dir-mode zip

# Every later editing task:
kaggle datasets version -p storage/kaggle_tasks/<task_name> -m "update video understanding task"
```

Kaggle stores Dataset versions, so do not keep adding unrelated huge videos to
the same Dataset forever. A practical rule is one reusable Dataset per project
or source-video pool.

Do not require the package to include pre-extracted keyframes. Kaggle should
extract frames from the full video so the process is reproducible and adjustable.

## Kaggle Notebook Responsibilities

The notebook should:

1. Read `task_config.json`.
2. Open the full source video from the mounted Kaggle Dataset.
3. Extract coarse keyframes every `frame_interval_seconds`.
4. Ask the model for frame observations and event candidates.
5. Refine high-score event windows by extracting frames every
   `refine_interval_seconds`.
6. Produce structured event data.
7. Optionally create `rough_cut.mp4` with original audio only for quick review.

Recommended output directory:

```text
/kaggle/working/narratoai_outputs/
├── keyframes/
├── refined_keyframes/
├── frame_analysis.json
├── event_timeline.json
├── candidate_clips.json
├── rough_cut.mp4
└── quality_report.json
```

## Event Timeline Contract

`event_timeline.json` is the main bridge between Kaggle and NarratoAI.

```json
{
  "video": "part2.mp4",
  "task_name": "part2_highlight",
  "events": [
    {
      "event_id": "evt_0001",
      "event_type": "death_fail",
      "time_range": "00:07:16,500-00:07:25,500",
      "score": 9.1,
      "confidence": 0.87,
      "visual_evidence": "角色被攻击后倒地，画面出现失败反馈",
      "subtitle_evidence": "又杀了",
      "recommended_clip": {
        "start": "00:07:14,500",
        "end": "00:07:27,000",
        "reason": "动作前留2秒，失败后保留主播反应"
      }
    }
  ]
}
```

Required event types for the first version:

```text
death_fail
strong_reaction
coop_command
puzzle_progress
live_interaction
transition
low_value
```

Scores:

- `score`: highlight value, 0 to 10
- `confidence`: model confidence, 0 to 1
- candidates below score 7 should normally not enter the edit

## Candidate Clips Contract

`candidate_clips.json` is the edit decision output from the video understanding
stage. It should already be close to NarratoAI's script format but still keep
model evidence for review.

```json
{
  "target_duration_seconds": [60, 90],
  "clips": [
    {
      "clip_id": "clip_0001",
      "source_event_ids": ["evt_0001"],
      "timestamp": "00:07:14,500-00:07:27,000",
      "role": "continuous_fail",
      "score": 9.1,
      "picture": "连续失败，主播吐槽，又杀了",
      "narration_hint": "",
      "OST": 1
    }
  ]
}
```

Use `OST=1` for live/game highlight segments where original audio carries the
main value. Narration can be added later by NarratoAI if a channel format needs
commentary.

## Prompt Layers

Keep prompts separated. Do not merge all goals into one large prompt.

### 1. Video Understanding Prompt

Purpose: describe visual facts and detect candidate events.

Must ask for:

- visible death/failure feedback
- jump/fall/missed action
- streamer strong reaction
- cooperative command or coordination
- puzzle/story progress
- live interaction or gift/task interruption
- low-value travel or idle moments
- visual evidence and timestamp range

Important rule:

```text
Do not select a segment only because the subtitle is funny. A selected event
must have visible action, feedback, reaction, or story progress in the frames.
```

### 2. Editing Decision Prompt

Purpose: turn events into a 60-90 second highlight structure.

Recommended structure:

```text
hook
-> continuous fail
-> streamer reaction
-> story/puzzle progress
-> final fail
```

Hard requirements for gaming live highlights:

```text
at least 3 death_fail events
at least 2 strong_reaction events
ending must be death_fail or coop_command failure
keep each clip short with 2-5 seconds context around the action
total duration must be 60-90 seconds
```

### 3. Quality Review Prompt

Purpose: decide if the candidate edit or final video is publishable.

Ask the model to score:

- visual-event/subtitle alignment
- density of clear events
- pacing
- whether the ending has a comment-worthy beat
- whether there are dead/boring segments
- whether audio and subtitles feel usable

Suggested output:

```json
{
  "publish_score": 8.4,
  "pass": true,
  "issues": [],
  "reasons": [
    "至少包含3次明确失败",
    "主播反应和画面事件对齐",
    "结尾有失败笑点"
  ]
}
```

Gate:

```text
publish_score >= 8.0: pass
7.0 <= publish_score < 8.0: manual review
publish_score < 7.0: reselect clips
```

## Two-Stage Filtering

Run filtering twice:

1. After keyframe understanding on Kaggle, filter low-value events before they
   become candidate clips.
2. After final video assembly in NarratoAI, re-analyze the produced video and
   decide whether it can be published.

If compute budget is tight, keep the first filter and make the final review
optional. If quality matters, both filters should run.

## NarratoAI Integration Points

Recommended files/modules:

- `app/services/documentary/frame_analysis_service.py`
  - keep Kaggle export/import responsibilities here
  - do not make it own all editing decisions
- `resource/kaggle/narratoai_kaggle_video_runner.py`
  - keep a script runner for Kaggle environments
  - notebook can import or mirror this logic
- `resource/kaggle/narratoai_video_understanding.ipynb`
  - add a runnable notebook for Kaggle users
- `app/services/video_event_service.py`
  - parse `event_timeline.json`
  - normalize event scores and timestamps
  - merge duplicate events
- `app/services/highlight_script_service.py`
  - convert candidate clips to `video_clip_json`
  - enforce duration and ordering
- `webui/components/script_settings.py`
  - expose export/import controls
  - show candidate events and clips for human confirmation

Do not bypass the WebUI for the final user workflow. Backend scripts are useful
for tests and development, but the feature should be repeatable from the WebUI.

## Minimal Implementation Checklist

1. Add local task package builder:
   - input video
   - optional SRT
   - `task_config.json`
   - `dataset-metadata.json`
   - Kaggle notebook
2. Add Kaggle Notebook:
   - install dependencies
   - load Qwen2VL/Qwen2.5-VL
   - extract coarse and refined frames
   - write `event_timeline.json`
   - write `candidate_clips.json`
3. Add local import:
   - read `event_timeline.json`
   - read `candidate_clips.json`
   - create `resource/scripts/<task>_highlight_project.json`
4. Add WebUI review:
   - list events
   - list candidate clips
   - allow select/delete/edit time ranges
   - generate script JSON
5. Add quality review:
   - run on final video or rough cut
   - write `quality_report.json`
   - block publish if score is below threshold

## Example for `part2.mp4`

For `resource/videos/part2.mp4`:

- coarse interval: `3`
- expected coarse frames: about `707`
- refine interval: `0.75`
- first target output: one 60-90 second game/live highlight
- expected events:
  - at least 3 death/fail beats
  - at least 2 strong streamer reactions
  - one puzzle/story progress segment
  - final clip ends on failure or cooperative mistake

The rough cut can be original-audio only. NarratoAI can later decide whether to
add narration, subtitles, BGM, or leave it as a live highlight.

## Non-Goals for the First Version

- fully automatic publishing without review
- perfect frame-accurate editing
- online Kaggle API serving through ngrok
- requiring Kaggle to render the final NarratoAI video
- replacing NarratoAI's TTS, subtitle, and final assembly pipeline

## Acceptance Criteria

The MVP is acceptable when:

- a local video can be packaged and uploaded as a Kaggle Dataset
- the Kaggle Notebook can run against the Dataset without manual code edits
- `event_timeline.json` and `candidate_clips.json` are produced
- NarratoAI imports candidate clips into a valid `video_clip_json`
- the WebUI can generate a final video from that script
- a quality report gives pass/manual-review/reject status
