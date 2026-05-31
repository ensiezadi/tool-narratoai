import streamlit as st
import requests
from requests.auth import HTTPBasicAuth
import xml.etree.ElementTree as ET
import os


class AlistClient:
    """Alist WebDAV 客户端"""

    def __init__(self, base_url, username, password):
        self.base_url = base_url.rstrip('/')
        self.auth = HTTPBasicAuth(username, password)
        self.session = requests.Session()
        self.session.auth = self.auth

    def _build_url(self, path: str) -> str:
        """构建完整的 WebDAV URL"""
        if not path.startswith('/'):
            path = '/' + path
        return f"{self.base_url}{path}"

    def list_directory(self, path="/"):
        """
        列出目录内容。

        Args:
            path: Alist 内的虚拟路径，如 "/"、"/folder"、"/folder/sub"
                   注意：不需要包含 /dav 前缀，base_url 已经包含
        """
        url = self._build_url(path)

        headers = {
            'Depth': '1',
            'Content-Type': 'application/xml'
        }

        body = '<?xml version="1.0" encoding="utf-8"?><d:propfind xmlns:d="DAV:"><d:prop><d:resourcetype/><d:displayname/><d:getcontentlength/><d:getcontenttype/><d:getlastmodified/></d:prop></d:propfind>'

        try:
            response = self.session.request(
                'PROPFIND',
                url,
                headers=headers,
                data=body,
                timeout=10
            )

            if response.status_code == 207:
                return self._parse_dav_response(response.text)
            else:
                st.error(f"列出目录失败: HTTP {response.status_code} - {response.text[:200]}")
                return []
        except Exception as e:
            st.error(f"连接 Alist 失败: {str(e)}")
            return []

    def _parse_dav_response(self, xml_content):
        """解析 WebDAV XML 响应"""
        items = []

        try:
            root = ET.fromstring(xml_content)
            namespaces = {
                'd': 'DAV:',
                'oc': 'http://owncloud.org/ns'
            }

            # 从 base_url 提取 /dav 前缀，用于剥离 href 中的重复部分
            # base_url 如 "https://alist.ensiezadi.lol/dav"，提取 "/dav"
            dav_prefix = '/' + self.base_url.split('/')[-1] if '/' in self.base_url else ''

            for response in root.findall('.//d:response', namespaces):
                href = response.find('d:href', namespaces)
                resource_type = response.find('.//d:resourcetype/d:collection', namespaces)
                content_length = response.find('.//d:getcontentlength', namespaces)
                content_type = response.find('.//d:getcontenttype', namespaces)
                display_name = response.find('d:displayname', namespaces)
                last_modified = response.find('.//d:getlastmodified', namespaces)

                if href is not None:
                    raw_href = href.text or ''

                    # 跳过根自身（raw_href 为 /dav/ 或 /dav）
                    if dav_prefix and raw_href.rstrip('/') == dav_prefix.rstrip('/'):
                        continue
                    if raw_href.rstrip('/') == self.base_url.rstrip('/'):
                        continue

                    # raw_href 格式如: /dav/folder/  或  /dav/folder/file.mp4
                    # 先剥离 /dav 前缀，得到虚拟路径 /folder/ 或 /folder/file.mp4
                    path = raw_href
                    if dav_prefix and path.rstrip('/').startswith(dav_prefix):
                        path = path[len(dav_prefix):]
                    elif path.startswith(self.base_url):
                        path = path[len(self.base_url):]
                    if not path.startswith('/'):
                        path = '/' + path

                    is_dir = resource_type is not None

                    name = display_name.text if display_name is not None else os.path.basename(path.rstrip('/'))
                    if name in ['.', '..', '']:
                        continue

                    items.append({
                        'name': name,
                        'path': path,
                        'is_dir': is_dir,
                        'size': int(content_length.text) if content_length is not None and content_length.text else 0,
                        'type': content_type.text if content_type is not None else ('directory' if is_dir else 'file'),
                        'modified': last_modified.text if last_modified is not None else None
                    })
        except Exception as e:
            st.error(f"解析响应失败: {str(e)}")

        return sorted(items, key=lambda x: (not x['is_dir'], x['name'].lower()))

    def download_file(self, path):
        """
        下载文件内容。

        Args:
            path: Alist 内的虚拟路径，如 "/folder/video.mp4"
                  （不需要 /dav 前缀，_build_url 会处理）
        """
        url = self._build_url(path)

        try:
            response = self.session.get(url, timeout=60)
            if response.status_code == 200:
                return response.content
            else:
                st.error(f"下载失败: HTTP {response.status_code}")
                return None
        except Exception as e:
            st.error(f"下载失败: {str(e)}")
            return None


