import os
import socket
import toml
import shutil
from copy import deepcopy
from loguru import logger

from app.config.defaults import build_default_app_config, merge_missing_app_defaults

root_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))
config_file = f"{root_dir}/config.toml"
version_file = f"{root_dir}/project_version"

SECRET_ENV_OVERRIDES = {
    ("app", "vision_gemini_api_key"): "NARRATOAI_VISION_GEMINI_API_KEY",
    ("app", "vision_openai_api_key"): "NARRATOAI_VISION_OPENAI_API_KEY",
    ("app", "vision_kaggle_api_key"): "NARRATOAI_VISION_KAGGLE_API_KEY",
    ("app", "text_openai_api_key"): "NARRATOAI_TEXT_OPENAI_API_KEY",
    ("alist", "url"): "NARRATOAI_ALIST_URL",
    ("alist", "username"): "NARRATOAI_ALIST_USERNAME",
    ("alist", "password"): "NARRATOAI_ALIST_PASSWORD",
    ("alist", "base_path"): "NARRATOAI_ALIST_BASE_PATH",
    ("azure", "speech_key"): "NARRATOAI_AZURE_SPEECH_KEY",
    ("azure", "speech_region"): "NARRATOAI_AZURE_SPEECH_REGION",
    ("tencent", "secret_id"): "NARRATOAI_TENCENT_SECRET_ID",
    ("tencent", "secret_key"): "NARRATOAI_TENCENT_SECRET_KEY",
    ("soulvoice", "api_key"): "NARRATOAI_SOULVOICE_API_KEY",
    ("tts_qwen", "api_key"): "NARRATOAI_TTS_QWEN_API_KEY",
    ("fun_asr", "api_key"): "NARRATOAI_FUN_ASR_API_KEY",
    ("doubaotts", "ak"): "NARRATOAI_DOUBAOTTS_AK",
    ("doubaotts", "sk"): "NARRATOAI_DOUBAOTTS_SK",
    ("doubaotts", "appid"): "NARRATOAI_DOUBAOTTS_APPID",
    ("doubaotts", "token"): "NARRATOAI_DOUBAOTTS_TOKEN",
    ("xiaomi", "api_key"): "NARRATOAI_XIAOMI_API_KEY",
    ("minimax", "api_key"): "NARRATOAI_MINIMAX_API_KEY",
}


def get_version_from_file():
    """从project_version文件中读取版本号"""
    try:
        if os.path.isfile(version_file):
            with open(version_file, "r", encoding="utf-8") as f:
                return f.read().strip()
        return "0.1.0"  # 默认版本号
    except Exception as e:
        logger.error(f"读取版本号文件失败: {str(e)}")
        return "0.1.0"  # 默认版本号


def load_config():
    # fix: IsADirectoryError: [Errno 21] Is a directory: '/NarratoAI/config.toml'
    if os.path.isdir(config_file):
        shutil.rmtree(config_file)

    if not os.path.isfile(config_file):
        _config_ = build_default_config()
        write_config_file(_config_)
        logger.info("create config.toml with shared defaults")
        return _config_

    logger.info(f"load config from file: {config_file}")

    _config_ = load_toml_file(config_file)
    _config_["app"] = merge_missing_app_defaults(_config_.get("app", {}))
    return _config_


def apply_secret_env_overrides(config_data):
    """Overlay secrets from environment variables without persisting them."""
    applied = set()
    for (section, key), env_name in SECRET_ENV_OVERRIDES.items():
        env_value = os.getenv(env_name)
        if env_value is None:
            continue
        config_data.setdefault(section, {})[key] = env_value
        applied.add((section, key))

    if applied:
        logger.info(f"loaded {len(applied)} secret config value(s) from environment")
    return applied


