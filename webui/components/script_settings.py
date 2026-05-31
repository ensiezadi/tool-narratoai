import os
import glob
import json
import time
import traceback
import streamlit as st
from loguru import logger

from app.config import config
from app.models.schema import VideoClipParams
from app.services.documentary.frame_analysis_service import DocumentaryFrameAnalysisService
from app.services.kaggle_video_understanding_service import KaggleVideoUnderstandingService
from app.services.subtitle_text import decode_subtitle_bytes
from app.utils import utils, check_script
from webui.tools.generate_script_docu import generate_script_docu
from webui.tools.generate_script_short import generate_script_short
from webui.tools.generate_short_summary import generate_script_short_sunmmary
from webui.tools.generate_script_gaming import generate_script_gaming
from webui.components.alist_file_browser import AlistClient


def render_script_panel(tr):
    """渲染脚本配置面板"""
    with st.expander(tr("Video Script Configuration"), expanded=True):
        params = VideoClipParams()

        # 渲染脚本文件选择
        render_script_file(tr, params)

        # 渲染视频文件选择
        render_video_file(tr, params)

        # 获取当前选择的脚本类型
        script_path = st.session_state.get('video_clip_json_path', '')

        # 根据脚本类型显示不同的布局
        if script_path == "auto":
            # 画面解说
            render_video_details(tr)
        elif script_path == "short":
            # 短剧混剪
            render_short_generate_options(tr)
        elif script_path == "summary":
            # 短剧解说
            short_drama_summary(tr)
        elif script_path == "gaming":
            # 游戏解说
            render_gaming_details(tr)
        else:
            # 默认为空
            pass

        # 渲染脚本操作按钮
        render_script_buttons(tr, params)


def render_script_file(tr, params):
    """渲染脚本文件选择"""
    # 定义功能模式
    MODE_FILE = "file_selection"
    MODE_AUTO = "auto"
    MODE_SHORT = "short"
    MODE_SUMMARY = "summary"
    MODE_GAMING = "gaming"

    # 处理保存脚本后的模式切换（必须在 widget 实例化之前）
    if st.session_state.get('_switch_to_file_mode'):
        st.session_state['script_mode_selection'] = tr("Select/Upload Script")
        del st.session_state['_switch_to_file_mode']

    # 模式选项映射
    mode_options = {
        tr("Select/Upload Script"): MODE_FILE,
        tr("Auto Generate"): MODE_AUTO,
        tr("Short Generate"): MODE_SHORT,
        tr("Short Drama Summary"): MODE_SUMMARY,
        tr("Gaming Commentary"): MODE_GAMING,
    }
    
    # 获取当前状态
    current_path = st.session_state.get('video_clip_json_path', '')
    
    # 确定当前选中的模式索引
    default_index = 0
    mode_keys = list(mode_options.keys())

    if current_path == "auto":
        default_index = mode_keys.index(tr("Auto Generate"))
    elif current_path == "short":
        default_index = mode_keys.index(tr("Short Generate"))
    elif current_path == "summary":
        default_index = mode_keys.index(tr("Short Drama Summary"))
    elif current_path == "gaming":
        default_index = mode_keys.index(tr("Gaming Commentary"))
    else:
        default_index = mode_keys.index(tr("Select/Upload Script"))

    # 1. 渲染功能选择下拉框
    # 使用 segmented_control 替代 selectbox，提供更好的视觉体验
    default_mode_label = mode_keys[default_index]
    
    # 定义回调函数来处理状态更新
    def update_script_mode():
        # 获取当前选中的标签
        selected_label = st.session_state.script_mode_selection
        if selected_label:
            # 更新实际的 path 状态
            new_mode = mode_options[selected_label]
            st.session_state.video_clip_json_path = new_mode
            params.video_clip_json_path = new_mode
        else:
            # 如果用户取消选择（segmented_control 允许取消），恢复到默认或上一个状态
            # 这里我们强制保持当前状态，或者重置为默认
            st.session_state.script_mode_selection = default_mode_label

    # 渲染组件
    selected_mode_label = st.segmented_control(
        tr("Video Type"),
        options=mode_keys,
        default=default_mode_label,
        key="script_mode_selection",
        on_change=update_script_mode
    )
    
    # 处理未选择的情况（虽然有default，但在某些交互下可能为空）
    if not selected_mode_label:
        selected_mode_label = default_mode_label
        
    selected_mode = mode_options[selected_mode_label]

    # 2. 根据选择的模式处理逻辑
    if selected_mode == MODE_FILE:
        # --- 文件选择模式 ---
        script_list = [
            (tr("None"), ""),
            (tr("Upload Script"), "upload_script")
        ]

        # 获取已有脚本文件
        suffix = "*.json"
        script_dir = utils.script_dir()
        files = glob.glob(os.path.join(script_dir, suffix))
        file_list = []

        for file in files:
            file_list.append({
                "name": os.path.basename(file),
                "file": file,
                "ctime": os.path.getctime(file)
            })

        file_list.sort(key=lambda x: x["ctime"], reverse=True)
        for file in file_list:
            display_name = file['file'].replace(config.root_dir, "")
            script_list.append((display_name, file['file']))

        # 找到保存的脚本文件在列表中的索引
        # 如果当前path是特殊值(auto/short/summary)，则重置为空
        saved_script_path = current_path if current_path not in [MODE_AUTO, MODE_SHORT, MODE_SUMMARY] else ""
        
        selected_index = 0
        for i, (_, path) in enumerate(script_list):
            if path == saved_script_path:
                selected_index = i
                break

        # 如果找到了保存的脚本，同步更新 selectbox 的 key 状态
        if saved_script_path and selected_index > 0:
            st.session_state['script_file_selection'] = selected_index

        selected_script_index = st.selectbox(
            tr("Script Files"),
            index=selected_index,
            options=range(len(script_list)),
            format_func=lambda x: script_list[x][0],
            key="script_file_selection"
        )

        script_path = script_list[selected_script_index][1]
        # 只有当用户实际选择了脚本时才更新路径，避免覆盖已保存的路径
        if script_path:
            st.session_state['video_clip_json_path'] = script_path
            params.video_clip_json_path = script_path
        elif saved_script_path:
            # 如果用户选择了 "None" 但之前有保存的脚本，保持原有路径
            st.session_state['video_clip_json_path'] = saved_script_path
            params.video_clip_json_path = saved_script_path

        # 处理脚本上传
        if script_path == "upload_script":
            uploaded_file = st.file_uploader(
                tr("Upload Script File"),
                type=["json"],
                accept_multiple_files=False,
            )

            if uploaded_file is not None:
                try:
                    # 读取上传的JSON内容并验证格式
                    script_content = uploaded_file.read().decode('utf-8')
                    json_data = json.loads(script_content)

                    # 保存到脚本目录
                    safe_filename = os.path.basename(uploaded_file.name)
                    script_file_path = os.path.join(script_dir, safe_filename)
                    file_name, file_extension = os.path.splitext(safe_filename)

                    # 如果文件已存在,添加时间戳
                    if os.path.exists(script_file_path):
                        timestamp = time.strftime("%Y%m%d%H%M%S")
                        file_name_with_timestamp = f"{file_name}_{timestamp}"
                        script_file_path = os.path.join(script_dir, file_name_with_timestamp + file_extension)

                    # 写入文件
                    with open(script_file_path, "w", encoding='utf-8') as f:
                        json.dump(json_data, f, ensure_ascii=False, indent=2)

                    # 更新状态
                    st.success(tr("Script Uploaded Successfully"))
                    st.session_state['video_clip_json_path'] = script_file_path
                    params.video_clip_json_path = script_file_path
                    time.sleep(1)
                    st.rerun()

                except json.JSONDecodeError:
                    st.error(tr("Invalid JSON format"))
                except Exception as e:
                    st.error(f"{tr('Upload failed')}: {str(e)}")
    else:
        # --- 功能生成模式 ---
        st.session_state['video_clip_json_path'] = selected_mode
        params.video_clip_json_path = selected_mode


