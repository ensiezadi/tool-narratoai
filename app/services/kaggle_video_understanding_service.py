import base64
import json
import os
import re
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger

from app.config import config
from app.utils import utils

try:
    import swanlab
    _SWANLAB_AVAILABLE = True
except ImportError:
    _SWANLAB_AVAILABLE = False
    swanlab = None


class KaggleVideoUnderstandingService:
    """Build and import Kaggle GPU video-understanding tasks."""

    DEFAULT_MODEL = "Qwen/Qwen2.5-VL-3B-Instruct"
    DEFAULT_CLIP_STYLE = "gaming_live_highlight"
    DEFAULT_TARGET_DURATION = [60, 90]
    DEFAULT_ACCELERATOR = "NvidiaTeslaT4"

    def validate_kaggle_write_access(self) -> dict[str, Any]:
        """Fail fast when Kaggle credentials cannot access the user's kernels."""
        if not shutil.which("kaggle"):
            raise RuntimeError("未找到 kaggle CLI，请先安装并配置 Kaggle API 凭据")

        command = ["kaggle", "kernels", "list", "--mine"]
        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            output = "\n".join(
                text.strip()
                for text in (completed.stderr or "", completed.stdout or "")
                if text.strip()
            )
            if "401" in output or "unauthorized" in output.lower():
                raise RuntimeError(
                    "Kaggle 凭据无权访问当前账号的 Kernel，请重新配置 "
                    "KAGGLE_USERNAME/KAGGLE_API_TOKEN 或 ~/.kaggle/kaggle.json"
                )
            raise RuntimeError(f"Kaggle 账号预检失败: {output or 'unknown Kaggle CLI error'}")

        return {
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        }

    def build_dataset_task(
        self,
        *,
        video_path: str,
        subtitle_path: str | None = None,
        task_name: str | None = None,
        kaggle_username: str | None = None,
        dataset_slug: str | None = None,
        video_theme: str = "",
        custom_prompt: str = "",
        frame_interval_seconds: float = 3.0,
        refine_interval_seconds: float = 0.75,
        batch_size: int = 8,
        model_name: str | None = None,
        target_duration_seconds: list[int] | None = None,
        min_fail_events: int = 3,
        min_strong_reactions: int = 2,
        ending: str = "death_fail_or_coop_fail",
        kernel_slug: str | None = None,
    ) -> dict[str, Any]:
        if not video_path or not os.path.exists(video_path):
            raise FileNotFoundError(f"视频文件不存在: {video_path}")
        if subtitle_path and not os.path.exists(subtitle_path):
            raise FileNotFoundError(f"字幕文件不存在: {subtitle_path}")
        if frame_interval_seconds <= 0:
            raise ValueError("frame_interval_seconds must be > 0")
        if refine_interval_seconds <= 0:
            raise ValueError("refine_interval_seconds must be > 0")
        if batch_size <= 0:
            raise ValueError("batch_size must be > 0")

        task = self._normalize_task_name(task_name or Path(video_path).stem)
        if not task.endswith(datetime.now().strftime("%Y%m%d")):
            task = f"{task}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        username = (
            (kaggle_username or "").strip()
            or os.getenv("KAGGLE_USERNAME", "").strip()
            or str(config.app.get("kaggle_username", "") or "").strip()
        )
        slug = self._slugify(
            dataset_slug
            or str(config.app.get("kaggle_dataset_slug", "") or "").strip()
            or "narratoai-video-understanding-tasks"
        )
        dataset_ref = f"{username}/{slug}" if username else slug

        task_dir = os.path.join(utils.storage_dir("kaggle_tasks", create=True), task)
        os.makedirs(task_dir, exist_ok=True)

        video_name = os.path.basename(video_path)
        video_dst = os.path.join(task_dir, video_name)
        shutil.copy2(video_path, video_dst)

        subtitle_file = ""
        if subtitle_path:
            subtitle_name = os.path.basename(subtitle_path)
            subtitle_dst = os.path.join(task_dir, subtitle_name)
            shutil.copy2(subtitle_path, subtitle_dst)
            subtitle_file = subtitle_name

        kaggle_dir = os.path.join(config.root_dir, "resource", "kaggle")
        runner_src = os.path.join(kaggle_dir, "narratoai_kaggle_video_runner.py")
        notebook_src = os.path.join(kaggle_dir, "narratoai_video_understanding.ipynb")
        if os.path.exists(runner_src):
            shutil.copy2(runner_src, os.path.join(task_dir, "narratoai_kaggle_video_runner.py"))
        if os.path.exists(notebook_src):
            shutil.copy2(notebook_src, os.path.join(task_dir, "narratoai_video_understanding.ipynb"))

        task_config = {
            "task_name": task,
            "task_type": "video_understanding_highlight",
            "protocol_version": "narratoai-kaggle-video-understanding-v1",
            "video_file": video_name,
            "subtitle_file": subtitle_file,
            "video_theme": video_theme,
            "custom_prompt": custom_prompt,
            "frame_interval_seconds": float(frame_interval_seconds),
            "refine_interval_seconds": float(refine_interval_seconds),
            "batch_size": int(batch_size),
            "model_name": model_name or self.DEFAULT_MODEL,
            "max_new_tokens": 1024,
            "target_duration_seconds": target_duration_seconds or self.DEFAULT_TARGET_DURATION,
            "clip_style": self.DEFAULT_CLIP_STYLE,
            "requirements": {
                "min_fail_events": min_fail_events,
                "min_strong_reactions": min_strong_reactions,
                "ending": ending,
            },
            "quality_gate": {
                "pass_score": 8.0,
                "manual_review_score": 7.0,
            },
        }
        self._write_json(os.path.join(task_dir, "task_config.json"), task_config)

        dataset_metadata = {
            "title": self._dataset_title(task),
            "id": dataset_ref,
            "licenses": [{"name": "CC0-1.0"}],
        }
        self._write_json(os.path.join(task_dir, "dataset-metadata.json"), dataset_metadata)

        resolved_kernel_slug = self._slugify(kernel_slug or task)
        kernel_metadata = {
            "id": f"{username}/{resolved_kernel_slug}" if username else resolved_kernel_slug,
            "title": resolved_kernel_slug,
            "code_file": "narratoai_video_understanding.ipynb",
            "language": "python",
            "kernel_type": "notebook",
            "dataset_sources": [dataset_ref] if dataset_ref else [],
            "enable_gpu": True,
        }
        self._write_json(os.path.join(task_dir, "kernel-metadata.json"), kernel_metadata)

        readme = self._build_readme(task, dataset_ref)
        with open(os.path.join(task_dir, "README.md"), "w", encoding="utf-8") as fp:
            fp.write(readme)

        archive_path = shutil.make_archive(
            base_name=task_dir,
            format="zip",
            root_dir=os.path.dirname(task_dir),
            base_dir=os.path.basename(task_dir),
        )

        result = {
            "task_name": task,
            "task_dir": task_dir,
            "archive_path": archive_path,
            "dataset_ref": dataset_ref,
            "dataset_slug": slug,
            "kernel_slug": resolved_kernel_slug,
            "dataset_metadata_path": os.path.join(task_dir, "dataset-metadata.json"),
            "task_config_path": os.path.join(task_dir, "task_config.json"),
            "notebook_path": os.path.join(task_dir, "narratoai_video_understanding.ipynb"),
        }
        logger.info(f"Kaggle Dataset 任务目录已生成: {task_dir}")
        return result

    def build_kernel_task(
        self,
        *,
        task_name: str,
        kaggle_username: str | None = None,
        dataset_slug: str | None = None,
        dataset_ref: str | None = None,
        kernel_slug: str | None = None,
        runtime_overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Build a lightweight Kaggle Kernel package for an existing Dataset."""
        task = self._normalize_task_name(task_name)
        if not task.endswith(datetime.now().strftime("%Y%m%d")):
            task = f"{task}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

        username = (
            (kaggle_username or "").strip()
            or os.getenv("KAGGLE_USERNAME", "").strip()
            or str(config.app.get("kaggle_username", "") or "").strip()
        )
        slug = self._slugify(
            dataset_slug
            or str(config.app.get("kaggle_dataset_slug", "") or "").strip()
            or "narratoai-video-understanding-tasks"
        )
        resolved_dataset_ref = (dataset_ref or "").strip() or (f"{username}/{slug}" if username else slug)

        task_dir = os.path.join(utils.storage_dir("kaggle_tasks", create=True), task)
        os.makedirs(task_dir, exist_ok=True)

        kaggle_dir = os.path.join(config.root_dir, "resource", "kaggle")
        runner_src = os.path.join(kaggle_dir, "narratoai_kaggle_video_runner.py")
        notebook_src = os.path.join(kaggle_dir, "narratoai_video_understanding.ipynb")
        if os.path.exists(runner_src):
            shutil.copy2(runner_src, os.path.join(task_dir, "narratoai_kaggle_video_runner.py"))
        if os.path.exists(notebook_src):
            notebook_dst = os.path.join(task_dir, "narratoai_video_understanding.ipynb")
            shutil.copy2(notebook_src, notebook_dst)
            self._prepare_kernel_notebook(notebook_dst, runner_src, runtime_overrides)

        resolved_kernel_slug = self._slugify(kernel_slug or task)
        kernel_metadata = {
            "id": f"{username}/{resolved_kernel_slug}" if username else resolved_kernel_slug,
            "title": resolved_kernel_slug,
            "code_file": "narratoai_video_understanding.ipynb",
            "language": "python",
            "kernel_type": "notebook",
            "dataset_sources": [resolved_dataset_ref] if resolved_dataset_ref else [],
            "enable_gpu": True,
        }
        self._write_json(os.path.join(task_dir, "kernel-metadata.json"), kernel_metadata)

        readme = self._build_readme(task, resolved_dataset_ref)
        with open(os.path.join(task_dir, "README.md"), "w", encoding="utf-8") as fp:
            fp.write(readme)

        result = {
            "task_name": task,
            "task_dir": task_dir,
            "archive_path": "",
            "dataset_ref": resolved_dataset_ref,
            "dataset_slug": slug,
            "kernel_slug": resolved_kernel_slug,
            "dataset_metadata_path": "",
            "task_config_path": "",
            "notebook_path": os.path.join(task_dir, "narratoai_video_understanding.ipynb"),
        }
        logger.info(f"Kaggle Kernel 任务目录已生成: {task_dir}")
        return result

    def upload_dataset(self, *, task_dir: str, create: bool = True, message: str | None = None) -> dict[str, Any]:
        if not os.path.isdir(task_dir):
            raise FileNotFoundError(f"Kaggle 任务目录不存在: {task_dir}")
        self.validate_kaggle_write_access()

        command = ["kaggle", "datasets", "create", "-p", task_dir, "--dir-mode", "zip"]
        if not create:
            command = [
                "kaggle",
                "datasets",
                "version",
                "-p",
                task_dir,
                "--dir-mode",
                "zip",
                "-m",
                message or f"update {os.path.basename(task_dir)}",
            ]

        completed = subprocess.run(command, capture_output=True, text=True, check=False)
        if completed.returncode != 0:
            stderr = (completed.stderr or completed.stdout or "").strip()
            if create and ("already in use" in stderr.lower() or "already exists" in stderr.lower()):
                logger.info("Dataset 已存在，改为 version 更新")
                command = [
                    "kaggle",
                    "datasets",
                    "version",
                    "-p",
                    task_dir,
                    "--dir-mode",
                    "zip",
                    "-m",
                    message or f"update {os.path.basename(task_dir)}",
                ]
                completed = subprocess.run(command, capture_output=True, text=True, check=False)
                if completed.returncode != 0:
                    raise RuntimeError(f"Kaggle Dataset version 更新失败: {completed.stderr or completed.stdout}")
            else:
                raise RuntimeError(f"Kaggle Dataset 上传失败: {stderr}")

        output = (completed.stdout or completed.stderr or "").strip()
        logger.info(f"Kaggle Dataset 上传完成: {output}")
        return {
            "command": command,
            "stdout": completed.stdout,
            "stderr": completed.stderr,
            "returncode": completed.returncode,
        }

    def run_on_kaggle(
        self,
        *,
        task_dir: str,
        kernel_slug: str | None = None,
        dataset_ref: str | None = None,
        poll_interval_seconds: int = 60,
        timeout_seconds: int = 7200,
        output_subdir: str = "narratoai_outputs",
        swanlab_project: str = "NarratoAI-Video-Understanding",
        swanlab_api_key: str | None = None,
        swanlab_mode: str = "online",
        accelerator: str | None = None,
    ) -> dict[str, Any]:
        """一键运行 Kaggle Notebook 并下载结果，全程 swanlab 记录日志。

        完整流程：
        1. push notebook 到 Kaggle Kernel（自动启动 GPU 运行）
        2. 轮询状态直到完成
        3. 下载 output 文件到本地
        4. 解析 event_timeline.json / candidate_clips.json / quality_report.json

        参数：
            task_dir: build_dataset_task() 生成的本地任务目录
            kernel_slug: Kaggle Kernel slug，默认取 task_dir 目录名
            dataset_ref: Dataset 引用，用于日志
            poll_interval_seconds: 轮询间隔，默认 60 秒
            timeout_seconds: 超时时间，默认 2 小时
            output_subdir: Kaggle Notebook output 子目录
            swanlab_project: swanlab 项目名
            swanlab_api_key: swanlab API key（可选，优先读环境变量 SWANLAB_API_KEY）
            swanlab_mode: swanlab 运行模式，"online" 或 "local"
        """
        self.validate_kaggle_write_access()

        task_name = os.path.basename(os.path.abspath(task_dir))
        kernel_slug = kernel_slug or self._slugify(task_name)
        username = (
            os.getenv("KAGGLE_USERNAME", "").strip()
            or str(config.app.get("kaggle_username", "") or "").strip()
        )
        kernel_ref = f"{username}/{kernel_slug}" if username else kernel_slug

        # ── swanlab 初始化 ──────────────────────────────────────────
        swanlab_api_key = swanlab_api_key or os.getenv("SWANLAB_API_KEY")
        swanlab_run = None
        if _SWANLAB_AVAILABLE:
            try:
                if swanlab_api_key:
                    swanlab.login(api_key=swanlab_api_key)
                swanlab_run = swanlab.init(
                    mode=swanlab_mode,
                    project=swanlab_project,
                    name=task_name,
                    config={
                        "task_dir": task_dir,
                        "kernel_ref": kernel_ref,
                        "dataset_ref": dataset_ref,
                        "poll_interval_seconds": poll_interval_seconds,
                    },
                )
                swanlab.log({"phase": "init", "message": "SwanLab 日志记录已启动", "kernel_ref": kernel_ref})
                logger.info(f"SwanLab 记录已初始化: {swanlab_run.dir if swanlab_run else 'N/A'}")
            except Exception as exc:
                logger.warning(f"SwanLab 初始化失败，继续运行: {exc}")
                swanlab_run = None

        def _swanlab_log(data: dict[str, Any]) -> None:
            if swanlab_run is not None:
                try:
                    swanlab.log(data)
                except Exception:
                    pass

        def _run_cli(args: list[str], check: bool = True) -> subprocess.CompletedProcess:
            completed = subprocess.run(args, capture_output=True, text=True, check=False)
            if check and completed.returncode != 0:
                raise RuntimeError(
                    f"Kaggle CLI 失败: {' '.join(args)}\n"
                    f"stdout: {completed.stdout}\n"
                    f"stderr: {completed.stderr}"
                )
            return completed

        # ── Step 1: Push Notebook ──────────────────────────────────
        logger.info(f"[1/4] 推送 Notebook 到 Kaggle Kernel: {kernel_ref}")
        _swanlab_log({"phase": "push", "message": "开始推送 Notebook", "kernel_ref": kernel_ref})

        push_command = [
            "kaggle", "kernels", "push",
            "-p", task_dir,
        ]
        if accelerator:
            push_command.extend(["--accelerator", accelerator])
        push_result = _run_cli(push_command)
        logger.info(f"Notebook 推送完成:\n{push_result.stdout}")
        _swanlab_log({
            "phase": "push",
            "message": "Notebook 推送成功",
            "push_stdout": push_result.stdout[:500],
            "push_stderr": push_result.stderr[:500],
        })

        logger.info(f"[2/4] 等待 Kernel 完成（轮询间隔 {poll_interval_seconds}s，超时 {timeout_seconds}s）")
        _swanlab_log({
            "phase": "poll",
            "message": "开始轮询 Kernel 状态",
            "poll_interval_seconds": poll_interval_seconds,
            "timeout_seconds": timeout_seconds,
        })

        start_time = time.time()
        poll_count = 0
        last_status = ""
        while True:
            elapsed = time.time() - start_time
            if elapsed > timeout_seconds:
                raise TimeoutError(f"Kernel 运行超时（{timeout_seconds}s），最后状态: {last_status}")

            poll_count += 1
            status_result = _run_cli(["kaggle", "kernels", "status", kernel_ref], check=False)
            status = (status_result.stdout or status_result.stderr or "unknown").strip().lower()
            status_state = self._normalize_kernel_status(status)
            last_status = status

            elapsed_rounded = round(elapsed)
            logger.info(f"[轮询 #{poll_count}] {elapsed_rounded}s | 状态: {status}")
            _swanlab_log({
                "phase": "poll",
                "poll_count": poll_count,
                "elapsed_seconds": elapsed_rounded,
                "status": status,
                "status_state": status_state,
            })

            if status_state == "complete":
                logger.info("Kernel 运行完成！")
                _swanlab_log({"phase": "poll", "message": "Kernel 运行完成", "total_polls": poll_count, "elapsed_seconds": elapsed_rounded})
                break
            elif status_state == "failed":
                raise RuntimeError(f"Kernel 运行失败，状态: {status}\n{status_result.stderr}")

            time.sleep(poll_interval_seconds)

        logger.info(f"[3/4] 下载 Kernel Output")
        _swanlab_log({"phase": "download", "message": "开始下载 Output"})

        output_base = os.path.join(utils.storage_dir("kaggle_results", create=True), task_name)
        os.makedirs(output_base, exist_ok=True)

        dl_result = _run_cli([
            "kaggle", "kernels", "output",
            kernel_ref,
            "-p", output_base,
            "-o",      # overwrite
        ])
        logger.info(f"Output 下载完成:\n{dl_result.stdout}")
        _swanlab_log({
            "phase": "download",
            "message": "Output 下载成功",
            "download_output": dl_result.stdout[:500],
            "output_base": output_base,
        })

        logger.info("[4/4] 解析结果文件")
        _swanlab_log({"phase": "parse", "message": "开始解析结果文件"})

        result_files = self._discover_result_files(output_base, output_subdir)

        parsed: dict[str, Any] = {
            "kernel_ref": kernel_ref,
            "output_base": output_base,
            "files": result_files,
        }

        # event_timeline.json
        if "event_timeline" in result_files:
            with open(result_files["event_timeline"], "r", encoding="utf-8") as f:
                event_data = json.load(f)
            events = event_data.get("events", [])
            parsed["event_timeline"] = event_data
            parsed["event_count"] = len(events)
            event_types: dict[str, int] = {}
            for ev in events:
                event_types[ev.get("event_type", "unknown")] = event_types.get(ev.get("event_type", "unknown"), 0) + 1
            logger.info(f"事件统计: {event_types}")
            _swanlab_log({
                "phase": "result",
                "event_count": len(events),
                "event_types": event_types,
            })

        # candidate_clips.json
        if "candidate_clips" in result_files:
            with open(result_files["candidate_clips"], "r", encoding="utf-8") as f:
                clips_data = json.load(f)
            clips = clips_data.get("clips", [])
            parsed["candidate_clips"] = clips_data
            parsed["clip_count"] = len(clips)
            clip_scores = [float(c.get("score", 0)) for c in clips]
            avg_score = round(sum(clip_scores) / len(clip_scores), 2) if clip_scores else 0
            logger.info(f"候选片段: {len(clips)} 个，平均分 {avg_score}")
            _swanlab_log({
                "phase": "result",
                "clip_count": len(clips),
                "average_clip_score": avg_score,
            })

        # quality_report.json
        if "quality_report" in result_files:
            with open(result_files["quality_report"], "r", encoding="utf-8") as f:
                quality_data = json.load(f)
            parsed["quality_report"] = quality_data
            publish_score = quality_data.get("publish_score", 0)
            pass_status = quality_data.get("pass", False)
            parsed["publish_score"] = publish_score
            parsed["quality_pass"] = pass_status
            logger.info(f"质量报告: 发布分数 {publish_score}，通过: {pass_status}")
            _swanlab_log({
                "phase": "result",
                "publish_score": publish_score,
                "quality_pass": pass_status,
                "fail_event_count": quality_data.get("fail_event_count", 0),
                "strong_reaction_count": quality_data.get("strong_reaction_count", 0),
            })

        # rough_cut.mp4
        if "rough_cut" in result_files:
            rough_size = os.path.getsize(result_files["rough_cut"])
            parsed["rough_cut_size_bytes"] = rough_size
            logger.info(f"Rough Cut: {result_files['rough_cut']} ({rough_size / 1024 / 1024:.1f} MB)")
            _swanlab_log({"phase": "result", "rough_cut_size_mb": round(rough_size / 1024 / 1024, 1)})

        logger.info(f"全部解析完成，结果目录: {output_base}")
        _swanlab_log({"phase": "complete", "message": "Kaggle 视频理解流程全部完成", "output_base": output_base})

        if swanlab_run is not None:
            try:
                swanlab.finish()
            except Exception:
                pass

        return parsed

    def _discover_result_files(self, output_base: str, subdir: str) -> dict[str, str]:
        """在 output_base 里找 NarratoAI 的输出文件。"""
        candidates: dict[str, str] = {}

        # 可能直接放在 output_base，也可能放在 subdir 里
        for base in [output_base, os.path.join(output_base, subdir), os.path.join(output_base, "narratoai_outputs")]:
            if not os.path.isdir(base):
                continue
            for fname in os.listdir(base):
                fpath = os.path.join(base, fname)
                if fname == "event_timeline.json" and "event_timeline" not in candidates:
                    candidates["event_timeline"] = fpath
                elif fname == "candidate_clips.json" and "candidate_clips" not in candidates:
                    candidates["candidate_clips"] = fpath
                elif fname == "quality_report.json" and "quality_report" not in candidates:
                    candidates["quality_report"] = fpath
                elif fname == "rough_cut.mp4" and "rough_cut" not in candidates:
                    candidates["rough_cut"] = fpath
                elif fname == "analysis_result.json" and "analysis_result" not in candidates:
                    candidates["analysis_result"] = fpath

        logger.info(f"发现结果文件: {list(candidates.keys())}")
        return candidates

    @staticmethod
    def _normalize_kernel_status(status: str) -> str:
        text = (status or "").strip().lower()
        if "complete" in text or "succeed" in text or "success" in text:
            return "complete"
        if any(token in text for token in ("error", "failed", "failure", "cancel", "invalid")):
            return "failed"
        if any(token in text for token in ("running", "executing", "queued", "pending", "initializing")):
            return "running"
        return text or "unknown"

    def candidate_clips_to_script(
        self,
        *,
        candidate_clips_path: str,
        output_script_path: str | None = None,
        min_score: float = 7.0,
        include_low_score: bool = False,
    ) -> dict[str, Any]:
        if not os.path.exists(candidate_clips_path):
            raise FileNotFoundError(f"候选片段文件不存在: {candidate_clips_path}")

        with open(candidate_clips_path, "r", encoding="utf-8") as fp:
            payload = json.load(fp)

        clips = payload.get("clips", []) if isinstance(payload, dict) else payload
        if not isinstance(clips, list):
            raise ValueError("candidate_clips.json 格式错误：clips 必须是数组")

        selected = []
        for clip in clips:
            if not isinstance(clip, dict):
                continue
            score = float(clip.get("score", 0) or 0)
            if not include_low_score and score < min_score:
                continue
            timestamp = self._clip_timestamp(clip)
            if not timestamp:
                continue
            selected.append((self._timestamp_to_milliseconds(timestamp.split("-", 1)[0]), score, clip, timestamp))

        selected.sort(key=lambda item: (item[0], -item[1]))
        script = []
        for index, (_, _, clip, timestamp) in enumerate(selected, start=1):
            script.append(
                {
                    "_id": index,
                    "timestamp": timestamp,
                    "picture": str(clip.get("picture") or clip.get("visual_evidence") or ""),
                    "narration": str(clip.get("narration_hint") or clip.get("narration") or ""),
                    "OST": int(clip.get("OST", 1) or 1),
                }
            )

        if output_script_path is None:
            task_name = str(payload.get("task_name", "") if isinstance(payload, dict) else "").strip()
            filename = f"{task_name or 'kaggle_highlight'}_script.json"
            output_script_path = os.path.join(utils.script_dir(), filename)

        os.makedirs(os.path.dirname(output_script_path), exist_ok=True)
        self._write_json(output_script_path, script)
        logger.info(f"Kaggle 候选片段已转换为剪辑脚本: {output_script_path}")

        return {
            "script_path": output_script_path,
            "video_clip_json": script,
            "selected_count": len(script),
        }

    @staticmethod
    def _write_json(path: str, payload: Any) -> None:
        with open(path, "w", encoding="utf-8") as fp:
            json.dump(payload, fp, ensure_ascii=False, indent=2)

    @staticmethod
    def _prepare_kernel_notebook(
        notebook_path: str,
        runner_path: str,
        runtime_overrides: dict[str, Any] | None = None,
    ) -> None:
        """Embed current runner and optional config overrides into a lightweight Kernel."""
        with open(notebook_path, "r", encoding="utf-8") as fp:
            notebook = json.load(fp)

        if os.path.exists(runner_path):
            with open(runner_path, "rb") as fp:
                encoded_runner = base64.b64encode(fp.read()).decode("ascii")
            runner_cell = {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    "import base64\n",
                    "embedded_runner_path = Path('/kaggle/working/narratoai_kaggle_video_runner.py')\n",
                    f"embedded_runner_path.write_bytes(base64.b64decode('{encoded_runner}'))\n",
                    "print('Embedded runner:', embedded_runner_path)\n",
                ],
            }
            runner_index = next(
                (
                    index for index, cell in enumerate(notebook.get("cells", []))
                    if "runner_candidates = [" in "".join(cell.get("source", []))
                ),
                len(notebook.get("cells", [])),
            )
            notebook["cells"].insert(runner_index, runner_cell)

        overrides = {key: value for key, value in (runtime_overrides or {}).items() if value is not None}
        if overrides:
            encoded_overrides = json.dumps(overrides, ensure_ascii=False)
            override_cell = {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": [
                    f"runtime_overrides = json.loads({json.dumps(encoded_overrides)})\n",
                    "config.update(runtime_overrides)\n",
                    "print('Runtime overrides:', runtime_overrides)\n",
                ],
            }
            config_index = next(
                (
                    index + 1 for index, cell in enumerate(notebook.get("cells", []))
                    if "config = json.loads(config_path.read_text" in "".join(cell.get("source", []))
                ),
                len(notebook.get("cells", [])),
            )
            notebook["cells"].insert(config_index, override_cell)

        with open(notebook_path, "w", encoding="utf-8") as fp:
            json.dump(notebook, fp, ensure_ascii=False, indent=1)

    def _copy_kaggle_assets(self, task_dir: str) -> None:
        kaggle_dir = os.path.join(config.root_dir, "resource", "kaggle")
        for filename in [
            "narratoai_kaggle_video_runner.py",
            "narratoai_video_understanding.ipynb",
        ]:
            src = os.path.join(kaggle_dir, filename)
            if os.path.exists(src):
                shutil.copy2(src, os.path.join(task_dir, filename))

    @staticmethod
    def _normalize_task_name(value: str) -> str:
        return re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip()).strip("_") or "narratoai_video"

    @staticmethod
    def _slugify(value: str) -> str:
        slug = re.sub(r"[^a-z0-9-]+", "-", value.lower()).strip("-")
        slug = re.sub(r"-+", "-", slug)
        return slug[:90] or "narratoai-video-understanding"

    @staticmethod
    def _dataset_title(task_name: str) -> str:
        title = f"NarratoAI {task_name}"
        if len(title) <= 50:
            return title
        return title[:50].rstrip("_- ")

    @staticmethod
    def _build_readme(task_name: str, dataset_ref: str) -> str:
        return f"""# NarratoAI Kaggle Video Understanding Task

Task: `{task_name}`

Dataset: `{dataset_ref}`

Run `narratoai_video_understanding.ipynb` on Kaggle with GPU enabled.

The notebook writes outputs to:

```text
/kaggle/working/narratoai_outputs/
```

Download these files after the run:

- `event_timeline.json`
- `candidate_clips.json`
- `quality_report.json`
- optional `rough_cut.mp4`

Import `candidate_clips.json` back into NarratoAI to create a local
`video_clip_json` script and generate the final video in the WebUI.
"""

    @staticmethod
    def _clip_timestamp(clip: dict[str, Any]) -> str:
        timestamp = str(clip.get("timestamp") or "").strip()
        if "-" in timestamp:
            return timestamp

        recommended = clip.get("recommended_clip")
        if isinstance(recommended, dict):
            start = str(recommended.get("start") or "").strip()
            end = str(recommended.get("end") or "").strip()
            if start and end:
                return f"{start}-{end}"
        return ""

    @staticmethod
    def _timestamp_to_milliseconds(timestamp: str) -> int:
        text = (timestamp or "").strip()
        try:
            if "," in text:
                time_part, milliseconds_part = text.split(",", 1)
                milliseconds = int(milliseconds_part)
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
