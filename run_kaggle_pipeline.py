#!/usr/bin/env python3
"""
NarratoAI Kaggle 视频理解一键脚本。

用法：
    python run_kaggle_pipeline.py \
        --video resource/videos/part2.mp4 \
        --subtitle resource/videos/part2.srt \
        --task-name part2_highlight \
        --model Qwen/Qwen2.5-VL-7B-Instruct \
        --target-duration 60 90 \
        --poll-interval 60 \
        --swanlab-mode online

环境变量：
    KAGGLE_USERNAME         Kaggle 用户名
    KAGGLE_KEY             Kaggle API Token（Kaggle CLI 使用）
    KAGGLE_API_TOKEN       Kaggle API Token（兼容旧配置）
    SWANLAB_API_KEY        SwanLab API Key（可选，swanlab_mode=online 时需要）
"""

from __future__ import annotations

import argparse
import os


def configure_kaggle_env(kaggle_username: str | None = None, kaggle_token: str | None = None) -> None:
    """Normalize CLI/env credential names used by the Kaggle CLI."""
    resolved_username = kaggle_username or os.getenv("KAGGLE_USERNAME")
    if resolved_username:
        os.environ["KAGGLE_USERNAME"] = resolved_username

    if kaggle_token:
        os.environ["KAGGLE_KEY"] = kaggle_token
        # Kaggle CLI 2.x treats KAGGLE_API_TOKEN as an OAuth access token,
        # while the legacy API token belongs in KAGGLE_KEY.
        os.environ.pop("KAGGLE_API_TOKEN", None)
        return

    resolved_token = os.getenv("KAGGLE_KEY") or os.getenv("KAGGLE_API_TOKEN")
    if resolved_token:
        os.environ.setdefault("KAGGLE_KEY", resolved_token)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="NarratoAI Kaggle 视频理解一键脚本",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
    # 在线模式（需要 SWANLAB_API_KEY）
    python run_kaggle_pipeline.py \\
        --video resource/videos/part2.mp4 \\
        --subtitle resource/videos/part2.srt \\
        --task-name part2_highlight \\
        --swanlab-mode online

    # 本地模式（不上传 swanlab）
    python run_kaggle_pipeline.py \\
        --video resource/videos/part2.mp4 \\
        --task-name part2_highlight \\
        --swanlab-mode local

环境变量：
    KAGGLE_USERNAME       Kaggle 用户名（替代 --kaggle-username）
    KAGGLE_KEY            Kaggle API Token（替代 --kaggle-token，Kaggle CLI 使用）
    KAGGLE_API_TOKEN      Kaggle API Token（兼容旧配置）
    SWANLAB_API_KEY       SwanLab API Key（swanlab_mode=online 时需要）