def render_video_file(tr, params):
    """渲染视频文件选择"""
    video_list = [
        (tr("None"), ""),
        (tr("Upload Local Files"), "upload_local"),
        ("📁 从 Alist 选择", "alist")
    ]

    # 获取已有视频文件
    for suffix in ["*.mp4", "*.mov", "*.avi", "*.mkv"]:
        video_files = glob.glob(os.path.join(utils.video_dir(), suffix))
        for file in video_files:
            display_name = file.replace(config.root_dir, "")
            video_list.append((display_name, file))

    saved_video_path = st.session_state.get('video_origin_path', '')
    
    # 默认选中 "alist" (即 "📁 从 Alist 选择")
    default_value = "alist"
    selected_index = 0
    for i, (_, path) in enumerate(video_list):
        if path == default_value:
            selected_index = i
            break

    if saved_video_path:
        for i, (_, path) in enumerate(video_list):
            if path == saved_video_path:
                selected_index = i
                break
            try:
                if path and os.path.abspath(path) == os.path.abspath(saved_video_path):
                    selected_index = i
                    break
            except Exception:
                pass

    selected_video_index = st.selectbox(
        tr("Video File"),
        index=selected_index,
        options=range(len(video_list)),
        format_func=lambda x: video_list[x][0],
        key="video_file_selection",
    )

    video_path = video_list[selected_video_index][1]
    st.session_state['video_origin_path'] = video_path
    params.video_origin_path = video_path

    if video_path == "upload_local":
        uploaded_file = st.file_uploader(
            tr("Upload Local Files"),
            type=["mp4", "mov", "avi", "flv", "mkv"],
            accept_multiple_files=False,
        )

        if uploaded_file is not None:
            safe_filename = os.path.basename(uploaded_file.name)
            video_file_path = os.path.join(utils.video_dir(), safe_filename)
            file_name, file_extension = os.path.splitext(safe_filename)

            if os.path.exists(video_file_path):
                timestamp = time.strftime("%Y%m%d%H%M%S")
                file_name_with_timestamp = f"{file_name}_{timestamp}"
                video_file_path = os.path.join(utils.video_dir(), file_name_with_timestamp + file_extension)

            with open(video_file_path, "wb") as f:
                f.write(uploaded_file.read())
                st.success(tr("File Uploaded Successfully"))
                st.session_state['video_origin_path'] = video_file_path
                params.video_origin_path = video_file_path
                time.sleep(1)
                st.rerun()

    elif video_path == "alist":
        # 从 Alist 选择文件
        from webui.components.alist_file_browser import render_alist_file_browser

        with st.expander("🌐 Alist 文件选择器", expanded=True):
            selected_file, selected_path = render_alist_file_browser()

            if selected_file and selected_path:
                # 获取 Alist 配置
                alist_url = st.session_state.get('alist_url', config.alist.get('url', ''))
                alist_username = st.session_state.get('alist_username', config.alist.get('username', ''))
                alist_password = st.session_state.get('alist_password', config.alist.get('password', ''))

                if alist_url and alist_username and alist_password:
                    client = AlistClient(alist_url, alist_username, alist_password)

                    with st.spinner(f"正在从 Alist 下载 {selected_file}..."):
                        file_content = client.download_file(selected_path)

                        if file_content:
                            # 保存到本地
                            video_file_path = os.path.join(utils.video_dir(), selected_file)

                            with open(video_file_path, "wb") as f:
                                f.write(file_content)

                            st.success(f"✅ 成功从 Alist 下载: {selected_file}")
                            st.session_state['video_origin_path'] = video_file_path
                            params.video_origin_path = video_file_path
                        else:
                            st.error("从 Alist 下载文件失败")
                else:
                    st.warning("请先配置 Alist 连接信息")


