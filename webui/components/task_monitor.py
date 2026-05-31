import streamlit as st
import os
import time
from datetime import datetime
from pathlib import Path


class LogCaptureHandler:
    """Loguru handler to capture logs for Streamlit display"""

    def __init__(self):
        self.logs = []
        self.max_logs = 500

    def write(self, message):
        """Write a log message"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        level = message.record["level"].name
        formatted_msg = f"[{timestamp}] [{level}] {message}"

        self.logs.append(formatted_msg)
        if len(self.logs) > self.max_logs:
            self.logs = self.logs[-self.max_logs:]

    def flush(self):
        pass

    def get_logs(self):
        return "\n".join(self.logs)

    def clear(self):
        self.logs = []


# Global log capture instance
_log_capture = LogCaptureHandler()


def get_log_capture():
    """Get the global log capture instance"""
    return _log_capture


def render_task_monitor():
    """渲染任务监控面板"""
    st.subheader("📊 任务监控")

    # 初始化 session state
    if "task_logs" not in st.session_state:
        st.session_state["task_logs"] = []
    if "task_frames" not in st.session_state:
        st.session_state["task_frames"] = []
    if "task_progress" not in st.session_state:
        st.session_state["task_progress"] = 0

    # 标签页布局
    tab1, tab2, tab3 = st.tabs(["📟 日志", "🖼️ 关键帧", "📝 文案"])

    with tab1:
        render_log_panel()

    with tab2:
        render_frames_panel()

    with tab3:
        render_script_panel()

    # 底部操作按钮
    st.divider()
    col1, col2, col3, col4 = st.columns(4)
    with col1:
        if st.button("🗑️ 清空", use_container_width=True):
            clear_logs()
            st.rerun()
    with col2:
        if st.button("📥 导出日志", use_container_width=True):
            export_logs()
    with col3:
        if st.button("⏹️ 停止任务", use_container_width=True):
            stop_task()
    with col4:
        if st.button("🔄 刷新", use_container_width=True):
            st.rerun()


def render_log_panel():
    """渲染日志面板"""
    # 读取最新的日志
    logs = get_logs()

    # 日志显示区域
    st.text_area(
        "实时日志",
        value=logs,
        height=300,
        key="log_display",
        disabled=False
    )

    # 自动滚动到底部的提示
    st.caption("📍 日志实时更新，向上滚动查看历史")


def render_frames_panel():
    """渲染关键帧预览面板"""
    # 获取关键帧目录
    keyframes_dir = get_keyframes_dir()

    if not os.path.exists(keyframes_dir):
        st.info("暂无关键帧，正在等待任务生成...")
        return

    # 获取所有图片文件
    frame_files = sorted([
        f for f in os.listdir(keyframes_dir)
        if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
    ])

    if not frame_files:
        st.info("暂无关键帧，正在等待任务生成...")
        return

    # 显示帧数量
    st.caption(f"共 {len(frame_files)} 帧")

    # 计算进度
    total_expected = st.session_state.get("task_total_frames", len(frame_files) + 10)
    progress = min(len(frame_files) / total_expected, 1.0)
    st.progress(progress)

    # 网格布局显示缩略图 (4列)
    cols = st.columns(4)
    for i, frame_file in enumerate(frame_files):
        with cols[i % 4]:
            frame_path = os.path.join(keyframes_dir, frame_file)
            st.image(frame_path, caption=frame_file[:20], use_container_width=True)

    # 如果还有更多帧，显示提示
    if len(frame_files) > 12:
        st.info(f"还有 {len(frame_files) - 12} 帧未显示...")


def render_script_panel():
    """渲染文案预览面板"""
    script_content = st.session_state.get("task_script", "")

    if not script_content:
        st.info("暂无生成的文案，正在等待任务...")
        return

    st.text_area(
        "生成的文案",
        value=script_content,
        height=300,
        disabled=True
    )


def get_logs():
    """获取当前日志"""
    capture = get_log_capture()
    return capture.get_logs()


def clear_logs():
    """清空日志"""
    capture = get_log_capture()
    capture.clear()
    st.session_state["task_logs"] = []
    st.session_state["task_frames"] = []
    st.session_state["task_progress"] = 0
    st.session_state["task_script"] = ""


def export_logs():
    """导出日志"""
    logs = get_logs()
    if logs:
        st.download_button(
            label="📥 下载日志文件",
            data=logs,
            file_name=f"task_log_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt",
            mime="text/plain"
        )
    else:
        st.warning("暂无日志可导出")


def stop_task():
    """停止当前任务"""
    st.warning("停止任务功能开发中...")
    # TODO: 实现任务停止逻辑


def get_keyframes_dir():
    """获取关键帧目录"""
    from app.utils.utils import storage_dir
    return storage_dir("temp/keyframes")


def get_analysis_dir():
    """获取分析结果目录"""
    from app.utils.utils import storage_dir
    return storage_dir("temp/analysis")


def append_log(message, level="INFO"):
    """追加日志（供外部调用）"""
    timestamp = datetime.now().strftime("%H:%M:%S")
    formatted = f"[{timestamp}] [{level}] {message}"

    capture = get_log_capture()
    capture.write(type("Message", (), {"record": {"level": {"name": level}}, "__str__": lambda self: message})())

    if "task_logs" not in st.session_state:
        st.session_state["task_logs"] = []
    st.session_state["task_logs"].append(formatted)


def update_frames():
    """更新关键帧列表"""
    keyframes_dir = get_keyframes_dir()
    if os.path.exists(keyframes_dir):
        frame_files = sorted([
            f for f in os.listdir(keyframes_dir)
            if f.lower().endswith(('.png', '.jpg', '.jpeg', '.webp'))
        ])
        st.session_state["task_frames"] = frame_files


def update_script(script_content):
    """更新文案内容"""
    st.session_state["task_script"] = script_content


def set_progress(progress):
    """设置任务进度"""
    st.session_state["task_progress"] = progress