""",
    )
    parser.add_argument("--video", default=None, help="视频文件路径；--skip-upload 时可省略")
    parser.add_argument("--subtitle", default=None, help="字幕文件路径（可选）")
    parser.add_argument("--task-name", default=None, help="任务名称，默认用视频文件名 + 时间戳")
    parser.add_argument("--kaggle-username", default=None, help="Kaggle 用户名")
    parser.add_argument("--kaggle-token", default=None, help="Kaggle API Token")
    parser.add_argument("--dataset-slug", default="narratoai-video-understanding-tasks", help="Dataset slug")
    parser.add_argument("--dataset-ref", default=None, help="完整 Dataset 引用，例如 chrisezadi/narratoai-video-understanding-tasks")
    parser.add_argument("--model", default="Qwen/Qwen2.5-VL-7B-Instruct", help="VLM 模型名")
    parser.add_argument("--frame-interval", type=float, default=3.0, help="粗扫抽帧间隔（秒）")
    parser.add_argument("--refine-interval", type=float, default=0.75, help="精扫抽帧间隔（秒）")
    parser.add_argument("--batch-size", type=int, default=8, help="每批送入模型的帧数")
    parser.add_argument("--target-duration", nargs=2, type=int, default=[60, 90], metavar=("MIN", "MAX"), help="目标视频时长（秒）")
    parser.add_argument("--min-fail-events", type=int, default=3, help="最少失败/翻车事件数")
    parser.add_argument("--min-strong-reactions", type=int, default=2, help="最少强反应事件数")
    parser.add_argument("--poll-interval", type=int, default=60, help="轮询 Kaggle 状态间隔（秒）")
    parser.add_argument("--timeout", type=int, default=7200, help="超时时间（秒）")
    parser.add_argument("--swanlab-mode", default="online", choices=["online", "local", "off"], help="SwanLab 运行模式")
    parser.add_argument("--swanlab-project", default="NarratoAI-Video-Understanding", help="SwanLab 项目名")
    parser.add_argument("--kernel-slug", default=None, help="Kaggle Kernel slug，默认用任务名")
    parser.add_argument("--accelerator", default="NvidiaTeslaT4", help="Kaggle accelerator ID，默认使用 NvidiaTeslaT4")
    parser.add_argument("--runtime-model", default=None, help="复用已有 Dataset 时覆盖其模型名")
    parser.add_argument("--runtime-batch-size", type=int, default=None, help="复用已有 Dataset 时覆盖视觉批大小")
    parser.add_argument("--runtime-frame-interval", type=float, default=None, help="复用已有 Dataset 时覆盖抽帧间隔（秒）")
    parser.add_argument("--runtime-max-new-tokens", type=int, default=None, help="复用已有 Dataset 时覆盖单批生成 token 上限")
    parser.add_argument("--runtime-video-file", default=None, help="复用纯视频 Dataset 时指定要分析的视频文件名，例如 part4.mp4")
    parser.add_argument("--max-analysis-seconds", type=float, default=None, help="只分析视频开头指定秒数，用于短程视觉验证")
    parser.add_argument("--analysis-range", nargs=2, type=float, action="append", metavar=("START", "END"), help="仅精扫指定秒数范围；可重复传入多个窗口")
    parser.add_argument("--create-dataset", action="store_true", help="第一次创建 Dataset 时使用；默认复用固定 Dataset 并创建新 version")
    parser.add_argument("--skip-upload", action="store_true", help="跳过 Dataset 上传，直接使用已存在的 Dataset 运行 Kernel")
    parser.add_argument("--no-upload", action="store_true", help="只生成任务包，不上传 Kaggle（跳过 Step 1-2）")
    parser.add_argument("--skip-run", action="store_true", help="上传 Dataset 后不运行 Kernel")
    parser.add_argument("--import-only", help="跳过构建和运行，直接从已有 output 目录导入：--import-only storage/kaggle_results/xxx")
    args = parser.parse_args()

    configure_kaggle_env(args.kaggle_username, args.kaggle_token)

    from app.config import config
    from app.services.kaggle_video_understanding_service import KaggleVideoUnderstandingService

    svc = KaggleVideoUnderstandingService()

    # ── import-only 模式 ──────────────────────────────────────
    if args.import_only:
        print(f"[Import Only] 从 {args.import_only} 导入已有结果")
        files = svc._discover_result_files(args.import_only, "narratoai_outputs")
        result = svc.candidate_clips_to_script(
            candidate_clips_path=files["candidate_clips"],
        )
        print(f"✅ 剪辑脚本已生成: {result['script_path']}")
        print(f"   选中片段: {result['selected_count']} 个")
        return

    if not args.skip_upload and not args.video:
        parser.error("--video is required unless --skip-upload is used")
    if args.skip_upload and not args.task_name and not args.video:
        parser.error("--task-name or --video is required when --skip-upload is used")

    if not args.no_upload:
        print("[Preflight] 验证 Kaggle 账号凭据与 Kernel 权限")
        svc.validate_kaggle_write_access()

    # ── Step 1: 构建任务包 ────────────────────────────────────
    print("\n" + "=" * 60)
    print("Step 1: 构建 Kaggle Kernel/Dataset 任务包")
    print("=" * 60)
    if args.skip_upload:
        runtime_overrides = {
            "model_name": args.runtime_model,
            "batch_size": args.runtime_batch_size,
            "frame_interval_seconds": args.runtime_frame_interval,
            "max_new_tokens": args.runtime_max_new_tokens,
            "video_file": args.runtime_video_file,
            "max_analysis_seconds": args.max_analysis_seconds,
            "analysis_ranges_seconds": args.analysis_range,
        }
        build_result = svc.build_kernel_task(
            task_name=args.task_name or os.path.splitext(os.path.basename(args.video or "kaggle_video"))[0],
            kaggle_username=args.kaggle_username,
            dataset_slug=args.dataset_slug,
            dataset_ref=args.dataset_ref,
            kernel_slug=args.kernel_slug,
            runtime_overrides=runtime_overrides,
        )
    else:
        build_result = svc.build_dataset_task(
            video_path=args.video,
            subtitle_path=args.subtitle,
            task_name=args.task_name,
            kaggle_username=args.kaggle_username,
            dataset_slug=args.dataset_slug,
            frame_interval_seconds=args.frame_interval,
            refine_interval_seconds=args.refine_interval,
            batch_size=args.batch_size,
            model_name=args.model,
            max_new_tokens=args.runtime_max_new_tokens or 1024,
            target_duration_seconds=args.target_duration,
            min_fail_events=args.min_fail_events,
            min_strong_reactions=args.min_strong_reactions,
            kernel_slug=args.kernel_slug,
            max_analysis_seconds=args.max_analysis_seconds,
            analysis_ranges_seconds=args.analysis_range,
        )
    print(f"✅ 任务目录: {build_result['task_dir']}")
    if build_result.get("archive_path"):
        print(f"   Archive:   {build_result['archive_path']}")
    print(f"   Dataset:  {build_result['dataset_ref']}")

    # ── Step 2: 上传 Dataset ─────────────────────────────────
    if not args.no_upload and not args.skip_upload:
        print("\n" + "=" * 60)
        print("Step 2: 上传 Dataset 到 Kaggle")
        print("=" * 60)
        upload_result = svc.upload_dataset(
            task_dir=build_result["task_dir"],
            create=args.create_dataset,
        )
        print(f"✅ Dataset 上传成功")
        print(f"   {build_result['dataset_ref']}")
    elif args.skip_upload:
        print("\n" + "=" * 60)
        print("Step 2: 跳过 Dataset 上传，使用已有 Dataset")
        print("=" * 60)
        print(f"   Dataset: {build_result['dataset_ref']}")

    # ── Step 3: 运行 Kernel（核心） ───────────────────────────
    if not args.skip_run and not args.no_upload:
        print("\n" + "=" * 60)
        print("Step 3: 运行 Kaggle Kernel（GPU 视频理解）")
        print("=" * 60)
        print(f"   SwanLab 模式: {args.swanlab_mode}")
        print(f"   轮询间隔: {args.poll_interval}s | 超时: {args.timeout}s")
        print("   （Kaggle GPU 运行 Qwen2.5-VL，全程 swanlab 记录日志）")
        print()

        run_result = svc.run_on_kaggle(
            task_dir=build_result["task_dir"],
            kernel_slug=args.kernel_slug or build_result["kernel_slug"],
            dataset_ref=build_result["dataset_ref"],
            poll_interval_seconds=args.poll_interval,
            timeout_seconds=args.timeout,
            swanlab_project=args.swanlab_project,
            swanlab_mode=args.swanlab_mode if args.swanlab_mode != "off" else "off",
            accelerator=args.accelerator,
        )

        print("\n" + "=" * 60)
        print("Step 4: 解析结果")
        print("=" * 60)
        print(f"   Output 目录: {run_result['output_base']}")
        print(f"   发现文件:    {list(run_result['files'].keys())}")
        if "event_count" in run_result:
            print(f"   事件数:     {run_result['event_count']}")
        if "clip_count" in run_result:
            print(f"   候选片段:   {run_result['clip_count']} 个")
        if "publish_score" in run_result:
            print(f"   发布分数:   {run_result['publish_score']} | 通过: {run_result.get('quality_pass', False)}")

        # ── Step 5: 导入为剪辑脚本 ─────────────────────────
        quality_report = run_result.get("quality_report", {})
        vision_pass = quality_report.get("vision_inference_pass", True)
        if not vision_pass:
            print("\n⚠️ 视觉推理质量门禁未通过，本次结果不导入剪辑脚本。")
            print("   请先检查 Kaggle 日志和 quality_report.json，再决定是否重跑。")
        elif "candidate_clips" in run_result["files"]:
            print("\n" + "=" * 60)
            print("Step 5: 导入候选片段，生成 NarratoAI 剪辑脚本")
            print("=" * 60)
            script_result = svc.candidate_clips_to_script(
                candidate_clips_path=run_result["files"]["candidate_clips"],
            )
            print(f"✅ 剪辑脚本已生成: {script_result['script_path']}")
            print(f"   选中片段: {script_result['selected_count']} 个")
            print(f"\n   下一步：在 WebUI 中打开 {script_result['script_path']}")
        else:
            print(f"\n⚠️ 未找到 candidate_clips.json，请检查 output 目录: {run_result['output_base']}")
    else:
        print("\n[跳过] Kernel 运行")
        print(f"   任务目录: {build_result['task_dir']}")
        print(f"   如需运行 Kernel，执行：")
        print(f"   kaggle kernels push -p {build_result['task_dir']}")
        print(f"   kaggle kernels status {build_result['kernel_slug']}")

    print("\n✅ 全部完成！")


if __name__ == "__main__":
    main()