def render_short_generate_options(tr):
    """
    渲染Short Generate模式下的特殊选项
    在Short Generate模式下，替换原有的输入框为自定义片段选项
    """
    short_drama_summary(tr)
    # 显示自定义片段数量选择器
    custom_clips = st.number_input(
        tr("自定义片段"),
        min_value=1,
        max_value=20,
        value=st.session_state.get('custom_clips', 5),
        help=tr("设置需要生成的短视频片段数量"),
        key="custom_clips_input"
    )
    st.session_state['custom_clips'] = custom_clips


def render_video_details(tr):
    """画面解说 渲染视频主题和提示词"""
    video_theme = st.text_input(tr("Video Theme"))
    custom_prompt = st.text_area(
        tr("Generation Prompt"),
        value=st.session_state.get('video_plot', ''),
        help=tr("Custom prompt for LLM, leave empty to use default prompt"),
        height=180
    )
    # 非短视频模式下显示原有的三个输入框
    input_cols = st.columns(2)

    with input_cols[0]:
        st.number_input(
            tr("Frame Interval (seconds)"),
            min_value=0,
            value=st.session_state.get('frame_interval_input', config.frames.get('frame_interval_input', 3)),
            help=tr("Frame Interval (seconds) (More keyframes consume more tokens)"),
            key="frame_interval_input"
        )

    with input_cols[1]:
        st.number_input(
            tr("Batch Size"),
            min_value=0,
            value=st.session_state.get('vision_batch_size', config.frames.get('vision_batch_size', 10)),
            help=tr("Batch Size (More keyframes consume more tokens)"),
            key="vision_batch_size"
        )
    st.session_state['video_theme'] = video_theme
    st.session_state['custom_prompt'] = custom_prompt
    return video_theme, custom_prompt


def short_drama_summary(tr):
    """短剧解说 渲染视频主题和提示词"""
    # 检查是否已经处理过字幕文件
    if 'subtitle_file_processed' not in st.session_state:
        st.session_state['subtitle_file_processed'] = False

    render_fun_asr_transcription(tr)
    
    subtitle_file = st.file_uploader(
        tr("上传字幕文件"),
        type=["srt"],
        accept_multiple_files=False,
        key="subtitle_file_uploader"  # 添加唯一key
    )
    
    # 显示当前已上传的字幕文件路径
    if 'subtitle_path' in st.session_state and st.session_state['subtitle_path']:
        st.info(f"已上传字幕: {os.path.basename(st.session_state['subtitle_path'])}")
        if st.button(tr("清除已上传字幕")):
            st.session_state['subtitle_path'] = None
            st.session_state['subtitle_content'] = None
            st.session_state['subtitle_file_processed'] = False
            st.rerun()
    
    # 只有当有文件上传且尚未处理时才执行处理逻辑
    if subtitle_file is not None and not st.session_state['subtitle_file_processed']:
        try:
            # 清理文件名，防止路径污染和路径遍历攻击
            safe_filename = os.path.basename(subtitle_file.name)

            decoded = decode_subtitle_bytes(subtitle_file.getvalue())
            script_content = decoded.text
            detected_encoding = decoded.encoding

            if not script_content:
                st.error(tr("无法读取字幕文件，请检查文件编码（支持 UTF-8、UTF-16、GBK、GB2312）"))
                st.stop()

            # 验证字幕内容（简单检查）
            if len(script_content.strip()) < 10:
                st.warning(tr("字幕文件内容似乎为空，请检查文件"))

            # 保存到字幕目录
            script_file_path = os.path.join(utils.subtitle_dir(), safe_filename)
            file_name, file_extension = os.path.splitext(safe_filename)

            # 如果文件已存在,添加时间戳
            if os.path.exists(script_file_path):
                timestamp = time.strftime("%Y%m%d%H%M%S")
                file_name_with_timestamp = f"{file_name}_{timestamp}"
                script_file_path = os.path.join(utils.subtitle_dir(), file_name_with_timestamp + file_extension)

            # 直接写入SRT内容（统一使用 UTF-8）
            with open(script_file_path, "w", encoding='utf-8') as f:
                f.write(script_content)

            # 更新状态
            st.success(
                f"{tr('字幕上传成功')} "
                f"(编码: {detected_encoding.upper()}, "
                f"大小: {len(script_content)} 字符)"
            )
            st.session_state['subtitle_path'] = script_file_path
            st.session_state['subtitle_content'] = script_content
            st.session_state['subtitle_file_processed'] = True  # 标记已处理

            # 避免使用rerun，使用更新状态的方式
            # st.rerun()

        except Exception as e:
            st.error(f"{tr('Upload failed')}: {str(e)}")

    # 名称输入框
    video_theme = st.text_input(tr("短剧名称"))
    st.session_state['video_theme'] = video_theme
    # 数字输入框
    temperature = st.slider("temperature", 0.0, 2.0, 0.7)
    st.session_state['temperature'] = temperature
    return video_theme