def build_persistable_config(config_data):
    """Return config data suitable for config.toml, excluding env-only secrets."""
    persistable = deepcopy(config_data)
    for section, key in _env_secret_override_paths:
        env_name = SECRET_ENV_OVERRIDES[(section, key)]
        env_value = os.getenv(env_name)
        if env_value is None:
            continue
        section_data = persistable.get(section)
        if isinstance(section_data, dict) and section_data.get(key) == env_value:
            section_data[key] = _cfg_from_file.get(section, {}).get(key, "")
    return persistable


def load_toml_file(file_path):
    """Load a TOML file and fall back to utf-8-sig when needed."""
    try:
        return toml.load(file_path)
    except Exception as e:
        logger.warning(f"load config failed: {str(e)}, try to load as utf-8-sig")
        with open(file_path, mode="r", encoding="utf-8-sig") as fp:
            _cfg_content = fp.read()
            return toml.loads(_cfg_content)


def build_default_config():
    """Build the initial config file content for a fresh installation."""
    example_file = f"{root_dir}/config.example.toml"
    config_data = {}
    if os.path.isfile(example_file):
        config_data = load_toml_file(example_file)

    config_data["app"] = build_default_app_config(config_data.get("app", {}))
    return config_data


def write_config_file(config_data):
    parent_dir = os.path.dirname(config_file)
    if parent_dir:
        os.makedirs(parent_dir, exist_ok=True)

    with open(config_file, "w", encoding="utf-8") as f:
        f.write(toml.dumps(config_data))


def save_config():
    with open(config_file, "w", encoding="utf-8") as f:
        _cfg["app"] = app
        _cfg["proxy"] = proxy
        _cfg["azure"] = azure
        _cfg["tencent"] = tencent
        _cfg["soulvoice"] = soulvoice
        _cfg["ui"] = ui
        _cfg["tts_qwen"] = tts_qwen
        _cfg["fun_asr"] = fun_asr
        _cfg["indextts2"] = indextts2
        _cfg["doubaotts"] = doubaotts
        _cfg["xiaomi"] = xiaomi
        _cfg["minimax"] = minimax
        _cfg["alist"] = alist
        f.write(toml.dumps(build_persistable_config(_cfg)))


_cfg = load_config()
_cfg_from_file = deepcopy(_cfg)
_env_secret_override_paths = apply_secret_env_overrides(_cfg)
app = _cfg.get("app", {})
whisper = _cfg.get("whisper", {})
proxy = _cfg.get("proxy", {})
azure = _cfg.get("azure", {})
tencent = _cfg.get("tencent", {})
soulvoice = _cfg.get("soulvoice", {})
ui = _cfg.get("ui", {})
frames = _cfg.get("frames", {})
tts_qwen = _cfg.get("tts_qwen", {})
fun_asr = _cfg.get("fun_asr", {})
indextts2 = _cfg.get("indextts2", {})
doubaotts = _cfg.get("doubaotts", {})
xiaomi = _cfg.get("xiaomi", {})
minimax = _cfg.get("minimax", {})
alist = _cfg.get("alist", {})

hostname = socket.gethostname()

log_level = _cfg.get("log_level", "DEBUG")
listen_host = _cfg.get("listen_host", "0.0.0.0")
listen_port = _cfg.get("listen_port", 8080)
project_name = _cfg.get("project_name", "NarratoAI")
project_description = _cfg.get(
    "project_description",
    "<a href='https://github.com/linyqh/NarratoAI'>https://github.com/linyqh/NarratoAI</a>",
)
# 从文件读取版本号，而不是从配置文件中获取
project_version = get_version_from_file()
reload_debug = False

imagemagick_path = app.get("imagemagick_path", "")
if imagemagick_path and os.path.isfile(imagemagick_path):
    os.environ["IMAGEMAGICK_BINARY"] = imagemagick_path

ffmpeg_path = app.get("ffmpeg_path", "")
if ffmpeg_path and os.path.isfile(ffmpeg_path):
    os.environ["IMAGEIO_FFMPEG_EXE"] = ffmpeg_path

logger.info(f"{project_name} v{project_version}")
