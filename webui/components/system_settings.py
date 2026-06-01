import streamlit as st
import os
import shutil
from loguru import logger

from app.utils.utils import storage_dir


def clear_directory(dir_path, tr):
    """清理指定目录"""
    if os.path.exists(dir_path):
        try:
            for item in os.listdir(dir_path):
                item_path = os.path.join(dir_path, item)
                try:
                    if os.path.isfile(item_path):
                        os.unlink(item_path)
                    elif os.path.isdir(item_path):
                        shutil.rmtree(item_path)
                except Exception as e:
                    logger.error(f"Failed to delete {item_path}: {e}")
            st.success(tr("Directory cleared"))
            logger.info(f"Cleared directory: {dir_path}")
        except Exception as e:
            st.error(f"{tr('Failed to clear directory')}: {str(e)}")
            logger.error(f"Failed to clear directory {dir_path}: {e}")
    else:
        st.warning(tr("Directory does not exist"))

def render_system_panel(tr):
    """渲染系统设置面板"""
    with st.expander(tr("System settings"), expanded=False):
        targets = {
            tr("Clear frames"): os.path.join(storage_dir(), "temp/keyframes"),
            tr("Clear clip videos"): os.path.join(storage_dir(), "temp/clip_video"),
            tr("Clear tasks"): os.path.join(storage_dir(), "tasks"),
        }
        st.caption("清理本地缓存目录，不会删除 resource/videos 或 resource/scripts。")
        confirm_clear = st.checkbox("确认要清理缓存目录", key="confirm_clear_system_cache")

        col1, col2, col3 = st.columns(3)
                
        with col1:
            if st.button(tr("Clear frames"), use_container_width=True, disabled=not confirm_clear):
                clear_directory(targets[tr("Clear frames")], tr)
                
        with col2:
            if st.button(tr("Clear clip videos"), use_container_width=True, disabled=not confirm_clear):
                clear_directory(targets[tr("Clear clip videos")], tr)
                
        with col3:
            if st.button(tr("Clear tasks"), use_container_width=True, disabled=not confirm_clear):
                clear_directory(targets[tr("Clear tasks")], tr)

        with st.expander("清理目标路径", expanded=False):
            for label, target_path in targets.items():
                st.code(f"{label}: {target_path}", language="text")