def render_fun_asr_transcription(tr):
    """使用阿里百炼 Fun-ASR 从本地音视频转写生成字幕。"""
    def clear_fun_asr_subtitle_state():
        st.session_state['subtitle_path'] = None
        st.session_state['subtitle_content'] = None
        st.session_state['subtitle_file_processed'] = False

    with st.expander("阿里百炼 Fun-ASR 字幕转录", expanded=False):
        st.caption("上传本地音频/视频后，将自动上传到阿里百炼临时存储并通过 fun-asr 生成 SRT 字幕。")
        st.markdown(
            "API Key 获取地址："
            "[https://bailian.console.aliyun.com/?tab=model#/api-key]"
            "(https://bailian.console.aliyun.com/?tab=model#/api-key)"
        )

        api_key = st.text_input(
            "阿里百炼 API Key",
            value=config.fun_asr.get("api_key", ""),
            type="password",
            help="请输入你自己的阿里百炼 API Key；保存配置后会写入本地 config.toml",
            key="fun_asr_api_key",
        )
        uploaded_media = st.file_uploader(
            "上传需要转录的音频/视频",
            type=[
                "aac", "amr", "avi", "flac", "flv", "m4a", "mkv", "mov",
                "mp3", "mp4", "mpeg", "ogg", "opus", "wav", "webm", "wma", "wmv",
            ],
            accept_multiple_files=False,
            key="fun_asr_media_uploader",
        )

        if st.button("转写生成字幕", key="fun_asr_transcribe"):
            if not api_key.strip():
                clear_fun_asr_subtitle_state()
                st.error("请先输入阿里百炼 API Key")
                return
            if uploaded_media is None:
                clear_fun_asr_subtitle_state()
                st.error("请先上传需要转录的音频或视频文件")
                return

            try:
                clear_fun_asr_subtitle_state()
                from app.services import fun_asr_subtitle

                config.fun_asr["api_key"] = api_key.strip()
                config.fun_asr["model"] = "fun-asr"
                config.save_config()

                temp_dir = utils.temp_dir("fun_asr")
                safe_filename = os.path.basename(uploaded_media.name)
                media_path = os.path.join(temp_dir, safe_filename)
                file_name, file_extension = os.path.splitext(safe_filename)
                if os.path.exists(media_path):
                    timestamp = time.strftime("%Y%m%d%H%M%S")
                    media_path = os.path.join(temp_dir, f"{file_name}_{timestamp}{file_extension}")

                with open(media_path, "wb") as f:
                    f.write(uploaded_media.getbuffer())

                subtitle_name = f"{os.path.splitext(os.path.basename(media_path))[0]}_fun_asr.srt"
                subtitle_path = os.path.join(utils.subtitle_dir(), subtitle_name)

                with st.spinner("正在使用阿里百炼 Fun-ASR 转写字幕，请稍候..."):
                    generated_path = fun_asr_subtitle.create_with_fun_asr(
                        local_file=media_path,
                        subtitle_file=subtitle_path,
                        api_key=api_key.strip(),
                    )

                if not generated_path or not os.path.exists(generated_path):
                    clear_fun_asr_subtitle_state()
                    st.error("Fun-ASR 转写失败：未生成字幕文件")
                    return

                with open(generated_path, "r", encoding="utf-8") as f:
                    subtitle_content = f.read()

                st.session_state['subtitle_path'] = generated_path
                st.session_state['subtitle_content'] = subtitle_content
                st.session_state['subtitle_file_processed'] = True
                st.success(f"字幕转写成功: {os.path.basename(generated_path)}")
            except Exception as e:
                clear_fun_asr_subtitle_state()
                logger.error(f"Fun-ASR 字幕转写失败: {traceback.format_exc()}")
                st.error(f"Fun-ASR 字幕转写失败: {str(e)}")


def render_gaming_details(tr):
    """游戏解说 渲染视频主题和自定义提示词"""
    st.info("""
    **游戏解说模式**：使用自定义 Prompt 生成游戏解说脚本

    **视觉对齐公式**：`[主体]+[场景]+[动作]+[镜头语言]+[氛围/风格]`

    示例：`[玩家一]+[剧毒沼泽]+[利用完美格挡弹反BOSS重击]+[镜头锁定特写]+[硬核/紧张/极具压迫感]`
    """)

    # 游戏主题
    video_theme = st.text_input(
        tr("游戏名称/主题"),
        value=st.session_state.get('gaming_video_theme', ''),
        placeholder="例如：双影奇境 硬核攻略"
    )
    st.session_state['gaming_video_theme'] = video_theme

    # 预设风格选择
    style_options = {
        "hardcore": "硬核攻略（专业、冷静、干练）",
        "funny": "沙雕吐槽（幽默、接地气、节目效果）",
        "custom": "自定义 Prompt"
    }
    selected_style = st.selectbox(
        tr("解说风格"),
        options=list(style_options.keys()),
        format_func=lambda x: style_options[x],
        help="选择预设风格或使用自定义 Prompt"
    )

    # 根据风格显示不同的 Prompt
    if selected_style == "hardcore":
        default_prompt = """# 任务：分析《双影奇境》的高端操作片段，并生成硬核技术流解说脚本。

# 视觉对齐公式：
请严格遵循 [主体]+[场景]+[动作]+[镜头语言]+[氛围/风格] 的逻辑来理解画面并撰写解说。

# 解说词要求：
1. 语言风格：专业、冷静、干练，多用游戏术语（如：完美格挡、无敌帧、卡身位）
2. 时间轴容错：每段解说词后必须预留 1-2 秒的视觉展示时间

# 输出格式：
{
  "clip_style": "hardcore",
  "visual_anchor": "画面描述",
  "narration": "硬核解说词"
}"""
    elif selected_style == "funny":
        default_prompt = """# 任务：分析《双影奇境》双人联机片段，生成搞笑、吐槽风格的解说脚本。

# 视觉对齐公式：
请严格遵循 [主体]+[场景]+[动作]+[镜头语言]+[氛围/风格] 的逻辑来理解画面并撰写解说。

# 解说词要求：
1. 语言风格：接地气、幽默、充满戏剧性，多描述双人联机时的"互坑"细节
2. 时间轴容错：在"死亡"或"失误"的精彩瞬间留白 2 秒

# 输出格式：
{
  "clip_style": "funny",
  "visual_anchor": "画面描述",
  "narration": "沙雕解说词"
}"""
    else:
        default_prompt = st.session_state.get('gaming_custom_prompt', '')

    # 自定义 Prompt 输入
    custom_prompt = st.text_area(
        tr("自定义 Prompt"),
        value=st.session_state.get('gaming_custom_prompt', default_prompt),
        help="输入自定义的解说 Prompt，支持视觉对齐公式",
        height=250
    )
    st.session_state['gaming_custom_prompt'] = custom_prompt

    # 视频参数设置
    input_cols = st.columns(2)
    with input_cols[0]:
        st.number_input(
            tr("帧间隔（秒）"),
            min_value=1,
            value=st.session_state.get('gaming_frame_interval', 3),
            help="关键帧提取间隔",
            key="gaming_frame_interval"
        )

    with input_cols[1]:
        st.number_input(
            tr("批次大小"),
            min_value=1,
            value=st.session_state.get('gaming_batch_size', 10),
            help="每批处理的帧数",
            key="gaming_batch_size"
        )

    return video_theme, custom_prompt


