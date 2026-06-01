import streamlit as st
import os
import sys
import time
from loguru import logger
from app.config import config
from webui.components import (
    basic_settings, video_settings, audio_settings, subtitle_settings,
    script_settings, system_settings, task_monitor
)
from app.utils import utils
from app.utils import ffmpeg_utils
from app.models.schema import VideoClipParams, VideoAspect


# 初始化配置 - 必须是第一个 Streamlit 命令
st.set_page_config(
    page_title="NarratoAI",
    page_icon="📽️",
    layout="wide",
    initial_sidebar_state="auto",
    menu_items={
        "Report a bug": "https://github.com/linyqh/NarratoAI/issues",
        'About': f"# Narrato:blue[AI] :sunglasses: 📽️ \n #### Version: v{config.project_version} \n "
                 f"自动化影视解说视频详情请移步：https://github.com/linyqh/NarratoAI"
    },
)

# 设置页面样式
hide_streamlit_style = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Outfit:wght@300;400;500;600;700&display=swap');

/* Main app background & typography */
.stApp {
    font-family: 'Outfit', -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif !important;
    background-color: #0B0E14 !important;
    color: #E2E8F0 !important;
}

#root > div:nth-child(1) > div > div > div > div > section > div {
    padding-top: 1.5rem;
    padding-bottom: 10px;
    padding-left: 30px;
    padding-right: 30px;
}

/* Hide streamlit default headers and footers */
header {visibility: hidden;}
footer {visibility: hidden;}

/* Custom premium header styling */
.narrato-header-container {
    background: linear-gradient(135deg, rgba(30, 41, 59, 0.45) 0%, rgba(15, 23, 42, 0.65) 100%);
    border: 1px solid rgba(255, 255, 255, 0.08);
    border-radius: 12px;
    padding: 14px 18px;
    margin-bottom: 18px;
    box-shadow: 0 10px 40px rgba(0, 0, 0, 0.3);
    backdrop-filter: blur(12px);
    -webkit-backdrop-filter: blur(12px);
}

.narrato-header-title {
    font-size: 2rem;
    font-weight: 700;
    margin-bottom: 2px;
    background: linear-gradient(90deg, #38BDF8 0%, #818CF8 50%, #C084FC 100%);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    text-shadow: 0 0 50px rgba(129, 140, 248, 0.25);
    display: flex;
    align-items: center;
    gap: 12px;
}

.narrato-header-subtitle {
    font-size: 0.9rem;
    color: #94A3B8;
    letter-spacing: 0;
}

.narrato-inline-status {
    display: flex;
    flex-wrap: wrap;
    gap: 8px;
    margin: 6px 0 14px;
}

.narrato-inline-status span {
    display: inline-flex;
    align-items: center;
    min-height: 28px;
    padding: 4px 10px;
    border-radius: 8px;
    background: rgba(15, 23, 42, 0.55);
    border: 1px solid rgba(56, 189, 248, 0.22);
    color: #CBD5E1;
    font-size: 0.86rem;
}

/* Premium Card style for each setting panel */
div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column"] {
    background: rgba(17, 24, 39, 0.45) !important;
    border: 1px solid rgba(255, 255, 255, 0.06) !important;
    border-radius: 14px !important;
    padding: 20px !important;
    margin-bottom: 20px !important;
    box-shadow: 0 8px 32px rgba(0, 0, 0, 0.2) !important;
    transition: all 0.3s cubic-bezier(0.4, 0, 0.2, 1) !important;
}

div[data-testid="stVerticalBlock"] > div[style*="flex-direction: column"]:hover {
    border-color: rgba(129, 140, 248, 0.35) !important;
    box-shadow: 0 12px 40px rgba(129, 140, 248, 0.12) !important;
    transform: translateY(-2px) !important;
}

/* Beautiful style for expanders */
div[data-testid="stExpander"] {
    background: rgba(30, 41, 59, 0.25) !important;
    border: 1px solid rgba(255, 255, 255, 0.06) !important;
    border-radius: 12px !important;
    margin-bottom: 14px !important;
    box-shadow: 0 4px 15px rgba(0,0,0,0.12) !important;
    transition: all 0.2s ease !important;
}

