import asyncio
import json
import os
import re
from datetime import datetime
from typing import Any, Callable

from loguru import logger

from app.config import config
from app.services.documentary.frame_analysis_models import FrameBatchResult
from app.services.edit_decision import normalize_edit_script
from app.services.generate_narration_script import generate_narration, parse_frame_analysis_to_markdown
from app.services.llm.migration_adapter import create_vision_analyzer
from app.utils import utils, video_processor


class DocumentaryFrameAnalysisService:
    PROMPT_TEMPLATE = """
我提供了 {frame_count} 张视频帧，它们按时间顺序排列，代表一个连续的视频片段。
首先，请详细描述每一帧的关键视觉信息（包含：主要内容、人物、动作和场景）。
然后，基于所有帧的分析，请用简洁的语言总结整个视频片段中发生的主要活动或事件流程。
请务必使用 JSON 格式输出。
JSON 必须包含以下键：
- frame_observations: 数组，且长度必须为 {frame_count}
- overall_activity_summary: 字符串，描述整个批次主要活动
示例结构：
{{
  "frame_observations": [
    {{"timestamp": "00:00:00,000", "observation": "画面描述"}}
  ],
  "overall_activity_summary": "本批次主要活动总结"
}}
请务必不要遗漏视频帧，我提供了 {frame_count} 张视频帧，frame_observations 必须包含 {frame_count} 个元素
请只返回 JSON 字符串，不要附加解释文字。
""".strip()

    async def generate_documentary_script(
        self,
        *,
        video_path: str,
        video_theme: str = "",
        custom_prompt: str = "",
        frame_interval_input: int | float | None = None,
        vision_batch_size: int | None = None,
        vision_llm_provider: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        vision_api_key: str | None = None,
        vision_model_name: str | None = None,
        vision_base_url: str | None = None,
        max_concurrency: int | None = None,
        enable_checkpoint: bool = False,
    ) -> list[dict]:
        progress = progress_callback or (lambda _p, _m: None)
        analysis_result = await self.analyze_video(
            video_path=video_path,
            video_theme=video_theme,
            custom_prompt=custom_prompt,
            frame_interval_input=frame_interval_input,
            vision_batch_size=vision_batch_size,
            vision_llm_provider=vision_llm_provider,
            progress_callback=progress_callback,
            vision_api_key=vision_api_key,
            vision_model_name=vision_model_name,
            vision_base_url=vision_base_url,
            max_concurrency=max_concurrency,
            enable_checkpoint=enable_checkpoint,
        )
        analysis_json_path = analysis_result["analysis_json_path"]

        progress(80, "正在生成解说文案...")
        text_provider = config.app.get("text_llm_provider", "openai").lower()
        text_api_key = config.app.get(f"text_{text_provider}_api_key")
        text_model = config.app.get(f"text_{text_provider}_model_name")
        text_base_url = config.app.get(f"text_{text_provider}_base_url")
        if not text_api_key or not text_model:
            raise ValueError(
                f"未配置 {text_provider} 的文本模型参数。"
                f"请在设置中配置 text_{text_provider}_api_key 和 text_{text_provider}_model_name"
            )

        markdown_output = parse_frame_analysis_to_markdown(analysis_json_path)
        narration_input = self._build_narration_input(
            markdown_output=markdown_output,
            video_theme=video_theme,
            custom_prompt=custom_prompt,
        )
        narration_raw = generate_narration(
            narration_input,
            text_api_key,
            base_url=text_base_url,
            model=text_model,
        )
        narration_items = self._parse_narration_items(narration_raw)

        final_script = normalize_edit_script([{**item, "OST": 2} for item in narration_items])
        progress(100, "脚本生成完成")
        return final_script

    async def analyze_video(
        self,
        *,
        video_path: str,
        video_theme: str = "",
        custom_prompt: str = "",
        frame_interval_input: int | float | None = None,
        vision_batch_size: int | None = None,
        vision_llm_provider: str | None = None,
        progress_callback: Callable[[float, str], None] | None = None,
        vision_api_key: str | None = None,
        vision_model_name: str | None = None,
        vision_base_url: str | None = None,
        max_concurrency: int | None = None,
        enable_checkpoint: bool = False,
    ) -> dict[str, Any]:
        progress = progress_callback or (lambda _p, _m: None)

        if not video_path or not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")

        frame_interval_seconds = self._resolve_frame_interval(frame_interval_input)
        batch_size = self._resolve_batch_size(vision_batch_size)
        concurrency = self._resolve_max_concurrency(max_concurrency)
        provider = (vision_llm_provider or config.app.get("vision_llm_provider", "openai")).lower()

        api_key = vision_api_key if vision_api_key is not None else config.app.get(f"vision_{provider}_api_key")
        model_name = (
            vision_model_name if vision_model_name is not None else config.app.get(f"vision_{provider}_model_name")
        )
        base_url = vision_base_url if vision_base_url is not None else config.app.get(f"vision_{provider}_base_url", "")
        if not api_key or not model_name:
            raise ValueError(
                f"未配置 {provider} 的 API Key 或模型名称。"
                f"请在设置中配置 vision_{provider}_api_key 和 vision_{provider}_model_name"
            )

        progress(10, "正在提取关键帧...")
        keyframe_files = self._load_or_extract_keyframes(video_path, frame_interval_seconds)
        progress(25, f"关键帧准备完成，共 {len(keyframe_files)} 帧")

        checkpoint_dir = None
        if enable_checkpoint:
            checkpoint_dir = self._get_checkpoint_dir(video_path, frame_interval_seconds, batch_size)
            summary = self._load_checkpoint_summary(checkpoint_dir)
            if summary:
                completed = {idx for idx, info in summary.items() if info.get("status") in ("success", "failed")}
                progress(25, f"检测到断点：已完成 {len(completed)}/{len(keyframe_files)} 帧，将跳过已分析批次")

        progress(30, "正在初始化视觉分析器...")
        analyzer = create_vision_analyzer(
            provider=provider,
            api_key=api_key,
            model=model_name,
            base_url=base_url,
        )

        batches = self._chunk_keyframes(keyframe_files, batch_size=batch_size)
        if not batches:
            raise RuntimeError("未能构建任何关键帧批次")

        progress(40, f"正在分析关键帧，共 {len(batches)} 个批次...")
        batch_results = await self._analyze_batches_resumable(
            analyzer=analyzer,
            batches=batches,
            custom_prompt=custom_prompt,
            video_theme=video_theme,
            max_concurrency=concurrency,
            progress_callback=progress,
            checkpoint_dir=checkpoint_dir,
        )

        progress(65, "正在整理分析结果...")
        sorted_batches = self._sort_batch_results(batch_results)
        artifact = self._build_analysis_artifact(
            sorted_batches,
            video_path=video_path,
            frame_interval_seconds=frame_interval_seconds,
            vision_batch_size=batch_size,
            vision_llm_provider=provider,
            vision_model_name=model_name,
            max_concurrency=concurrency,
        )
        analysis_json_path = self._save_analysis_artifact(artifact)
        video_clip_json = self._build_video_clip_json(sorted_batches)

        progress(75, "逐帧分析完成")
        return {
            "analysis_json_path": analysis_json_path,
            "analysis_artifact": artifact,
            "video_clip_json": video_clip_json,
            "keyframe_files": keyframe_files,
        }

    def _parse_narration_items(self, narration_raw: str) -> list[dict[str, Any]]:
        parsed = self._repair_narration_payload(narration_raw)

        items: list[dict[str, Any]] = []
        if isinstance(parsed, dict):
            raw_items = parsed.get("items")
            if isinstance(raw_items, list):
                items = [item for item in raw_items if isinstance(item, dict)]

        if not items:
            raise ValueError("解说文案格式错误，无法解析JSON或缺少items字段")

        return items

    def _build_narration_input(self, *, markdown_output: str, video_theme: str, custom_prompt: str) -> str:
        context_lines: list[str] = []
        if (video_theme or "").strip():
            context_lines.append(f"视频主题：{video_theme.strip()}")
        if (custom_prompt or "").strip():
            context_lines.append(f"补充创作要求：{custom_prompt.strip()}")

        if not context_lines:
            return markdown_output

        context_block = "\n".join(f"- {line}" for line in context_lines)
        return f"{markdown_output.rstrip()}\n\n## 创作上下文\n{context_block}\n"

    def _repair_narration_payload(self, narration_raw: str) -> dict[str, Any] | None:
        def load_json_candidate(payload: str) -> dict[str, Any] | None:
            try:
                parsed = json.loads(payload)
                return parsed if isinstance(parsed, dict) else None
            except Exception:
                return None

        cleaned = (narration_raw or "").strip()
        if not cleaned:
            return None

        candidates: list[str] = [cleaned]
        candidates.append(cleaned.replace("{{", "{").replace("}}", "}"))

        json_block = re.search(r"```json\s*(.*?)\s*```", cleaned, re.DOTALL)
        if json_block:
            candidates.append(json_block.group(1).strip())

        start = cleaned.find("{")
        end = cleaned.rfind("}")
        if start >= 0 and end > start:
            candidates.append(cleaned[start : end + 1])

        for candidate in candidates:
            parsed = load_json_candidate(candidate)
            if parsed is not None:
                return parsed

        fixed = cleaned.replace("{{", "{").replace("}}", "}")
        fixed_start = fixed.find("{")
        fixed_end = fixed.rfind("}")
        if fixed_start >= 0 and fixed_end > fixed_start:
            fixed = fixed[fixed_start : fixed_end + 1]

        fixed = re.sub(r"^\s*#.*$", "", fixed, flags=re.MULTILINE)
        fixed = re.sub(r"^\s*//.*$", "", fixed, flags=re.MULTILINE)
        fixed = re.sub(r",\s*}", "}", fixed)
        fixed = re.sub(r",\s*]", "]", fixed)
        fixed = re.sub(r"'([^']*)'\s*:", r'"\1":', fixed)
        fixed = re.sub(r'([{\[,]\s*)([A-Za-z_][\w\u4e00-\u9fff]*)(\s*:)', r'\1"\2"\3', fixed)
        fixed = re.sub(r'""([^"]*?)""', r'"\1"', fixed)

        return load_json_candidate(fixed)

    def _resolve_frame_interval(self, frame_interval_input: int | float | None) -> float:
        interval = frame_interval_input
        if interval in (None, ""):
            interval = config.frames.get("frame_interval_input", 3)
        try:
            value = float(interval)
        except (TypeError, ValueError):
            value = 3.0
        if value <= 0:
            raise ValueError("frame_interval_input must be > 0")
        return value

    def _resolve_batch_size(self, vision_batch_size: int | None) -> int:
        size = vision_batch_size or config.frames.get("vision_batch_size", 10)
        try:
            value = int(size)
        except (TypeError, ValueError):
            value = 10
        if value <= 0:
            raise ValueError("vision_batch_size must be > 0")
        return value

    def _resolve_max_concurrency(self, max_concurrency: int | None) -> int:
        value = max_concurrency if max_concurrency is not None else config.frames.get("vision_max_concurrency", 2)
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            parsed = 1
        return max(1, parsed)

    def _load_or_extract_keyframes(self, video_path: str, frame_interval_seconds: float) -> list[str]:
        keyframes_root = os.path.join(utils.temp_dir(), "keyframes")
        os.makedirs(keyframes_root, exist_ok=True)
        cache_key = self._build_keyframe_cache_key(video_path, frame_interval_seconds)
        cache_dir = os.path.join(keyframes_root, cache_key)
        os.makedirs(cache_dir, exist_ok=True)

        cached_files = self._collect_keyframe_paths(cache_dir)
        if cached_files:
            logger.info(f"使用已缓存关键帧: {cache_dir}, 共 {len(cached_files)} 帧")
            return cached_files

        processor = video_processor.VideoProcessor(video_path)
        extracted = processor.extract_frames_by_interval_with_fallback(
            output_dir=cache_dir,
            interval_seconds=frame_interval_seconds,
        )
        keyframe_files = sorted(str(path) for path in extracted if str(path).endswith(".jpg"))
        if not keyframe_files:
            keyframe_files = self._collect_keyframe_paths(cache_dir)
        if not keyframe_files:
            raise RuntimeError("未提取到任何关键帧")

        logger.info(f"关键帧提取完成: {cache_dir}, 共 {len(keyframe_files)} 帧")
        return keyframe_files

    def _build_keyframe_cache_key(self, video_path: str, frame_interval_seconds: float) -> str:
        try:
            video_mtime = os.path.getmtime(video_path)
        except OSError:
            video_mtime = 0

        legacy_prefix = utils.md5(f"{video_path}{video_mtime}")
        payload = "|".join(
            [
                str(video_path),
                str(video_mtime),
                str(frame_interval_seconds),
                "documentary-keyframes-v2",
            ]
        )
        return f"{legacy_prefix}_{utils.md5(payload)}"

    @staticmethod
    def _collect_keyframe_paths(cache_dir: str) -> list[str]:
        if not os.path.exists(cache_dir):
            return []
        return sorted(
            os.path.join(cache_dir, name)
            for name in os.listdir(cache_dir)
            if re.fullmatch(r"keyframe_\d{6}_\d{9}\.jpg", name)
        )

    @staticmethod
    def _chunk_keyframes(keyframe_files: list[str], batch_size: int) -> list[list[str]]:
        return [keyframe_files[index : index + batch_size] for index in range(0, len(keyframe_files), batch_size)]

    async def _analyze_batches(
        self,
        *,
        analyzer: Any,
        batches: list[list[str]],
        custom_prompt: str,
        video_theme: str,
        max_concurrency: int,
        progress_callback: Callable[[float, str], None],
    ) -> list[FrameBatchResult]:
        semaphore = asyncio.Semaphore(max(1, max_concurrency))
        total = len(batches)
        done = 0
        done_lock = asyncio.Lock()

        batch_time_ranges: list[str] = []
        previous_batch_files: list[str] | None = None
        for batch_files in batches:
            _, _, time_range = self._get_batch_timestamps(batch_files, previous_batch_files)
            batch_time_ranges.append(time_range)
            previous_batch_files = batch_files

        async def run_single(batch_index: int, frame_paths: list[str], time_range: str) -> FrameBatchResult:
            nonlocal done
            prompt = self._build_batch_prompt(
                frame_count=len(frame_paths),
                video_theme=video_theme,
                custom_prompt=custom_prompt,
            )
            try:
                async with semaphore:
                    raw_results = await analyzer.analyze_images(
                        images=frame_paths,
                        prompt=prompt,
                        batch_size=max(1, len(frame_paths)),
                        max_concurrency=1,
                    )
                raw_response, error_message = self._extract_batch_response(raw_results)
                if error_message:
                    return self._build_failed_batch_result(
                        batch_index=batch_index,
                        raw_response=raw_response,
                        error_message=error_message,
                        frame_paths=frame_paths,
                        time_range=time_range,
                    )
                return self._parse_batch_response(
                    batch_index=batch_index,
                    raw_response=raw_response,
                    frame_paths=frame_paths,
                    time_range=time_range,
                )
            except Exception as exc:
                return self._build_failed_batch_result(
                    batch_index=batch_index,
                    raw_response="",
                    error_message=str(exc),
                    frame_paths=frame_paths,
                    time_range=time_range,
                )
            finally:
                async with done_lock:
                    done += 1
                    progress = 40 + (done / max(1, total)) * 25
                    progress_callback(progress, f"正在分析关键帧批次 ({done}/{total})...")

        tasks = [
            run_single(batch_index=index, frame_paths=batch_files, time_range=batch_time_ranges[index])
            for index, batch_files in enumerate(batches)
        ]
        return await asyncio.gather(*tasks)

    def _build_batch_prompt(self, *, frame_count: int, video_theme: str, custom_prompt: str) -> str:
        prompt = self._build_analysis_prompt(frame_count=frame_count)
        extra_lines: list[str] = []
        if (video_theme or "").strip():
            extra_lines.append(f"视频主题：{video_theme.strip()}")
        if (custom_prompt or "").strip():
            extra_lines.append(custom_prompt.strip())
        if not extra_lines:
            return prompt

        extras = "\n".join(f"- {line}" for line in extra_lines)
        return f"{prompt}\n\n补充分析要求：\n{extras}"

    def _extract_batch_response(self, raw_results: Any) -> tuple[str, str]:
        if not raw_results:
            return "", "Batch response is empty"

        first_result = raw_results[0] if isinstance(raw_results, list) else raw_results
        if isinstance(first_result, dict):
            raw_response = str(first_result.get("response", "") or "")
            error_message = str(first_result.get("error", "") or "")
            if error_message:
                if not raw_response:
                    raw_response = error_message
                return raw_response, error_message
            if not raw_response.strip():
                return raw_response, "Batch response is empty"
            return raw_response, ""

        raw_response = str(first_result or "")
        if not raw_response.strip():
            return raw_response, "Batch response is empty"
        return raw_response, ""

    def _sort_batch_results(self, batch_results: list[FrameBatchResult]) -> list[FrameBatchResult]:
        return sorted(batch_results, key=lambda item: (self._time_range_sort_key(item.time_range), item.batch_index))

    def _build_analysis_artifact(
        self,
        batch_results: list[FrameBatchResult],
        *,
        video_path: str,
        frame_interval_seconds: float,
        vision_batch_size: int,
        vision_llm_provider: str,
        vision_model_name: str,
        max_concurrency: int,
    ) -> dict[str, Any]:
        sorted_batches = self._sort_batch_results(batch_results)

        batch_dicts: list[dict[str, Any]] = []
        frame_observations: list[dict[str, Any]] = []
        overall_activity_summaries: list[dict[str, Any]] = []

        for batch in sorted_batches:
            batch_payload = {
                "batch_index": batch.batch_index,
                "status": batch.status,
                "time_range": batch.time_range,
                "raw_response": batch.raw_response,
                "frame_paths": list(batch.frame_paths),
                "frame_observations": list(batch.frame_observations),
                "overall_activity_summary": batch.overall_activity_summary,
                "fallback_summary": batch.fallback_summary,
                "error_message": batch.error_message,
            }
            batch_dicts.append(batch_payload)

            for observation in batch.frame_observations:
                observation_payload = dict(observation)
                observation_payload["batch_index"] = batch.batch_index
                observation_payload["time_range"] = batch.time_range
                frame_observations.append(observation_payload)

            summary_text = (batch.overall_activity_summary or batch.fallback_summary or "").strip()
            if summary_text:
                overall_activity_summaries.append(
                    {
                        "batch_index": batch.batch_index,
                        "time_range": batch.time_range,
                        "summary": summary_text,
                    }
                )

        return {
            "artifact_version": "documentary-frame-analysis-v2",
            "generated_at": datetime.now().isoformat(),
            "video_path": video_path,
            "frame_interval_seconds": frame_interval_seconds,
            "vision_batch_size": vision_batch_size,
            "vision_llm_provider": vision_llm_provider,
            "vision_model_name": vision_model_name,
            "vision_max_concurrency": max_concurrency,
            "batches": batch_dicts,
            # 向后兼容旧解析器结构
            "frame_observations": frame_observations,
            "overall_activity_summaries": overall_activity_summaries,
        }

    def _save_analysis_artifact(self, artifact: dict[str, Any]) -> str:
        analysis_dir = os.path.join(utils.storage_dir(), "temp", "analysis")
        os.makedirs(analysis_dir, exist_ok=True)

        filename = f"frame_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
        file_path = os.path.join(analysis_dir, filename)
        suffix = 1
        while os.path.exists(file_path):
            filename = f"frame_analysis_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{suffix:02d}.json"
            file_path = os.path.join(analysis_dir, filename)
            suffix += 1

        with open(file_path, "w", encoding="utf-8") as fp:
            json.dump(artifact, fp, ensure_ascii=False, indent=2)
        logger.info(f"分析结果已保存到: {file_path}")
        return file_path

    # ============================================================
    # 断点续传：checkpoint 管理
    # ============================================================

    def _get_checkpoint_dir(self, video_path: str, frame_interval: float, batch_size: int) -> str:
        """获取/创建 checkpoint 目录，按视频+参数唯一定位"""
        try:
            video_mtime = os.path.getmtime(video_path)
        except OSError:
            video_mtime = 0
        video_name = os.path.basename(video_path)
        cache_key = utils.md5(f"{video_name}{video_mtime}{frame_interval}{batch_size}")
        checkpoint_root = os.path.join(utils.temp_dir(), "checkpoints", cache_key)
        os.makedirs(checkpoint_root, exist_ok=True)
        return checkpoint_root

    def _get_checkpoint_summary_path(self, checkpoint_dir: str) -> str:
        return os.path.join(checkpoint_dir, "manifest.jsonl")

    def _get_batch_checkpoint_path(self, checkpoint_dir: str, batch_index: int) -> str:
        return os.path.join(checkpoint_dir, f"batch_{batch_index:04d}.json")

    def _load_checkpoint_summary(self, checkpoint_dir: str) -> dict[int, dict]:
        """
        加载已完成的批次摘要。返回 {batch_index: {status, time_range, ...}}
        """
        summary_path = self._get_checkpoint_summary_path(checkpoint_dir)
        if not os.path.exists(summary_path):
            return {}
        done: dict[int, dict] = {}
        try:
            with open(summary_path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    entry = json.loads(line)
                    done[entry["batch_index"]] = entry
        except Exception as e:
            logger.warning(f"加载 checkpoint 摘要失败: {e}")
        return done

    def _append_batch_checkpoint(self, checkpoint_dir: str, batch_result: FrameBatchResult) -> None:
        """追加单批次结果到 checkpoint 文件（JSON Lines 格式）"""
        batch_path = self._get_batch_checkpoint_path(checkpoint_dir, batch_result.batch_index)
        batch_data = {
            "batch_index": batch_result.batch_index,
            "status": batch_result.status,
            "time_range": batch_result.time_range,
            "raw_response": batch_result.raw_response,
            "frame_paths": list(batch_result.frame_paths),
            "frame_observations": batch_result.frame_observations,
            "overall_activity_summary": batch_result.overall_activity_summary,
            "fallback_summary": batch_result.fallback_summary,
            "error_message": batch_result.error_message,
        }
        with open(batch_path, "w", encoding="utf-8") as f:
            json.dump(batch_data, f, ensure_ascii=False, indent=2)

        with open(self._get_checkpoint_summary_path(checkpoint_dir), "a", encoding="utf-8") as f:
            f.write(json.dumps({"batch_index": batch_result.batch_index, "status": batch_result.status, "time_range": batch_result.time_range}, ensure_ascii=False) + "\n")

    def _load_batch_from_checkpoint(self, checkpoint_dir: str, batch_index: int) -> FrameBatchResult | None:
        """从 checkpoint 加载单批次结果"""
        batch_path = self._get_batch_checkpoint_path(checkpoint_dir, batch_index)
        if not os.path.exists(batch_path):
            return None
        with open(batch_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return FrameBatchResult(
            batch_index=data["batch_index"],
            status=data.get("status", "success"),
            time_range=data.get("time_range", ""),
            raw_response=data.get("raw_response", ""),
            frame_paths=data.get("frame_paths", []),
            frame_observations=data.get("frame_observations", []),
            overall_activity_summary=data.get("overall_activity_summary", ""),
            fallback_summary=data.get("fallback_summary", ""),
            error_message=data.get("error_message", ""),
        )

    def _get_completed_batches_from_checkpoint(self, checkpoint_dir: str) -> set[int]:
        """获取已完成的批次索引集合"""
        summary = self._load_checkpoint_summary(checkpoint_dir)
        return {idx for idx, info in summary.items() if info.get("status") in ("success", "failed")}

    def get_checkpoint_status(self, video_path: str, frame_interval: float, batch_size: int) -> dict:
        """
        返回指定视频的断点状态。
        Returns: {
            "exists": bool,
            "total_batches": int,
            "completed_batches": int,
            "completed_indices": list[int],
            "checkpoint_dir": str,
        }
        """
        checkpoint_dir = self._get_checkpoint_dir(video_path, frame_interval, batch_size)
        summary = self._load_checkpoint_summary(checkpoint_dir)
        completed = {idx for idx, info in summary.items() if info.get("status") in ("success", "failed")}
        keyframe_files = self._load_or_extract_keyframes(video_path, frame_interval)
        from app.services.documentary.frame_analysis_models import FrameBatchResult
        batches = self._chunk_keyframes(keyframe_files, batch_size=batch_size)
        return {
            "exists": len(summary) > 0,
            "total_batches": len(batches),
            "completed_batches": len(completed),
            "completed_indices": sorted(completed),
            "checkpoint_dir": checkpoint_dir,
        }

    def clear_checkpoint(self, video_path: str, frame_interval: float, batch_size: int) -> bool:
        """清除指定视频的 checkpoint"""
        checkpoint_dir = self._get_checkpoint_dir(video_path, frame_interval, batch_size)
        import shutil
        if os.path.exists(checkpoint_dir):
            shutil.rmtree(checkpoint_dir)
            logger.info(f"已清除 checkpoint: {checkpoint_dir}")
            return True
        return False

    # ============================================================
    # 断点续传版批次分析
    # ============================================================

    async def _analyze_batches_resumable(
        self,
        *,
        analyzer: Any,
        batches: list[list[str]],
        custom_prompt: str,
        video_theme: str,
        max_concurrency: int,
        progress_callback: Callable[[float, str], None],
        checkpoint_dir: str | None = None,
        start_batch_index: int = 0,
    ) -> list[FrameBatchResult]:
        """
        支持断点续传的批次分析。
        - checkpoint_dir 不为空时：每批处理完立即落盘，跳过已完成批次
        - start_batch_index：从此批次开始（用于从特定位置恢复）
        """
        semaphore = asyncio.Semaphore(max(1, max_concurrency))
        total = len(batches)
        done = 0
        done_lock = asyncio.Lock()

        completed_indices: set[int] = set()
        if checkpoint_dir:
            os.makedirs(checkpoint_dir, exist_ok=True)
            completed_indices = self._get_completed_batches_from_checkpoint(checkpoint_dir)
            if completed_indices:
                logger.info(f"检测到 checkpoint，共 {len(completed_indices)} 个批次已完成，将跳过")

        batch_time_ranges: list[str] = []
        previous_batch_files: list[str] | None = None
        for batch_files in batches:
            _, _, time_range = self._get_batch_timestamps(batch_files, previous_batch_files)
            batch_time_ranges.append(time_range)
            previous_batch_files = batch_files

        async def run_single(batch_index: int, frame_paths: list[str], time_range: str) -> FrameBatchResult:
            nonlocal done
            if checkpoint_dir and batch_index in completed_indices:
                result = self._load_batch_from_checkpoint(checkpoint_dir, batch_index)
                if result:
                    async with done_lock:
                        done += 1
                        progress_callback(
                            (done / max(1, total)) * 100,
                            f"已跳过批次 {done}/{total}（来自 checkpoint）...",
                        )
                    return result

            prompt = self._build_batch_prompt(
                frame_count=len(frame_paths),
                video_theme=video_theme,
                custom_prompt=custom_prompt,
            )
            try:
                async with semaphore:
                    raw_results = await analyzer.analyze_images(
                        images=frame_paths,
                        prompt=prompt,
                        batch_size=max(1, len(frame_paths)),
                        max_concurrency=1,
                    )
                raw_response, error_message = self._extract_batch_response(raw_results)
                if error_message:
                    result = self._build_failed_batch_result(
                        batch_index=batch_index,
                        raw_response=raw_response,
                        error_message=error_message,
                        frame_paths=frame_paths,
                        time_range=time_range,
                    )
                else:
                    result = self._parse_batch_response(
                        batch_index=batch_index,
                        raw_response=raw_response,
                        frame_paths=frame_paths,
                        time_range=time_range,
                    )
            except Exception as exc:
                result = self._build_failed_batch_result(
                    batch_index=batch_index,
                    raw_response="",
                    error_message=str(exc),
                    frame_paths=frame_paths,
                    time_range=time_range,
                )

            if checkpoint_dir:
                self._append_batch_checkpoint(checkpoint_dir, result)

            async with done_lock:
                done += 1
                progress_callback(
                    (done / max(1, total)) * 100,
                    f"已处理批次 {done}/{total}（{time_range}）...",
                )
            return result

        tasks = [
            run_single(batch_index=index, frame_paths=batch_files, time_range=batch_time_ranges[index])
            for index, batch_files in enumerate(batches)
            if index >= start_batch_index
        ]
        results = await asyncio.gather(*tasks)

        if checkpoint_dir and start_batch_index > 0:
            for idx in range(start_batch_index):
                if idx not in completed_indices:
                    r = self._load_batch_from_checkpoint(checkpoint_dir, idx)
                    if r:
                        results.insert(idx, r)

        return results

    def _build_video_clip_json(self, batch_results: list[FrameBatchResult]) -> list[dict]:
        clips: list[dict] = []
        for batch in self._sort_batch_results(batch_results):
            picture = self._build_batch_picture(batch)
            clips.append(
                {
                    "timestamp": batch.time_range,
                    "picture": picture,
                    "narration": "",
                    "OST": 2,
                }
            )
        return clips

    def _build_batch_picture(self, batch: FrameBatchResult) -> str:
        summary = (batch.overall_activity_summary or "").strip()
        if summary:
            return summary

        fallback = (batch.fallback_summary or "").strip()
        if fallback:
            return fallback

        observation_lines = []
        for frame in batch.frame_observations:
            timestamp = str(frame.get("timestamp", "") or "").strip()
            observation = str(frame.get("observation", "") or "").strip()
            if timestamp and observation:
                observation_lines.append(f"{timestamp}: {observation}")
            elif observation:
                observation_lines.append(observation)
        if observation_lines:
            return " ".join(observation_lines)

        raw_response = (batch.raw_response or "").strip()
        if raw_response:
            return raw_response[:200]
        return "该批次分析失败，未返回可用描述。"

    def _time_range_sort_key(self, time_range: str) -> tuple[int, str]:
        start = (time_range or "").split("-", 1)[0].strip()
        return self._timestamp_to_milliseconds(start), time_range

    @staticmethod
    def _timestamp_to_milliseconds(timestamp: str) -> int:
        text = (timestamp or "").strip()
        try:
            if "," in text:
                time_part, ms_part = text.split(",", 1)
                milliseconds = int(ms_part)
            else:
                time_part = text
                milliseconds = 0

            parts = [int(part) for part in time_part.split(":") if part]
            while len(parts) < 3:
                parts.insert(0, 0)
            hours, minutes, seconds = parts[-3], parts[-2], parts[-1]
            return ((hours * 3600 + minutes * 60 + seconds) * 1000) + milliseconds
        except Exception:
            return 0

    def _get_batch_timestamps(
        self,
        batch_files: list[str],
        prev_batch_files: list[str] | None = None,
    ) -> tuple[str, str, str]:
        if not batch_files:
            return "00:00:00,000", "00:00:00,000", "00:00:00,000-00:00:00,000"

        if len(batch_files) == 1 and prev_batch_files:
            first_frame = os.path.basename(prev_batch_files[-1])
            last_frame = os.path.basename(batch_files[0])
        else:
            first_frame = os.path.basename(batch_files[0])
            last_frame = os.path.basename(batch_files[-1])

        first_timestamp = self._timestamp_from_keyframe_name(first_frame)
        last_timestamp = self._timestamp_from_keyframe_name(last_frame)
        return first_timestamp, last_timestamp, f"{first_timestamp}-{last_timestamp}"

    def _timestamp_from_keyframe_name(self, filename: str) -> str:
        match = re.search(r"keyframe_\d{6}_(\d{9})\.jpg$", filename)
        if not match:
            return "00:00:00,000"
        token = match.group(1)
        hours = int(token[0:2])
        minutes = int(token[2:4])
        seconds = int(token[4:6])
        milliseconds = int(token[6:9])
        return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"

    def _build_analysis_prompt(self, frame_count: int) -> str:
        return self.PROMPT_TEMPLATE.format(frame_count=frame_count)

    def _build_failed_batch_result(
        self,
        *,
        batch_index: int,
        raw_response: str,
        error_message: str,
        frame_paths: list[str],
        time_range: str,
    ) -> FrameBatchResult:
        fallback_summary = (raw_response or "").strip()[:200]
        if not fallback_summary:
            fallback_summary = f"Batch {batch_index} analysis failed: {error_message or 'unknown error'}"

        return FrameBatchResult(
            batch_index=batch_index,
            status="failed",
            time_range=time_range,
            raw_response=raw_response,
            frame_paths=list(frame_paths),
            fallback_summary=fallback_summary,
            error_message=error_message,
        )

    def _build_cache_key(
        self,
        video_path: str,
        interval_seconds: float,
        prompt_version: str,
        model_name: str,
        batch_size: int,
        max_concurrency: int,
    ) -> str:
        try:
            video_mtime = os.path.getmtime(video_path)
        except OSError:
            video_mtime = 0

        legacy_prefix = utils.md5(f"{video_path}{video_mtime}")

        payload = "|".join(
            [
                str(video_path),
                str(video_mtime),
                str(interval_seconds),
                str(prompt_version),
                str(model_name),
                str(batch_size),
                str(max_concurrency),
                "documentary-frame-analysis-v2",
            ]
        )
        return f"{legacy_prefix}_{utils.md5(payload)}"

    def _strip_code_fence(self, response_text: str) -> str:
        cleaned = (response_text or "").strip()
        cleaned = re.sub(r"^```[a-zA-Z0-9_-]*\s*", "", cleaned)
        cleaned = re.sub(r"\s*```$", "", cleaned)
        return cleaned.strip()

    def _parse_batch_response(
        self,
        *,
        batch_index: int,
        raw_response: str,
        frame_paths: list[str],
        time_range: str,
    ) -> FrameBatchResult:
        try:
            payload = json.loads(self._strip_code_fence(raw_response))
        except Exception as exc:
            return self._build_failed_batch_result(
                batch_index=batch_index,
                raw_response=raw_response,
                error_message=str(exc),
                frame_paths=frame_paths,
                time_range=time_range,
            )

        validation_error = self._validate_batch_payload_contract(payload, expected_frame_count=len(frame_paths))
        if validation_error:
            return self._build_failed_batch_result(
                batch_index=batch_index,
                raw_response=raw_response,
                error_message=validation_error,
                frame_paths=frame_paths,
                time_range=time_range,
            )

        raw_observations = payload["frame_observations"]

        frame_observations: list[dict] = []
        for index, frame_path in enumerate(frame_paths):
            entry = raw_observations[index] if index < len(raw_observations) else {}
            if isinstance(entry, dict):
                observation = str(entry.get("observation", "") or "")
                timestamp = str(entry.get("timestamp", "") or "")
            else:
                observation = str(entry or "")
                timestamp = ""
            frame_observations.append(
                {
                    "frame_path": frame_path,
                    "timestamp": timestamp,
                    "observation": observation,
                }
            )

        raw_summary = payload.get("overall_activity_summary", "")
        if isinstance(raw_summary, str):
            summary = raw_summary
        elif raw_summary is None:
            summary = ""
        else:
            summary = str(raw_summary)

        return FrameBatchResult(
            batch_index=batch_index,
            status="success",
            time_range=time_range,
            raw_response=raw_response,
            frame_paths=list(frame_paths),
            frame_observations=frame_observations,
            overall_activity_summary=summary,
        )

    def export_for_kaggle(
        self,
        video_path: str,
        video_theme: str,
        custom_prompt: str,
        frame_interval_input: int | float | None = None,
        batch_size: int | None = None,
        upload_to_alist: bool = False,
    ) -> dict:
        """
        导出完整视频和配置为 Kaggle GPU 离线处理包（ZIP）。

        Kaggle 端负责抽取关键帧、按批次识别并生成 analysis_result.json。
        本地只保留任务编排和后续文案生成，避免把 Kaggle 当作不稳定的在线 API。

        Returns:
            dict with keys:
                - local_zip_path: str   # 本地 zip 路径
                - alist_uploaded: bool  # 是否已上传 Alist
                - alist_url: str|None  # Alist 下载直链（如果上传成功）
                - task_name: str        # 任务名称（用于 Kaggle 结果文件名）
        """
        import zipfile

        frame_interval_seconds = self._resolve_frame_interval(frame_interval_input)
        bs = self._resolve_batch_size(batch_size)

        task_name = f"kaggle_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        export_dir = os.path.join(utils.temp_dir(), "kaggle_export", task_name)
        os.makedirs(export_dir, exist_ok=True)

        import shutil

        video_name = os.path.basename(video_path)
        video_out_dir = os.path.join(export_dir, "input")
        os.makedirs(video_out_dir, exist_ok=True)
        shutil.copy2(video_path, os.path.join(video_out_dir, video_name))

        runner_src = os.path.join(config.root_dir, "resource", "kaggle", "narratoai_kaggle_video_runner.py")
        if os.path.exists(runner_src):
            shutil.copy2(runner_src, os.path.join(export_dir, "narratoai_kaggle_video_runner.py"))

        config_data = {
            "task_name": task_name,
            "task_type": "video_frame_analysis",
            "protocol_version": "narratoai-kaggle-video-v1",
            "video_file": f"input/{video_name}",
            "video_theme": video_theme,
            "custom_prompt": custom_prompt,
            "frame_interval_seconds": frame_interval_seconds,
            "batch_size": bs,
            "model_name": config.app.get("vision_kaggle_model_name", "Qwen/Qwen2-VL-7B-Instruct"),
            "max_new_tokens": 768,
        }
        with open(os.path.join(export_dir, "task_config.json"), "w", encoding="utf-8") as f:
            json.dump(config_data, f, ensure_ascii=False, indent=2)

        readme = """# NarratoAI Kaggle Video Task

Upload this zip to Kaggle, enable GPU, unzip it, then run:

```bash
pip install -U "transformers>=4.45" accelerate pillow opencv-python-headless
python narratoai_kaggle_video_runner.py --config task_config.json
```

The runner writes `analysis_result.json`. Download that file and import it in NarratoAI.
"""
        with open(os.path.join(export_dir, "README_KAGGLE.md"), "w", encoding="utf-8") as f:
            f.write(readme)

        parent_dir = os.path.dirname(export_dir)
        shutil.make_archive(base_name=export_dir, format="zip", root_dir=parent_dir, base_dir=os.path.basename(export_dir))
        local_zip_path = export_dir + ".zip"
        logger.info(f"Kaggle 完整视频离线包已导出: {local_zip_path}")

        result = {
            "local_zip_path": local_zip_path,
            "alist_uploaded": False,
            "alist_url": None,
            "task_name": task_name,
        }

        if upload_to_alist:
            from app.utils.alist_client import get_alist_client
            alist = get_alist_client()
            if alist:
                remote_zip_name = f"{task_name}.zip"
                ok = alist.upload_file(local_zip_path, f"/{remote_zip_name}")
                if ok:
                    alist_url = alist.get_download_url(f"/{remote_zip_name}")
                    result["alist_uploaded"] = True
                    result["alist_url"] = alist_url
                    logger.info(f"已上传 Alist: {alist_url}")
                else:
                    logger.warning("Alist 上传失败，降级为本地下载")
            else:
                logger.warning("Alist 未配置或登录失败，降级为本地下载")

        return result

    def import_kaggle_results(
        self,
        results_json_path: str | None,
        video_path: str,
        frame_interval_input: int | float | None = None,
        batch_size: int | None = None,
        download_from_alist: bool = False,
        alist_task_name: str | None = None,
    ) -> list[FrameBatchResult]:
        """
        导入 Kaggle 离线处理的 analysis_result.json，
        转换为 FrameBatchResult 列表，融入现有脚本生成流程。

        支持两种数据来源：
        - download_from_alist=True：自动从 Alist 下载 analysis_result.json
        - results_json_path：直接传入本地 analysis_result.json 路径
        """
        frame_interval_seconds = self._resolve_frame_interval(frame_interval_input)
        bs = self._resolve_batch_size(batch_size)

        keyframe_files = self._load_or_extract_keyframes(video_path, frame_interval_seconds)
        batches = self._chunk_keyframes(keyframe_files, batch_size=bs)

        results_path = results_json_path
        if download_from_alist and alist_task_name:
            from app.utils.alist_client import get_alist_client
            alist = get_alist_client()
            if not alist:
                raise RuntimeError("Alist 未配置或登录失败，请检查 config.toml 中的 [alist] 配置")
            remote_name = f"{alist_task_name}_analysis_result.json"
            local_results = os.path.join(utils.temp_dir(), "kaggle_import", remote_name)
            os.makedirs(os.path.dirname(local_results), exist_ok=True)
            if not alist.download_file(f"/{remote_name}", local_results):
                legacy_name = f"{alist_task_name}_results.json"
                local_legacy = os.path.join(utils.temp_dir(), "kaggle_import", legacy_name)
                if not alist.download_file(f"/{legacy_name}", local_legacy):
                    raise RuntimeError(f"从 Alist 下载 {remote_name} 失败，请确认 Kaggle 已上传结果")
                local_results = local_legacy
            results_path = local_results
            logger.info(f"从 Alist 下载 Kaggle 分析结果成功: {results_path}")

        if not results_path or not os.path.exists(results_path):
            raise FileNotFoundError(f"Kaggle 分析结果不存在: {results_path}")

        with open(results_path, "r", encoding="utf-8") as f:
            results_data = json.load(f)
        if isinstance(results_data, dict):
            result_items = results_data.get("batches", [])
        else:
            result_items = results_data

        batch_results: list[FrameBatchResult] = []
        for item in result_items:
            batch_index = item.get("batch_index", 0)
            time_range = item.get("time_range", "")
            raw_response = item.get("raw_response", "")
            frame_observations_raw = item.get("frame_observations", [])
            overall_summary = item.get("overall_activity_summary", "")
            status = item.get("status", "success")
            error_message = item.get("error_message", "")

            batch_files = batches[batch_index] if batch_index < len(batches) else []
            frame_obs = []
            for idx, obs in enumerate(frame_observations_raw):
                ts = obs.get("timestamp", "") if isinstance(obs, dict) else ""
                text = obs.get("observation", "") if isinstance(obs, dict) else str(obs)
                frame_obs.append({"frame_path": batch_files[idx] if idx < len(batch_files) else "", "timestamp": ts, "observation": text})

            batch_results.append(
                FrameBatchResult(
                    batch_index=batch_index,
                    status=status,
                    time_range=time_range,
                    raw_response=raw_response,
                    frame_paths=list(batch_files),
                    frame_observations=frame_obs,
                    overall_activity_summary=overall_summary,
                    error_message=error_message,
                )
            )

        logger.info(f"已导入 {len(batch_results)} 个 Kaggle 批次结果")
        return batch_results

    def _validate_batch_payload_contract(self, payload: object, *, expected_frame_count: int) -> str:
        if not isinstance(payload, dict):
            return "Batch response JSON payload must be an object"

        if "frame_observations" not in payload or not isinstance(payload["frame_observations"], list):
            return "Batch response must include frame_observations as a list"

        if len(payload["frame_observations"]) < expected_frame_count:
            return (
                "Batch response frame_observations length is shorter than provided frame_paths: "
                f"{len(payload['frame_observations'])} < {expected_frame_count}"
            )

        return ""