def get_alist_config():
    """获取 Alist 配置"""
    from app.config.config import alist as alist_cfg
    base_url = st.session_state.get('alist_url', '')
    username = st.session_state.get('alist_username', '')
    password = st.session_state.get('alist_password', '')

    if not base_url:
        base_url = st.text_input(
            "Alist 地址",
            value=alist_cfg.get('url', '') + '/dav/',
            help="Alist WebDAV 地址，格式：http://ip:port/dav/"
        )
        st.session_state['alist_url'] = base_url

    if not username:
        username = st.text_input("用户名", value=alist_cfg.get('username', 'ensiezadi'))
        st.session_state['alist_username'] = username

    if not password:
        password = st.text_input("密码", value=alist_cfg.get('password', ''), type="password")
        st.session_state['alist_password'] = password

    return base_url, username, password


def render_alist_file_browser(key="alist_browser"):
    """渲染 Alist 文件浏览器"""
    st.subheader("📁 Alist 文件浏览器")

    from app.config.config import alist as alist_cfg
    base_url = st.session_state.get('alist_url', alist_cfg.get('url', '') + '/dav/')
    username = st.session_state.get('alist_username', alist_cfg.get('username', 'ensiezadi'))
    password = st.session_state.get('alist_password', alist_cfg.get('password', ''))

    with st.expander("⚙️ Alist 连接设置", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            base_url = st.text_input(
                "Alist 地址",
                value=base_url,
                help="Alist WebDAV 地址"
            )
        with col2:
            username = st.text_input("用户名", value=username)

        password = st.text_input(
            "密码",
            value=password,
            type="password",
            help="Alist 密码"
        )

        if st.button("保存配置"):
            st.session_state['alist_url'] = base_url
            st.session_state['alist_username'] = username
            st.session_state['alist_password'] = password
            st.success("配置已保存")
            st.rerun()

    if not password:
        st.warning("请先输入密码连接 Alist")
        return None, None

    client = AlistClient(base_url, username, password)

    # current_path 存储虚拟路径（不含 /dav 前缀），如 "/"、"/folder"
    # 优先使用 config.toml 中配置的 base_path 作为默认路径
    default_path = alist_cfg.get('base_path', '/')
    # 剥离 /dav 前缀，因为 base_url 已经包含
    if default_path.startswith('/dav'):
        default_path = default_path[4:] or '/'
    if not default_path.startswith('/'):
        default_path = '/' + default_path
    current_path = st.session_state.get('alist_current_path', default_path)

    st.markdown(f"**当前路径:** `{current_path if current_path != '/' else '/'}`")

    col_back, col_refresh = st.columns(2)
    with col_back:
        if current_path != '/':
            parent = os.path.dirname(current_path.rstrip('/'))
            if not parent:
                parent = '/'
            if st.button("⬆️ 返回上级目录", key="alist_parent"):
                st.session_state['alist_current_path'] = parent
                st.rerun()
    with col_refresh:
        if st.button("🔄 刷新", key="alist_refresh"):
            st.rerun()

    with st.spinner("正在加载文件列表..."):
        files = client.list_directory(current_path)

    if not files:
        st.info("目录为空或无法访问")
        return None, None

    cols = st.columns([3, 1, 1, 1])
    cols[0].markdown("**文件名**")
    cols[1].markdown("**大小**")
    cols[2].markdown("**类型**")
    cols[3].markdown("**操作**")

    selected_file = None
    selected_path = None

    for file in files:
        col0, col1, col2, col3 = st.columns([3, 1, 1, 1])

        icon = "📁" if file['is_dir'] else "📄"
        size_str = format_size(file['size']) if not file['is_dir'] else "-"

        with col0:
            st.markdown(f"{icon} `{file['name']}`")

        with col1:
            st.text(size_str)

        with col2:
            st.text("文件夹" if file['is_dir'] else "文件")

        with col3:
            if file['is_dir']:
                if st.button("打开", key=f"open_{file['path']}"):
                    st.session_state['alist_current_path'] = file['path']
                    st.rerun()
            else:
                ext = os.path.splitext(file['name'])[1].lower()
                is_video = ext in ['.mp4', '.avi', '.mov', '.mkv', '.flv', '.wmv', '.webm']

                if is_video:
                    if st.button("选择视频", key=f"select_{file['path']}"):
                        selected_path = file['path']
                        selected_file = file['name']

    return selected_file, selected_path


def format_size(size_bytes):
    """格式化文件大小"""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def download_from_alist(path, client):
    """从 Alist 下载文件"""
    return client.download_file(path)