def render_script_buttons(tr, params):
    """渲染脚本操作按钮"""
    # 获取当前选择的脚本类型
    script_path = st.session_state.get('video_clip_json_path', '')

    # 仅在生成模式下渲染“生成脚本”按钮，选择已有的 JSON 脚本时无需“加载”按钮（已由选择框自动即时加载路径）
    if script_path in ["auto", "short", "summary", "gaming"]:
        if script_path == "auto":
            button_name = tr("Generate Video Script")
        elif script_path == "short":
            button_name = tr("Generate Short Video Script")
        elif script_path == "summary":
            button_name = tr("生成短剧解说脚本")
        elif script_path == "gaming":
            button_name = tr("🎮 生成游戏解说脚本")

        if st.button(button_name, key="script_action", use_container_width=True, type="primary"):
            if script_path == "auto":
                # 执行纪录片视频脚本生成（视频无字幕无配音）
                generate_script_docu(params)
            elif script_path == "short":
                # 执行 短剧混剪 脚本生成
                custom_clips = st.session_state.get('custom_clips')
                generate_script_short(tr, params, custom_clips)
            elif script_path == "summary":
                # 执行 短剧解说 脚本生成
                subtitle_path = st.session_state.get('subtitle_path')
                video_theme = st.session_state.get('video_theme')
                temperature = st.session_state.get('temperature')
                generate_script_short_sunmmary(params, subtitle_path, video_theme, temperature)
            elif script_path == "gaming":
                # 执行游戏解说脚本生成
                generate_script_gaming(params)

    # =============================================
    # 断点续传 + 关键帧预览（仅游戏解说模式）
    # =============================================
    if script_path == "gaming" and params.video_origin_path:
        st.divider()
        with st.expander("🔄 本地断点调试 & 关键帧预览 (开发者选项)", expanded=False):
            st.markdown("### 🔄 断点续传 & 关键帧预览")

            service = DocumentaryFrameAnalysisService()
            frame_interval = st.session_state.get('gaming_frame_interval', 3)
            batch_size = st.session_state.get('gaming_batch_size', 10)

            checkpoint_status = service.get_checkpoint_status(
                video_path=params.video_origin_path,
                frame_interval=float(frame_interval),
                batch_size=batch_size,
            )

            col_preview, col_checkpoint = st.columns(2)

            with col_preview:
                st.markdown("**🖼️ 关键帧预览**")
                if st.button("🔍 查看已提取的关键帧", key="preview_keyframes"):
                    with st.spinner("正在加载关键帧..."):
                        try:
                            keyframe_files = service._load_or_extract_keyframes(
                                params.video_origin_path, float(frame_interval)
                            )
                            if keyframe_files:
                                cols = st.columns(4)
                                for i, kf in enumerate(keyframe_files[:16]):
                                    with cols[i % 4]:
                                        st.image(kf, caption=os.path.basename(kf)[:30], width=120)
                                if len(keyframe_files) > 16:
                                    st.caption(f"共 {len(keyframe_files)} 帧，以上显示前 16 张")
                            else:
                                st.info("尚未提取关键帧，请先点击「生成游戏解说脚本」")
                        except Exception as e:
                            st.error(f"预览失败: {str(e)}")

            with col_checkpoint:
                st.markdown("**💾 断点状态**")
                if checkpoint_status["exists"]:
                    completed = checkpoint_status["completed_batches"]
                    total = checkpoint_status["total_batches"]
                    pct = completed / total * 100 if total > 0 else 0
                    st.success(f"✅ 已分析 {completed}/{total} 个批次（{pct:.0f}%）")
                    st.caption(f"批次索引: {checkpoint_status['completed_indices']}")
                    col_clear, col_resume = st.columns(2)
                    with col_clear:
                        if st.button("🗑️ 清除断点", key="clear_checkpoint"):
                            service.clear_checkpoint(
                                params.video_origin_path, float(frame_interval), batch_size
                            )
                            st.rerun()
                    with col_resume:
                        st.info("点击「🎮 生成游戏解说脚本」将从断点继续")
                else:
                    st.info("暂无断点记录，从头开始分析")

        st.divider()

    # =============================================
    # Kaggle 离线处理（仅游戏解说模式）
    # =============================================
    if script_path == "gaming":
        st.markdown("### 📦 Kaggle GPU 视频识别")
        st.caption("使用免费 GPU 资源（T4 x2/P100）一键离线运行 Qwen2-VL 模型进行超快视频与画面事件识别")

        # Alist 配置检测
        from app.utils.alist_client import get_alist_client
        alist_client = get_alist_client()
        alist_configured = alist_client is not None

        # 1. 高级参数与配置
        with st.expander("⚙️ Kaggle & Alist 账号与高级参数配置 (首次使用或需修改时展开)", expanded=False):
            if not alist_configured:
                st.warning("⚠️ Alist 未配置，文件传输将使用本地手动方式。配置 [alist] 可开启自动上传/下载")
                alist_enabled = False
            else:
                alist_enabled = st.checkbox(
                    "☁️ 使用 Alist 自动传输",
                    value=st.session_state.get("kaggle_alist_enabled", True),
                    help="开启后：导出自动上传 Alist，导入自动从 Alist 下载，Kaggle 端无需手动上传/下载文件",
                    key="kaggle_alist_enabled"
                )

            st.markdown("---")
            st.markdown("**Kaggle Dataset 设置**")
            dataset_cols = st.columns(2)
            with dataset_cols[0]:
                kaggle_username = st.text_input(
                    "Kaggle Username",
                    value=os.getenv("KAGGLE_USERNAME", config.app.get("kaggle_username", "")),
                    key="kaggle_username_input",
                    help="用于写入 dataset-metadata.json。Token 不会写入文件。",
                )
            with dataset_cols[1]:
                dataset_slug = st.text_input(
                    "Dataset Slug",
                    value=config.app.get("kaggle_dataset_slug", "narratoai-video-understanding-tasks"),
                    key="kaggle_dataset_slug_input",
                    help="建议固定一个 Dataset，后续用 version 更新不同剪辑任务。",
                )

            upload_cols = st.columns(2)
            with upload_cols[0]:
                upload_to_kaggle = st.checkbox(
                    "生成后直接上传/更新 Kaggle Dataset",
                    value=True,
                    key="kaggle_upload_enabled",
                )
            with upload_cols[1]:
                create_dataset = st.checkbox(
                    "第一次创建 Dataset",
                    value=False,
                    key="kaggle_create_dataset",
                    help="第一次勾选；之后取消勾选，使用 kaggle datasets version 更新版本。",
                )

            st.markdown("---")
            st.markdown("**🚀 Kaggle 运行高级参数**")
            kaggle_run_cols = st.columns([1, 1, 1])
            with kaggle_run_cols[0]:
                poll_interval = st.number_input(
                    "轮询间隔（秒）",
                    min_value=10,
                    max_value=300,
                    value=60,
                    key="kaggle_poll_interval",
                    help="每隔多少秒查询一次 Kernel 状态",
                )
            with kaggle_run_cols[1]:
                timeout_minutes = st.number_input(
                    "超时（分钟）",
                    min_value=5,
                    max_value=240,
                    value=120,
                    key="kaggle_timeout_minutes",
                    help="超过此时间未完成则报错",
                )
            with kaggle_run_cols[2]:
                swanlab_mode = st.selectbox(
                    "SwanLab 模式",
                    options=["off", "local", "online"],
                    index=1,
                    key="kaggle_swanlab_mode",
                    help="online 需要 SWANLAB_API_KEY 环境变量",
                )

        # Get alist_enabled value
        alist_enabled = st.session_state.get("kaggle_alist_enabled", alist_configured)

        col1, col2 = st.columns(2)
        with col1:
            st.markdown("#### 📤 1. 生成与提交任务")
            
            # Show active task card
            existing_task_dir = st.session_state.get("kaggle_task_dir")
            existing_task_name = st.session_state.get("kaggle_task_name")
            existing_zip_path = st.session_state.get("kaggle_zip_path")
            existing_kernel_slug = st.session_state.get("kaggle_kernel_slug")
            existing_dataset_ref = st.session_state.get("kaggle_dataset_ref")
            
            if existing_task_name and existing_task_dir:
                st.markdown(f"""
                <div style="background-color: rgba(255,255,255,0.05); padding: 12px; border-radius: 8px; border-left: 4px solid #4A90E2; margin-bottom: 12px;">
                    <p style="margin: 0; font-weight: bold; color: #4A90E2; font-size: 14px;">📋 当前活动任务</p>
                    <p style="margin: 4px 0 0 0; font-size: 12px; opacity: 0.9;">任务名称: <code>{existing_task_name}</code></p>
                    <p style="margin: 2px 0 0 0; font-size: 12px; opacity: 0.7;">本地路径: <code>{existing_task_dir}</code></p>
                </div>
                """, unsafe_allow_html=True)
                
                if existing_zip_path and os.path.exists(existing_zip_path):
                    with open(existing_zip_path, "rb") as f:
                        zip_data = f.read()
                    st.download_button(
                        "⬇️ 下载离线任务包 (用于手动上传)",
                        data=zip_data,
                        file_name=os.path.basename(existing_zip_path),
                        mime="application/zip",
                        key="kaggle_zip_download",
                        use_container_width=True
                    )

            if st.button("📦 一键生成并提交新任务", key="kaggle_export", use_container_width=True, type="primary"):
                if not params.video_origin_path:
                    st.error("请先选择视频文件")
                else:
                    with st.spinner("正在生成 Kaggle Dataset 任务目录..."):
                        dataset_service = KaggleVideoUnderstandingService()
                        gaming_custom_prompt = st.session_state.get('gaming_custom_prompt', '')
                        gaming_video_theme = st.session_state.get('gaming_video_theme', '游戏精彩片段')
                        frame_interval = st.session_state.get('gaming_frame_interval', 3)
                        batch_size = st.session_state.get('gaming_batch_size', 8)
                        subtitle_path = st.session_state.get('subtitle_path', '')
                        try:
                            export_result = dataset_service.build_dataset_task(
                                video_path=params.video_origin_path,
                                subtitle_path=subtitle_path or None,
                                kaggle_username=kaggle_username,
                                dataset_slug=dataset_slug,
                                video_theme=gaming_video_theme,
                                custom_prompt=gaming_custom_prompt,
                                frame_interval_seconds=float(frame_interval),
                                refine_interval_seconds=0.75,
                                batch_size=batch_size,
                            )
                            zip_path = export_result["archive_path"]
                            task_dir = export_result["task_dir"]
                            task_name = export_result["task_name"]
                            dataset_ref = export_result["dataset_ref"]

                            st.session_state["kaggle_zip_path"] = zip_path
                            st.session_state["kaggle_task_name"] = task_name
                            st.session_state["kaggle_task_dir"] = task_dir
                            st.session_state["kaggle_kernel_slug"] = export_result.get("kernel_slug", "")
                            st.session_state["kaggle_dataset_ref"] = dataset_ref

                            video_file_size = os.path.getsize(params.video_origin_path) if os.path.exists(params.video_origin_path) else 0
                            video_size_mb = video_file_size / (1024 * 1024)

                            st.success(f"✅ Dataset 任务 `{task_name}` 生成成功！视频大小 {video_size_mb:.1f} MB")

                            if upload_to_kaggle:
                                with st.spinner("正在调用 Kaggle CLI 上传/更新 Dataset..."):
                                    upload_result = dataset_service.upload_dataset(
                                        task_dir=task_dir,
                                        create=create_dataset,
                                        message=f"NarratoAI video task {task_name}",
                                    )
                                st.success("✅ Kaggle Dataset 上传成功")
                            st.rerun()

                        except Exception as e:
                            st.error(f"导出失败: {str(e)}")
                            logger.exception(f"Kaggle 导出失败\n{traceback.format_exc()}")
            
            # Online Run Block
            if existing_task_dir:
                st.markdown("---")
                st.markdown("##### 🚀 启动 GPU 运行并生成脚本")
                st.caption("自动推送 Notebook → 启动 GPU Kernel → 轮询状态 → 自动转换并重新加载")
                
                if st.button("🚀 一键在 Kaggle 运行视频理解", key="kaggle_run_online", use_container_width=True):
                    with st.spinner("🚀 正在推送 Notebook 到 Kaggle..."):
                        try:
                            dataset_service = KaggleVideoUnderstandingService()
                            run_result = dataset_service.run_on_kaggle(
                                task_dir=existing_task_dir,
                                kernel_slug=existing_kernel_slug or "",
                                dataset_ref=existing_dataset_ref or f"{kaggle_username}/{dataset_slug}",
                                poll_interval_seconds=int(poll_interval),
                                timeout_seconds=int(timeout_minutes * 60),
                                swanlab_mode=swanlab_mode if swanlab_mode != "off" else "off",
                            )

                            st.success("✅ Kaggle GPU 运行完成！")
                            result_cols = st.columns(2)
                            with result_cols[0]:
                                if "event_count" in run_result:
                                    st.metric("事件数", run_result["event_count"])
                            with result_cols[1]:
                                if "clip_count" in run_result:
                                    st.metric("候选片段", run_result["clip_count"])

                            st.session_state["kaggle_run_output_base"] = run_result.get("output_base", "")
                            st.session_state["kaggle_run_files"] = run_result.get("files", {})

                            if "candidate_clips" in run_result.get("files", {}):
                                st.info("⏭️ 自动进入生成剪辑脚本流程...")
                                clips_path = run_result["files"]["candidate_clips"]
                                auto_task_id = f"kaggle_auto_{int(time.time())}"
                                auto_task_dir = utils.task_dir(auto_task_id)
                                os.makedirs(auto_task_dir, exist_ok=True)
                                auto_script_path = os.path.join(auto_task_dir, "script.json")
                                script_result = dataset_service.candidate_clips_to_script(
                                    candidate_clips_path=clips_path,
                                    output_script_path=auto_script_path,
                                )
                                st.session_state["video_clip_json"] = script_result["video_clip_json"]
                                st.session_state["video_clip_json_path"] = auto_script_path
                                st.success(f"✅ 剪辑脚本已生成：{script_result['selected_count']} 个片段")
                                st.rerun()
                        except Exception as run_e:
                            st.error(f"❌ Kaggle 运行失败: {str(run_e)}")
                            logger.exception(f"Kaggle run_on_kaggle 失败\n{traceback.format_exc()}")

        with col2:
            st.markdown("#### 📥 2. 导入结果生成剪辑脚本")
            
            if alist_enabled and alist_configured:
                st.markdown("**☁️ 从 Alist 导入结果**")
                default_task_name = st.session_state.get("kaggle_task_name", "")
                alist_task_name_input = st.text_input(
                    "Task Name",
                    value=st.session_state.get("kaggle_alist_task_name", default_task_name),
                    placeholder="例如: kaggle_20250514_203400",
                    key="kaggle_alist_task_name_input",
                    help="填写导出时的 Task Name，脚本会从 Alist 下载对应结果"
                )
                st.session_state["kaggle_alist_task_name"] = alist_task_name_input
                if st.button("☁️ 从 Alist 下载 analysis_result.json 并继续", key="kaggle_from_alist", use_container_width=True):
                    if not alist_task_name_input:
                        st.error("请先填写 Task Name")
                    else:
                        with st.spinner("从 Alist 下载 analysis_result.json..."):
                            try:
                                service = DocumentaryFrameAnalysisService()
                                batch_results = service.import_kaggle_results(
                                    results_json_path=None,
                                    video_path=params.video_origin_path,
                                    frame_interval_input=st.session_state.get('gaming_frame_interval', 3),
                                    batch_size=st.session_state.get('gaming_batch_size', 10),
                                    download_from_alist=True,
                                    alist_task_name=alist_task_name_input,
                                )
                                video_clip_json = service._build_video_clip_json(batch_results)
                                st.session_state["video_clip_json"] = video_clip_json
                                task_id = f"gaming_alist_{int(time.time())}"
                                task_dir = utils.task_dir(task_id)
                                os.makedirs(task_dir, exist_ok=True)
                                script_path = os.path.join(task_dir, "script.json")
                                with open(script_path, "w", encoding="utf-8") as f:
                                    json.dump(video_clip_json, f, ensure_ascii=False, indent=2)
                                st.session_state["video_clip_json_path"] = script_path
                                st.success(f"✅ Kaggle 结果已导入，共 {len(video_clip_json)} 个片段")
                                st.rerun()
                            except Exception as e:
                                st.error(f"导入失败: {str(e)}")
                                logger.exception(f"Kaggle Alist 导入失败\n{traceback.format_exc()}")
            else:
                st.markdown("**📥 上传本地结果文件**")
                uploaded_results = st.file_uploader(
                    "选择 candidate_clips.json / analysis_result.json",
                    type=["json"],
                    key="kaggle_results_upload",
                    help="推荐上传 candidate_clips.json；兼容旧版 analysis_result.json"
                )
                if uploaded_results:
                    results_path = os.path.join(utils.temp_dir(), uploaded_results.name)
                    with open(results_path, "wb") as f:
                        f.write(uploaded_results.getvalue())
                    st.session_state["kaggle_results_path"] = results_path
                    st.success("✅ 结果文件已加载，可以生成剪辑脚本")

                if st.button("🎮 从 Kaggle 结果生成剪辑脚本", key="kaggle_continue", use_container_width=True):
                    results_path = st.session_state.get("kaggle_results_path")
                    if not results_path:
                        st.error("请先上传 candidate_clips.json 或 analysis_result.json 文件")
                    else:
                        with st.spinner("正在从 Kaggle 结果生成剪辑脚本..."):
                            try:
                                task_id = f"gaming_kaggle_{int(time.time())}"
                                task_dir = utils.task_dir(task_id)
                                os.makedirs(task_dir, exist_ok=True)
                                script_path = os.path.join(task_dir, "script.json")

                                with open(results_path, "r", encoding="utf-8") as f:
                                    result_payload = json.load(f)

                                if isinstance(result_payload, dict) and "clips" in result_payload:
                                    dataset_service = KaggleVideoUnderstandingService()
                                    script_result = dataset_service.candidate_clips_to_script(
                                        candidate_clips_path=results_path,
                                        output_script_path=script_path,
                                    )
                                    video_clip_json = script_result["video_clip_json"]
                                else:
                                    service = DocumentaryFrameAnalysisService()
                                    frame_interval = st.session_state.get('gaming_frame_interval', 3)
                                    batch_size = st.session_state.get('gaming_batch_size', 10)
                                    batch_results = service.import_kaggle_results(
                                        results_json_path=results_path,
                                        video_path=params.video_origin_path,
                                        frame_interval_input=frame_interval,
                                        batch_size=batch_size,
                                    )
                                    video_clip_json = service._build_video_clip_json(batch_results)
                                    with open(script_path, "w", encoding="utf-8") as f:
                                        json.dump(video_clip_json, f, ensure_ascii=False, indent=2)

                                st.session_state["video_clip_json"] = video_clip_json
                                st.session_state["video_clip_json_path"] = script_path

                                st.success(f"✅ Kaggle 结果已导入，共 {len(video_clip_json)} 个片段")
                                st.rerun()
                            except Exception as e:
                                st.error(f"继续生成失败: {str(e)}")
                                logger.exception(f"Kaggle 继续生成失败\n{traceback.format_exc()}")



