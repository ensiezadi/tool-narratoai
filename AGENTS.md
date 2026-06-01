# NarratoAI — Development Guide

AI-powered automated film commentary tool (影视解说). Pipeline: extracted frames → narration copy (LLM) → TTS audio → video assembly.

## Project Entry Points

| File | Role |
|------|------|
| `webui.py` | Streamlit UI entry point — `main()` at line 368 registers LLM providers |
| `app/config/config.py` | Loads `config.toml`, auto-creates from `config.example.toml` if missing, handles `utf-8-sig` encoding |
| `app/config/defaults.py` | Default LLM provider/model values shared by bootstrap and WebUI |
| `conftest.py` | Pytest collection rules — skips `test_llm_service.py` and `test_openai_compatible_integration.py` from default suite |

## Config Auto-Creation

`config.toml` is auto-created from `config.example.toml` at startup if missing — no need to manually `cp config.example.toml config.toml`.

## LLM Provider Registration (Critical)

`LLMServiceManager` is empty until `webui.py:main()` runs `register_all_providers()`. Calling LLM services outside the WebUI flow requires an explicit call:

```python
from app.services.llm.providers import register_all_providers
register_all_providers()
```

Registered providers: `openai` (OpenAI-compatible, used for vision+text), `gemini` (vision only), `kaggle` (vision only).

## TTS Engine Dispatch (`voice.py:tts()`, line 1239)

| `tts_engine` config value | Actual engine |
|---------------------------|---------------|
| `edge_tts` | Edge TTS (Azure V1) — default fallback |
| `azure_speech` | Azure Speech Services V2 if voice matches `should_use_azure_speech_services`, else Edge TTS |
| `tencent_tts` | Tencent Cloud TTS |
| `qwen3_tts` | Qwen3 TTS via DashScope |
| `soulvoice` | SoulVoice TTS |
| `indextts2` | IndexTTS2 (voice cloning) |
| `doubaotts` | 豆包 TTS |
| `xiaomi_tts` | Xiaomi MiMo TTS (has voice-clone sub-mode) |

Voice name prefix stripping: `soulvoice:`, `tencent:`, `qwen3:` prefixes are stripped before SDK calls.

Engines **without** SRT generation (duration estimated from text): SoulVoice, Qwen3 TTS, IndexTTS2, 豆包.

## Task Pipeline (`app/services/task.py`)

`start_subclip()` orchestrates the full pipeline:
1. Load script JSON (`video_clip_json_path`)
2. Filter segments by `OST` flag (0=TTS only, 1=keep original, 2=TTS+original)
3. Call `voice.tts_multiple()` for OST 0/2 segments
4. Call `audio_merger.merge_audio_files()`
5. Call `subtitle_merger` + `clip_video` + `merger_video`

**OST semantics:** `0`=remove original sound, TTS only | `1`=keep original, no TTS | `2`=TTS + original mixed

## Script Generation Flow

```
webui → script_service.py:ScriptGenerator
  → DocumentaryFrameAnalysisService
  → generate_narration_script.py:generate_narration()
  → migration_adapter.py:LegacyLLMAdapter.generate_narration()
  → PromptManager.get_prompt(category="documentary", name="narration_generation")
  → NarrationGenerationPrompt (v2.0) → LLM → JSON
```

Output schema: `{"items": [{ "_id", "timestamp", "picture", "narration" }]}`

## Kaggle Video Understanding Workflow

For game/live-stream highlight editing, Kaggle should be used as an offline GPU
video-understanding layer, not as an online API server or final video assembler.
The intended flow is:

```text
local full video → Kaggle Dataset → Kaggle Notebook/Qwen2VL
  → event_timeline.json + candidate_clips.json
  → NarratoAI import → WebUI confirmation → final assembly → quality review
```

Read the detailed implementation plan before changing this area:
`docs/kaggle-video-understanding-workflow.md`.

## Running

```bash
streamlit run webui.py --server.maxUploadSize=2048   # local
make deploy        # docker (first time — builds image)
make up            # docker-compose up -d
make logs          # docker-compose logs -f
make shell         # docker-compose exec narratoai-webui bash

pytest tests/ -q -n 4    # unit tests only (integration tests in collect-ignore)
make config         # bootstrap config.toml if missing (calls cp itself)
```

> **Docker first time**: `make deploy` runs `docker-deploy.sh` which requires execution permission. If it fails, run `chmod +x docker-deploy.sh` first.

## Known Constraints

- **No `pyproject.toml`** — uses `requirements.txt` directly; no Poetry/uv lockfile
- **Integration tests skipped** in CI: `conftest.py` adds `test_llm_service.py` and `test_openai_compatible_integration.py` to `collect_ignore` — these need live credentials
- **Config encoding**: `config.toml` may be `utf-8-sig` (Excel-edited on Windows); `load_toml_file()` handles this automatically
- **Loguru level**: `webui.py:init_log()` sets INFO by default, filtering torch DEBUG noise
- **MoviePy optional**: if not installed, audio duration falls back to file-size estimation (`voice.py:get_audio_duration_from_file()`)
- **ImageMagick/FFmpeg env**: `config.py` sets `IMAGEMAGICK_BINARY` and `IMAGEIO_FFMPEG_EXE` from config if paths are valid files
- **Version source**: `project_version` file in repo root is read at runtime; version is NOT stored in `config.toml`
- **Config auto-creation**: `config.toml` is created from `config.example.toml` automatically on first run — never manually run `cp config.example.toml config.toml`
