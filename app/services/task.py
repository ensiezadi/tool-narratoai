import math
import json
import os.path
import re
import traceback
from os import path
from loguru import logger

from app.config import config
from app.config.audio_config import AudioConfig, get_recommended_volumes_for_content
from app.models import const
from app.models.schema import VideoClipParams, SegmentPlan
from app.services import (voice, audio_merger, subtitle_merger, clip_video, merger_video, update_script, generate_video)
from app.services import state as sm
from app.services.segment_plan import build_segment_plan, validate_segment_plans, log_tts_duration_report
from app.utils import utils


def _load_script(video_script_path: str) -> list[dict]:
    """加载并校验剪辑脚本 JSON。"""
    if not path.exists(video_script_path):
        raise ValueError(f"解说脚本文件不存在: {video_script_path}，请先点击【保存脚本】保存脚本后再生成视频。")

    try:
        with open(video_script_path, "r", encoding="utf-8") as f:
            list_script = json.load(f)
            if not isinstance(list_script, list):
                raise ValueError("脚本 JSON 顶层必须是数组")
            return list_script
    except json.JSONDecodeError as e:
        raise ValueError(f"脚本 JSON 解析失败: {e}")


def _build_options_from_plans(plans: list[SegmentPlan], params: VideoClipParams) -> dict:
    """
    根据 SegmentPlan[] + 用户参数构建最终合成选项。

    原声策略从 plans 中推导，而非全局判断。
    """
    # 推荐音量（基于内容类型）
    optimized_volumes = get_recommended_volumes_for_content('mixed')

    # 是否存在需要保留原声的片段（OST 1 或 2）
    has_original_audio = any(p.keep_original_audio for p in plans)

    # 用户设置了非默认值时优先使用用户设置
    final_tts_volume = (
        params.tts_volume
        if hasattr(params, 'tts_volume') and params.tts_volume != 1.0
        else optimized_volumes['tts_volume']
    )

    if has_original_audio:
        final_original_volume = 1.0
        logger.info("检测到原声片段，原声音量设置为 1.0 以保持与原视频一致")
    else:
        final_original_volume = (
            params.original_volume
            if hasattr(params, 'original_volume') and params.original_volume != 0.7
            else optimized_volumes['original_volume']
        )

    final_bgm_volume = (
        params.bgm_volume
        if hasattr(params, 'bgm_volume') and params.bgm_volume != 0.3
        else optimized_volumes['bgm_volume']
    )

    logger.info(f"音量配置 - TTS: {final_tts_volume}, 原声: {final_original_volume}, BGM: {final_bgm_volume}")

    return {
        'voice_volume': final_tts_volume,
        'bgm_volume': final_bgm_volume,
        'original_audio_volume': final_original_volume,
        'keep_original_audio': True,
        'subtitle_enabled': params.subtitle_enabled,
        'subtitle_font': params.font_name,
        'subtitle_font_size': params.font_size,
        'subtitle_color': params.text_fore_color,
        'subtitle_bg_color': None,
        'subtitle_position': params.subtitle_position,
        'custom_position': params.custom_position,
        'threads': params.n_threads
    }