div[data-testid="stExpander"]:hover {
    border-color: rgba(56, 189, 248, 0.3) !important;
}

/* Form headers and titles styling */
h1, h2, h3 {
    font-family: 'Outfit', sans-serif !important;
    color: #FFFFFF !important;
    font-weight: 600 !important;
}

h3 {
    border-bottom: 2px solid rgba(129, 140, 248, 0.2) !important;
    padding-bottom: 8px !important;
    margin-bottom: 16px !important;
}

/* Input, Select & Widgets styling */
div[data-baseweb="select"], div[data-baseweb="input"], .stTextArea textarea {
    background-color: rgba(15, 23, 42, 0.6) !important;
    border-radius: 10px !important;
    border: 1px solid rgba(255, 255, 255, 0.08) !important;
    color: #E2E8F0 !important;
    transition: all 0.2s ease !important;
}

div[data-baseweb="select"]:hover, div[data-baseweb="input"]:hover, .stTextArea textarea:hover {
    border-color: rgba(56, 189, 248, 0.45) !important;
}

/* Primary generate button */
button[data-testid="baseButton-primary"] {
    background: linear-gradient(135deg, #6366F1 0%, #4F46E5 100%) !important;
    color: #FFFFFF !important;
    border: none !important;
    border-radius: 12px !important;
    font-weight: 600 !important;
    font-size: 1.1rem !important;
    padding: 16px 32px !important;
    box-shadow: 0 6px 20px rgba(79, 70, 229, 0.4) !important;
    transition: all 0.3s cubic-bezier(0.175, 0.885, 0.32, 1.275) !important;
    text-transform: uppercase !important;
    letter-spacing: 1px !important;
    width: 100% !important;
    cursor: pointer !important;
}

button[data-testid="baseButton-primary"]:hover {
    transform: scale(1.02) !important;
    box-shadow: 0 8px 25px rgba(79, 70, 229, 0.6) !important;
    background: linear-gradient(135deg, #818CF8 0%, #6366F1 100%) !important;
}

/* Secondary export button */
button[data-testid="baseButton-secondary"] {
    background: rgba(30, 41, 59, 0.65) !important;
    color: #E2E8F0 !important;
    border: 1px solid rgba(255, 255, 255, 0.15) !important;
    border-radius: 12px !important;
    font-weight: 600 !important;
    font-size: 1.1rem !important;
    padding: 16px 32px !important;
    transition: all 0.3s ease !important;
    width: 100% !important;
    backdrop-filter: blur(6px);
}

button[data-testid="baseButton-secondary"]:hover {
    background: rgba(30, 41, 59, 0.95) !important;
    border-color: rgba(129, 140, 248, 0.5) !important;
    color: #FFFFFF !important;
    box-shadow: 0 6px 20px rgba(0, 0, 0, 0.25) !important;
}
</style>
"""
st.markdown(hide_streamlit_style, unsafe_allow_html=True)


def init_log():
    """初始化日志配置"""
    from loguru import logger
    logger.remove()
    _lvl = "INFO"  # 改为 INFO 级别，过滤掉 DEBUG 日志

    def format_record(record):
        # 简化日志格式化处理，不尝试按特定字符串过滤torch相关内容
        file_path = record["file"].path
        relative_path = os.path.relpath(file_path, config.root_dir)
        record["file"].path = f"./{relative_path}"
        record['message'] = record['message'].replace(config.root_dir, ".")

        _format = '<green>{time:%Y-%m-%d %H:%M:%S}</> | ' + \
                  '<level>{level}</> | ' + \
                  '"{file.path}:{line}":<blue> {function}</> ' + \
                  '- <level>{message}</>' + "\n"
        return _format

    # 添加日志过滤器
    def log_filter(record):
        """过滤不必要的日志消息"""
        # 过滤掉启动时的噪音日志（即使在 DEBUG 模式下也可以选择过滤）
        ignore_patterns = [
            "Examining the path of torch.classes raised",
            "torch.cuda.is_available()",
            "CUDA initialization"
        ]
        return not any(pattern in record["message"] for pattern in ignore_patterns)

    logger.add(
        sys.stdout,
        level=_lvl,
        format=format_record,
        colorize=True,
        filter=log_filter
    )

    # 应用启动后，可以再添加更复杂的过滤器
    def setup_advanced_filters():
        """在应用完全启动后设置高级过滤器"""
        try:
            for handler_id in logger._core.handlers:
                logger.remove(handler_id)

            # 重新添加带有高级过滤的处理器
            def advanced_filter(record):
                """更复杂的过滤器，在应用启动后安全使用"""
                ignore_messages = [
                    "Examining the path of torch.classes raised",
                    "torch.cuda.is_available()",
                    "CUDA initialization"
                ]
                return not any(msg in record["message"] for msg in ignore_messages)

            logger.add(
                sys.stdout,
                level=_lvl,
                format=format_record,
                colorize=True,
                filter=advanced_filter
            )
        except Exception as e:
            # 如果过滤器设置失败，确保日志仍然可用
            logger.add(
                sys.stdout,
                level=_lvl,
                format=format_record,
                colorize=True
            )
            logger.error(f"设置高级日志过滤器失败: {e}")

    # 将高级过滤器设置放到启动主逻辑后
    import threading
    threading.Timer(5.0, setup_advanced_filters).start()


def init_global_state():
    """初始化全局状态"""
    if 'video_clip_json' not in st.session_state:
        st.session_state['video_clip_json'] = []
    if 'video_plot' not in st.session_state:
        st.session_state['video_plot'] = ''
    if 'ui_language' not in st.session_state:
        st.session_state['ui_language'] = config.ui.get("language", utils.get_system_locale())
    # 移除subclip_videos初始化 - 现在使用统一裁剪策略
    prefill_session_from_query_params()


def _resolve_prefill_path(path: str) -> str:
    if not path:
        return ""
    expanded_path = os.path.expanduser(path)
    if os.path.isabs(expanded_path):
        return expanded_path
    return os.path.abspath(os.path.join(config.root_dir, expanded_path))


def prefill_session_from_query_params():
    """允许通过 URL 参数预填脚本和视频，便于从前端复现任务。"""
    query_params = getattr(st, "query_params", {})
    prefill_map = {
        "script": "video_clip_json_path",
        "video": "video_origin_path",
    }

    for query_key, state_key in prefill_map.items():
        if st.session_state.get(state_key):
            continue
        value = query_params.get(query_key) if hasattr(query_params, "get") else None
        if isinstance(value, list):
            value = value[0] if value else ""
        resolved_path = _resolve_prefill_path(value)
        if resolved_path and os.path.exists(resolved_path):
            st.session_state[state_key] = resolved_path

    aspect = query_params.get("aspect") if hasattr(query_params, "get") else None
    if isinstance(aspect, list):
        aspect = aspect[0] if aspect else ""
    if aspect in {VideoAspect.landscape.value, VideoAspect.portrait.value}:
        st.session_state.setdefault("video_aspect", aspect)

    subtitle = query_params.get("subtitle") if hasattr(query_params, "get") else None
    if isinstance(subtitle, list):
        subtitle = subtitle[0] if subtitle else ""
    if subtitle is not None and "subtitle_enabled" not in st.session_state:
        st.session_state["subtitle_enabled"] = str(subtitle).lower() not in {"0", "false", "no", "off"}

    bgm_type = query_params.get("bgm") if hasattr(query_params, "get") else None
    if isinstance(bgm_type, list):
        bgm_type = bgm_type[0] if bgm_type else ""
    if bgm_type in {"none", "random", "custom"} and "bgm_type" not in st.session_state:
        st.session_state["bgm_type"] = "" if bgm_type == "none" else bgm_type

    for query_key, state_key in {
        "original_volume": "original_volume",
        "bgm_volume": "bgm_volume",
        "tts_volume": "tts_volume",
    }.items():
        value = query_params.get(query_key) if hasattr(query_params, "get") else None
        if isinstance(value, list):
            value = value[0] if value else ""
        if value is not None and state_key not in st.session_state:
            try:
                st.session_state[state_key] = float(value)
            except (TypeError, ValueError):
                pass


def tr(key):
    """翻译函数"""
    i18n_dir = os.path.join(os.path.dirname(__file__), "webui", "i18n")
    locales = utils.load_locales(i18n_dir)
    loc = locales.get(st.session_state['ui_language'], {})
    return loc.get("Translation", {}).get(key, key)


def render_generate_button():
    """渲染生成按钮和处理逻辑"""
    if st.button(tr("Generate Video"), use_container_width=True, type="primary"):
        from app.services import task as tm
        from app.services import state as sm
        from app.models import const
        import threading
        import time
        import uuid

        config.save_config()

        # 移除task_id检查 - 现在使用统一裁剪策略，不再需要预裁剪
        # 直接检查必要的文件是否存在
        if not st.session_state.get('video_clip_json_path'):
            st.error(tr("脚本文件不能为空"))
            return
        if not st.session_state.get('video_origin_path'):
            st.error(tr("视频文件不能为空"))
            return

        # 获取所有参数
        script_params = script_settings.get_script_params()
        video_params = video_settings.get_video_params()
        audio_params = audio_settings.get_audio_params()
        subtitle_params = subtitle_settings.get_subtitle_params()

        # 合并所有参数
        all_params = {
            **script_params,
            **video_params,
            **audio_params,
            **subtitle_params
        }

        # 创建参数对象
        params = VideoClipParams(**all_params)

        # 生成一个新的task_id用于本次处理
        task_id = str(uuid.uuid4())

        # 创建进度条
        progress_bar = st.progress(0)
        status_text = st.empty()

        def run_task():
            try:
                tm.start_subclip_unified(
                    task_id=task_id,
                    params=params
                )
            except Exception as e:
                logger.error(f"任务执行失败: {e}")
                sm.state.update_task(task_id, state=const.TASK_STATE_FAILED, message=str(e))

        # 在新线程中启动任务
        thread = threading.Thread(target=run_task)
        thread.start()

        # 轮询任务状态
        while True:
            task = sm.state.get_task(task_id)
            if task:
                progress = task.get("progress", 0)
                state = task.get("state")
                
                # 更新进度条
                progress_bar.progress(progress / 100)
                status_text.text(f"Processing... {progress}%")

                if state == const.TASK_STATE_COMPLETE:
                    status_text.text(tr("视频生成完成"))
                    progress_bar.progress(1.0)
                    
                    # 显示结果
                    video_files = task.get("videos", [])
                    try:
                        if video_files:
                            player_cols = st.columns(len(video_files) * 2 + 1)
                            for i, url in enumerate(video_files):
                                player_cols[i * 2 + 1].video(url)
                    except Exception as e:
                        logger.error(f"播放视频失败: {e}")
                    
                    st.success(tr("视频生成完成"))
                    break
                
                elif state == const.TASK_STATE_FAILED:
                    st.error(f"任务失败: {task.get('message', 'Unknown error')}")
                    break
            
            time.sleep(0.5)


def get_voice_name_for_tts_engine(tts_engine: str) -> str:
    """根据TTS引擎获取用户选择的音色"""
    if tts_engine == 'doubaotts':
        return st.session_state.get('voice_name', config.ui.get('doubaotts_voice_type', 'BV700_streaming'))
    elif tts_engine == 'azure_speech':
        return st.session_state.get('voice_name', config.ui.get('azure_voice_name', 'zh-CN-XiaoxiaoMultilingualNeural'))
    else:
        return st.session_state.get('voice_name', config.ui.get('edge_voice_name', 'zh-CN-XiaoxiaoNeural-Female'))


def get_jianying_export_params() -> VideoClipParams:
    """获取导出到剪映草稿的参数"""
    tts_engine = st.session_state.get('tts_engine', 'azure')
    voice_name = get_voice_name_for_tts_engine(tts_engine)
    voice_rate = st.session_state.get('voice_rate', 1.0)
    voice_pitch = st.session_state.get('voice_pitch', 1.0)
    
    return VideoClipParams(
        video_clip_json_path=st.session_state['video_clip_json_path'],
        video_origin_path=st.session_state['video_origin_path'],
        tts_engine=tts_engine,
        voice_name=voice_name,
        voice_rate=voice_rate,
        voice_pitch=voice_pitch,
        n_threads=config.app.get('n_threads', 4),
        video_aspect=VideoAspect.landscape,
        subtitle_enabled=st.session_state.get('subtitle_enabled', False),
        font_name=st.session_state.get('font_name', 'Microsoft YaHei'),
        font_size=st.session_state.get('font_size', 24),
        text_fore_color=st.session_state.get('text_fore_color', '#FFFFFF'),
        subtitle_position=st.session_state.get('subtitle_position', 'bottom'),
        custom_position=st.session_state.get('custom_position', 70.0),
        tts_volume=st.session_state.get('tts_volume', 1.0),
        original_volume=st.session_state.get('original_volume', 0.7),
        bgm_volume=st.session_state.get('bgm_volume', 0.3),
        draft_name=st.session_state.get('draft_name_input', f"NarratoAI_{int(time.time())}")
    )


def render_export_jianying_button():
    """渲染导出到剪映草稿按钮和处理逻辑"""
    import os
    import time
    import uuid
    from loguru import logger
    
    # 初始化session state
    if 'show_jianying_export_form' not in st.session_state:
        st.session_state['show_jianying_export_form'] = False
    if 'jianying_export_result' not in st.session_state:
        st.session_state['jianying_export_result'] = None
    if 'jianying_export_error' not in st.session_state:
        st.session_state['jianying_export_error'] = None
    
    if st.button("📤 导出到剪映草稿", use_container_width=True, type="secondary"):
        config.save_config()
        
        if not st.session_state.get('video_clip_json_path'):
            st.error("脚本文件不能为空")
            return
        if not st.session_state.get('video_origin_path'):
            st.error("视频文件不能为空")
            return
        
        jianying_draft_path = config.ui.get("jianying_draft_path", "")
        if not jianying_draft_path:
            st.error("请在基础设置中配置剪映草稿地址")
            return
        
        if not os.path.exists(jianying_draft_path):
            st.error(f"剪映草稿文件夹不存在: {jianying_draft_path}")
            return
        
        # 显示导出表单
        st.session_state['show_jianying_export_form'] = True
        st.session_state['jianying_export_result'] = None
        st.session_state['jianying_export_error'] = None
    
    # 显示导出表单
    if st.session_state['show_jianying_export_form']:
        st.markdown("---")
        st.subheader("导出到剪映草稿")
        
        draft_name = st.text_input(
            "请输入剪映草稿名称",
            value=f"NarratoAI_{int(time.time())}",
            key="draft_name_input"
        )
        
        if st.button("确认导出", key="confirm_export"):
            if not draft_name:
                st.error("请输入草稿名称")
                return
            
            # 创建任务ID
            task_id = str(uuid.uuid4())
            st.session_state['task_id'] = task_id
            
            # 构建参数
            try:
                params = get_jianying_export_params()
            except Exception as e:
                logger.error(f"构建参数失败: {e}")
                st.error(f"参数构建失败: {e}")
                return
            
            with st.spinner("正在导出到剪映草稿，请稍候..."):
                try:
                    from app.services import jianying_task
                    
                    # 调用导出到剪映草稿的任务
                    result = jianying_task.start_export_jianying_draft(task_id, params)
                    
                    # 记录日志
                    logger.info(f"成功导出到剪映草稿: {result['draft_name']}")
                    logger.info(f"草稿已保存到: {result['draft_path']}")
                    
                    # 保存结果到session state
                    st.session_state['jianying_export_result'] = result
                    st.session_state['jianying_export_error'] = None
                    st.session_state['show_jianying_export_form'] = False
                    
                    st.success(f"✅ 成功导出到剪映草稿: {result['draft_name']}")
                    st.info(f"📁 草稿已保存到: {result['draft_path']}")
                except Exception as e:
                    logger.error(f"导出到剪映草稿失败: {e}")
                    import traceback
                    logger.error(f"错误详情: {traceback.format_exc()}")
                    st.session_state['jianying_export_error'] = str(e)
                    st.session_state['jianying_export_result'] = None
                    st.error(f"❌ 导出到剪映草稿失败: {e}")
        
        if st.button("取消", key="cancel_export"):
            st.session_state['show_jianying_export_form'] = False
            st.session_state['jianying_export_result'] = None
            st.session_state['jianying_export_error'] = None
            st.rerun()



def main():
    """主函数"""
    init_log()
    init_global_state()

    # ===== 显式注册 LLM 提供商（最佳实践）=====
    # 在应用启动时立即注册，确保所有 LLM 功能可用
    if 'llm_providers_registered' not in st.session_state:
        try:
            from app.services.llm.providers import register_all_providers
            register_all_providers()
            st.session_state['llm_providers_registered'] = True
            logger.info("✅ LLM 提供商注册成功")
        except Exception as e:
            logger.error(f"❌ LLM 提供商注册失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            st.error(f"⚠️ LLM 初始化失败: {str(e)}\n\n请检查配置文件和依赖是否正确安装。")
            # 不抛出异常，允许应用继续运行（但 LLM 功能不可用）

    # 检测FFmpeg硬件加速，但只打印一次日志（使用 session_state 持久化）
    if 'hwaccel_logged' not in st.session_state:
        st.session_state['hwaccel_logged'] = False
    
    hwaccel_info = ffmpeg_utils.detect_hardware_acceleration()
    if not st.session_state['hwaccel_logged']:
        if hwaccel_info["available"]:
            logger.info(
                "FFmpeg硬件加速检测结果: "
                f"可用 | 类型: {hwaccel_info['type']} | 编码器: {hwaccel_info['encoder']} "
                f"| GPU类型: {hwaccel_info.get('gpu_kind') or ('独立GPU' if hwaccel_info.get('is_dedicated_gpu') else '集成/内建GPU')}"
            )
        else:
            logger.warning(f"FFmpeg硬件加速不可用: {hwaccel_info['message']}, 将使用CPU软件编码")
        st.session_state['hwaccel_logged'] = True

    # 仅初始化基本资源，避免过早地加载依赖PyTorch的资源
    # 检查是否能分解utils.init_resources()为基本资源和高级资源(如依赖PyTorch的资源)
    try:
        utils.init_resources()
    except Exception as e:
        logger.warning(f"资源初始化时出现警告: {e}")

    # Custom Premium Header Card
    header_html = """
    <div class="narrato-header-container">
        <div class="narrato-header-title">📽️ NarratoAI</div>
        <div class="narrato-header-subtitle">多模态智能解说与视频生成管线 • AI-powered video storytelling and montage automation pipeline</div>
    </div>
    """
    st.markdown(header_html, unsafe_allow_html=True)

    # 主内容
    left_col, right_col = st.columns([2, 1], gap="medium")

    with left_col:
        script_settings.render_script_panel(tr)
        audio_settings.render_audio_panel(tr)

    with right_col:
        video_settings.render_video_panel(tr)
        subtitle_settings.render_subtitle_panel(tr)
        system_settings.render_system_panel(tr)
        basic_settings.render_basic_settings(tr)

    # 底部操作按钮
    col_btn1, col_btn2 = st.columns([1, 1], gap="medium")
    with col_btn1:
        render_generate_button()
    with col_btn2:
        render_export_jianying_button()


if __name__ == "__main__":
    main()
