"""
Alist API 客户端

支持 Alist v3 的文件上传、下载、目录创建等操作。
作为 Kaggle 与本地 NarratoAI 之间的文件传输桥梁。

配置项（放在 config.toml 的 [alist] 节）：
    url         - Alist 地址，如 http://143.198.86.34:5244
    username    - 登录用户名
    password    - 登录密码
    base_path   - 存储基础路径，如 /kaggle
"""

import os
import requests
from loguru import logger
from typing import Optional


class AlistClient:
    """
    Alist v3 API 客户端。

    使用流程：
        client = AlistClient(url, username, password, base_path)
        client.login()
        client.upload_file(local_path, remote_path)
        client.download_file(remote_path, local_path)
    """

    def __init__(
        self,
        url: str,
        username: str,
        password: str,
        base_path: str = "/",
    ):
        self.url = url.rstrip("/")
        self.username = username
        self.password = password
        self.base_path = base_path.rstrip("/")
        self.token: str | None = None
        self._session = requests.Session()

    def _api(self, path: str, **kwargs) -> requests.Response:
        """发起带 auth header 的请求"""
        url = f"{self.url}{path}"
        headers = kwargs.pop("headers", {})
        if self.token:
            headers["Authorization"] = self.token
        return self._session.request(url=url, headers=headers, **kwargs)

    def login(self) -> bool:
        """登录 Alist，获取 token"""
        try:
            resp = self._session.post(
                f"{self.url}/api/v3/login",
                json={"username": self.username, "password": self.password},
                timeout=15,
            )
            resp.raise_for_status()
            data = resp.json()
            if data.get("code") == 200:
                self.token = data["data"]["token"]
                logger.info(f"Alist 登录成功: {self.username}@{self.url}")
                return True
            else:
                logger.error(f"Alist 登录失败: {data.get('message', 'unknown')}")
                return False
        except Exception as e:
            logger.error(f"Alist 登录异常: {e}")
            return False

    def ensure_dir(self, remote_path: str) -> bool:
        """确保远程目录存在，不存在则创建"""
        full_path = f"{self.base_path}{remote_path}".rstrip("/")
        try:
            resp = self._api(
                "/api/v3/mkdir",
                method="POST",
                json={"path": full_path, "password": self.password},
                timeout=15,
            )
            data = resp.json()
            if data.get("code") in (200, 201):
                return True
            if "already exists" in str(data).lower():
                return True
            logger.warning(f"mkdir {full_path}: {data.get('message', data)}")
            return False
        except Exception as e:
            logger.error(f"mkdir {full_path} 异常: {e}")
            return False

    def upload_file(
        self,
        local_path: str,
        remote_path: str,
        overwrite: bool = True,
    ) -> bool:
        """
        上传本地文件到 Alist 远程路径。

        Args:
            local_path:  本地文件路径
            remote_path: Alist 内的目标路径（相对路径，会拼上 base_path）
            overwrite:  是否覆盖已有文件
        """
        if not os.path.exists(local_path):
            logger.error(f"本地文件不存在: {local_path}")
            return False

        full_remote = f"{self.base_path}{remote_path}".rstrip("/")
        dir_part = os.path.dirname(full_remote)
        if dir_part:
            self.ensure_dir(f"/{dir_part}")

        try:
            with open(local_path, "rb") as f:
                files = {"file": (os.path.basename(local_path), f)}
                data = {"path": dir_part or "/", "password": self.password}
                resp = self._api(
                    "/api/v3/upload",
                    method="POST",
                    files=files,
                    data=data,
                    timeout=120,
                )
            resp_data = resp.json()
            if resp_data.get("code") in (200, 201):
                logger.info(f"上传成功: {local_path} → {full_remote}")
                return True
            logger.error(f"上传失败: {resp_data.get('message', resp_data)}")
            return False
        except Exception as e:
            logger.error(f"上传异常: {e}")
            return False

    def get_download_url(self, remote_path: str) -> str | None:
        """
        获取文件的下载直链（无需认证即可访问）。

        Args:
            remote_path: Alist 内的文件路径（相对路径，会拼上 base_path）
        Returns:
            可直接下载的 URL，或 None（失败时）
        """
        full_path = f"{self.base_path}{remote_path}".rstrip("/")
        try:
            resp = self._api(
                "/api/v3/storage/get",
                params={"path": full_path, "password": self.password},
                timeout=15,
            )
            data = resp.json()
            if data.get("code") == 200:
                raw_url = data["data"].get("raw_url") or data["data"].get("url")
                if raw_url:
                    return raw_url
            logger.error(f"获取下载链接失败: {data.get('message', data)}")
            return None
        except Exception as e:
            logger.error(f"获取下载链接异常: {e}")
            return None

    def download_file(self, remote_path: str, local_path: str) -> bool:
        """
        从 Alist 下载文件到本地。

        Args:
            remote_path: Alist 内的文件路径
            local_path:  本地保存路径
        """
        raw_url = self.get_download_url(remote_path)
        if not raw_url:
            return False

        try:
            resp = self._session.get(raw_url, timeout=120, stream=True)
            resp.raise_for_status()
            os.makedirs(os.path.dirname(local_path) or ".", exist_ok=True)
            with open(local_path, "wb") as f:
                for chunk in resp.iter_content(8192):
                    f.write(chunk)
            logger.info(f"下载成功: {remote_path} → {local_path}")
            return True
        except Exception as e:
            logger.error(f"下载异常: {e}")
            return False

    def exists(self, remote_path: str) -> bool:
        """检查远程文件是否存在"""
        raw_url = self.get_download_url(remote_path)
        return raw_url is not None


def get_alist_client(config: dict | None = None) -> Optional[AlistClient]:
    """
    从 config 或环境变量创建 AlistClient。

    配置路径（config.toml [alist] 节）：
        url       - Alist 服务地址
        username  - 用户名
        password  - 密码
        base_path - 基础存储路径，默认 /

    也可通过环境变量覆盖：
        ALIST_URL, ALIST_USERNAME, ALIST_PASSWORD, ALIST_BASE_PATH
    """
    if config is None:
        from app.config import config as global_config
        # 优先读取 [alist] 配置节，若没有则回退到 global_config._cfg 中的 alist 或者全局的 app 字段
        config = getattr(global_config, "alist", {}) or global_config._cfg.get("alist", {})

    url = os.environ.get("ALIST_URL") or os.environ.get("NARRATOAI_ALIST_URL") or config.get("url") or config.get("alist_url") or ""
    username = os.environ.get("ALIST_USERNAME") or os.environ.get("NARRATOAI_ALIST_USERNAME") or config.get("username") or config.get("alist_username") or ""
    password = os.environ.get("ALIST_PASSWORD") or os.environ.get("NARRATOAI_ALIST_PASSWORD") or config.get("password") or config.get("alist_password") or ""
    base_path = os.environ.get("ALIST_BASE_PATH") or os.environ.get("NARRATOAI_ALIST_BASE_PATH") or config.get("base_path") or config.get("alist_base_path") or "/"

    if not url or not username or not password:
        logger.debug(f"Alist 配置不完整 (url={url}, username={username}, has_password={bool(password)}), 跳过初始化")
        return None

    client = AlistClient(url=url, username=username, password=password, base_path=base_path)
    if not client.login():
        return None
    return client