def start_subclip(task_id: str, params: VideoClipParams, subclip_path_videos: dict = None):
    """
    视频处理流水线 — SegmentPlan 驱动版本。

    流程:
        1. 加载脚本 JSON → SegmentPlan[]
        2. TTS 生成（仅 need_tts=True 的片段）
        3. 统一视频裁剪（基于 source_start/end）
        4. 音频/字幕合并（对齐 target timeline）
        5. 视频拼接
        6. 最终合成（音频 + 字幕 + BGM + 视频）
    """
    logger.info(f"\n\n## 开始任务: {task_id}")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=0)

    # ─────────────────────────────────────────────────────────────────────────
    # 1. 加载脚本 → SegmentPlan[]
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("\n\n## 1. 加载视频脚本 → SegmentPlan")
    list_script = _load_script(path.join(params.video_clip_json_path))

    plans = build_segment_plan(list_script)
    validate_segment_plans(plans)

    video_ost = [p.ost for p in plans]
    logger.debug(f"OST 列表: {video_ost}")
    logger.debug(f"target timeline: 0.00s → {plans[-1].target_end:.2f}s" if plans else "无片段")

    # ─────────────────────────────────────────────────────────────────────────
    # 2. TTS 生成（只处理 need_tts=True 的片段）
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("\n\n## 2. 根据 SegmentPlan 生成 TTS 音频")
    tts_segments = [list_script[p.index] for p in plans if p.need_tts]
    logger.debug(f"需要生成 TTS 的片段数: {len(tts_segments)}")

    tts_results = voice.tts_multiple(
        task_id=task_id,
        list_script=tts_segments,
        tts_engine=params.tts_engine,
        voice_name=params.voice_name,
        voice_rate=params.voice_rate,
        voice_pitch=params.voice_pitch,
    )

    # TTS 时长校验：回填到 plans
    tts_map = {r['_id']: r for r in tts_results}
    for plan in plans:
        if plan.need_tts:
            seg_script = list_script[plan.index]
            _id = seg_script.get('_id')
            if _id in tts_map:
                plan.tts_audio_path = tts_map[_id]['audio_file']
                plan.tts_duration = tts_map[_id].get('duration', 0.0)
                plan.subtitle_file = tts_map[_id].get('subtitle_file', '')

    log_tts_duration_report(plans)

    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=20)

    # ─────────────────────────────────────────────────────────────────────────
    # 3. 统一视频裁剪（基于 source_start/end）
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("\n\n## 3. 统一视频裁剪（基于 SegmentPlan）")
    video_clip_result = clip_video.clip_video_unified(
        video_origin_path=params.video_origin_path,
        script_list=list_script,
        tts_results=tts_results
    )

    # 更新 plans 中的裁剪路径和字幕路径
    tts_clip_result = {r['_id']: r['audio_file'] for r in tts_results}
    subclip_clip_result = {r['_id']: r['subtitle_file'] for r in tts_results}
    new_script_list = update_script.update_script_timestamps(
        list_script, video_clip_result, tts_clip_result, subclip_clip_result
    )

    # 回填 video_clip_path 到 plans
    for plan in plans:
        seg_script = list_script[plan.index]
        _id = seg_script.get('_id')
        if _id in video_clip_result:
            plan.video_clip_path = video_clip_result[_id]

    logger.info(f"统一裁剪完成，处理了 {len(video_clip_result)} 个视频片段")
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=60)

    # ─────────────────────────────────────────────────────────────────────────
    # 4. 合并音频和字幕（对齐 target timeline）
    # ─────────────────────────────────────────────────────────────────────────
    logger.info("\n\n## 4. 合并音频和字幕")
    total_duration = sum(float(item.get("duration", 0.0) or 0.0) for item in new_script_list)
    if total_duration <= 0:
        total_duration = sum(p.target_duration for p in plans)
    merged_audio_path = ""
    merged_subtitle_path = ""

    if tts_segments:
        try:
            merged_audio_path = audio_merger.merge_audio_files(
                task_id=task_id,
                total_duration=total_duration,
                list_script=new_script_list
            )
            logger.info(f"音频文件合并成功 -> {merged_audio_path}")

            merged_subtitle_path = subtitle_merger.merge_subtitle_files(new_script_list)
            if merged_subtitle_path:
                logger.info(f"字幕文件合并成功 -> {merged_subtitle_path}")
            else:
                logger.warning("没有有效的字幕内容，将生成无字幕视频")
                merged_subtitle_path = ""
        except Exception as e:
            logger.error(f"合并音频/字幕文件失败: {str(e)}")
    else:
        logger.warning("没有需要合并的音频/字幕")

    # ─────────────────────────────────────────────────────────────────────────
    # 5. 拼接视频
    # ─────────────────────────────────────────────────────────────────────────
    combined_video_path = path.join(utils.task_dir(task_id), "merger.mp4")
    logger.info(f"\n\n## 5. 拼接视频 => {combined_video_path}")

    video_clips = []
    for plan in plans:
        vp = plan.video_clip_path
        if vp and os.path.exists(vp):
            video_clips.append(vp)
        else:
            # 备用方案
            seg_script = list_script[plan.index]
            _id = seg_script.get('_id')
            if subclip_path_videos and _id in subclip_path_videos:
                backup = subclip_path_videos[_id]
                if os.path.exists(backup):
                    video_clips.append(backup)
                    logger.info(f"使用备用视频: {backup}")
                else:
                    logger.error(f"备用视频不存在: {backup}")
            else:
                logger.error(f"片段 {_id} 无视频文件")

    logger.info(f"准备合并 {len(video_clips)} 个视频片段")
    merger_video.combine_clip_videos(
        output_video_path=combined_video_path,
        video_paths=video_clips,
        video_ost_list=video_ost,
        video_aspect=params.video_aspect,
        threads=params.n_threads
    )
    sm.state.update_task(task_id, state=const.TASK_STATE_PROCESSING, progress=80)

    # ─────────────────────────────────────────────────────────────────────────
    # 6. 最终合成（音频 + 字幕 + BGM + 视频）
    # ─────────────────────────────────────────────────────────────────────────
    output_video_path = path.join(utils.task_dir(task_id), "combined.mp4")
    logger.info(f"\n\n## 6. 最终合成 -> {output_video_path}")

    bgm_path = utils.get_bgm_file()
    options = _build_options_from_plans(plans, params)

    generate_video.merge_materials(
        video_path=combined_video_path,
        audio_path=merged_audio_path,
        subtitle_path=merged_subtitle_path,
        bgm_path=bgm_path,
        output_path=output_video_path,
        options=options
    )

    final_video_paths = [output_video_path]
    combined_video_paths = [combined_video_path]

    logger.success(f"任务 {task_id} 已完成，生成 {len(final_video_paths)} 个视频")

    kwargs = {
        "videos": final_video_paths,
        "combined_videos": combined_video_paths
    }
    sm.state.update_task(task_id, state=const.TASK_STATE_COMPLETE, progress=100, **kwargs)
    return kwargs


# ──────────────────────────────────────────────────────────────────────────────
# 旧入口保留兼容，内部转发到 start_subclip
# ──────────────────────────────────────────────────────────────────────────────

def start_subclip_unified(task_id: str, params: VideoClipParams):
    """统一视频裁剪处理函数 — 兼容旧调用，转发到 start_subclip。"""
    return start_subclip(task_id, params)


# ──────────────────────────────────────────────────────────────────────────────
# 参数校验
# ──────────────────────────────────────────────────────────────────────────────

def validate_params(video_path, audio_path, output_file, params):
    """
    验证输入参数

    Raises:
        FileNotFoundError: 文件不存在
        ValueError: 参数无效
    """
    if not video_path:
        raise ValueError("视频路径不能为空")
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"视频文件不存在: {video_path}")

    if audio_path and not os.path.exists(audio_path):
        raise FileNotFoundError(f"音频文件不存在: {audio_path}")