def load_script(tr, script_path):
    """加载脚本文件"""
    try:
        with open(script_path, 'r', encoding='utf-8') as f:
            script = f.read()
            script = utils.clean_model_output(script)
            st.session_state['video_clip_json'] = json.loads(script)
            st.success(tr("Script loaded successfully"))
            st.rerun()
    except Exception as e:
        logger.error(f"加载脚本文件时发生错误\n{traceback.format_exc()}")
        st.error(f"{tr('Failed to load script')}: {str(e)}")


def save_script_with_validation(tr, video_clip_json_details):
    """保存视频脚本（包含格式验证）"""
    if not video_clip_json_details:
        st.error(tr("请输入视频脚本"))
        st.stop()

    # 第一步：格式验证
    with st.spinner("正在验证脚本格式..."):
        try:
            result = check_script.check_format(video_clip_json_details)
            if not result.get('success'):
                # 格式验证失败，显示详细错误信息
                error_message = result.get('message', '未知错误')
                error_details = result.get('details', '')

                st.error(f"**脚本格式验证失败**")
                st.error(f"**错误信息：** {error_message}")
                if error_details:
                    st.error(f"**详细说明：** {error_details}")

                # 显示正确格式示例
                st.info("**正确的脚本格式示例：**")
                example_script = [
                    {
                        "_id": 1,
                        "timestamp": "00:00:00,600-00:00:07,559",
                        "picture": "工地上，蔡晓艳奋力救人，场面混乱",
                        "narration": "灾后重建，工地上险象环生！泼辣女工蔡晓艳挺身而出，救人第一！",
                        "OST": 0
                    },
                    {
                        "_id": 2,
                        "timestamp": "00:00:08,240-00:00:12,359",
                        "picture": "领导视察，蔡晓艳不屑一顾",
                        "narration": "播放原片4",
                        "OST": 1
                    }
                ]
                st.code(json.dumps(example_script, ensure_ascii=False, indent=2), language='json')
                st.stop()

        except Exception as e:
            st.error(f"格式验证过程中发生错误: {str(e)}")
            st.stop()

    # 第二步：保存脚本
    with st.spinner(tr("Save Script")):
        script_dir = utils.script_dir()
        timestamp = time.strftime("%Y-%m%d-%H%M%S")
        save_path = os.path.join(script_dir, f"{timestamp}.json")

        try:
            data = json.loads(video_clip_json_details)
            with open(save_path, 'w', encoding='utf-8') as file:
                json.dump(data, file, ensure_ascii=False, indent=4)
                st.session_state['video_clip_json'] = data
                st.session_state['video_clip_json_path'] = save_path
                
                # 标记需要切换到文件选择模式（在下次渲染前处理）
                st.session_state['_switch_to_file_mode'] = True

                # 更新配置
                config.app["video_clip_json_path"] = save_path

                # 显示成功消息
                st.success("✅ 脚本格式验证通过，保存成功！")

                # 强制重新加载页面更新选择框
                time.sleep(0.5)  # 给一点时间让用户看到成功消息
                st.rerun()

        except Exception as err:
            st.error(f"{tr('Failed to save script')}: {str(err)}")
            st.stop()


# crop_video函数已移除 - 现在使用统一裁剪策略，不再需要预裁剪步骤


def get_script_params():
    """获取脚本参数"""
    return {
        'video_language': st.session_state.get('video_language', ''),
        'video_clip_json_path': st.session_state.get('video_clip_json_path', ''),
        'video_origin_path': st.session_state.get('video_origin_path', ''),
        'video_name': st.session_state.get('video_name', ''),
        'video_plot': st.session_state.get('video_plot', '')
    }
