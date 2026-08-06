"""统一配置加载:优先真实环境变量,回退仓库根目录的 .env.local。

板块代码一律用 ``env.get(name)`` / ``env.require(name)``,不要裸 ``os.getenv``。
CI 里无 .env.local,凭据来自 GitHub Secrets 注入的真实环境变量。
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, Optional

# src/common/env.py -> parents[2] = 仓库根目录
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_ENV_PATH = _REPO_ROOT / ".env.local"

_loaded: Optional[Dict[str, str]] = None


def _strip_wrapping_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def parse_env_file(path: Path | str) -> Dict[str, str]:
    """解析 .env 格式文件为 dict。支持 ``export`` 前缀和引号包裹。"""
    path = Path(path)
    values: Dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export "):].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not key:
            continue
        values[key] = _strip_wrapping_quotes(value.strip())
    return values


def load_local_env(path: str | Path | None = None) -> Dict[str, str]:
    """加载 .env.local(带缓存)。"""
    global _loaded
    if path is not None:
        return parse_env_file(path)
    if _loaded is None:
        _loaded = parse_env_file(_DEFAULT_ENV_PATH)
    return _loaded


def get_env_value(name: str, local_env: Optional[Dict[str, str]] = None, default: str = "") -> str:
    value = os.getenv(name)
    if value is not None:
        return value.strip()
    if local_env is None:
        local_env = load_local_env()
    return str(local_env.get(name, default)).strip()


def get(name: str, default: str = "") -> str:
    """统一读取环境变量:优先真实 env,回退 .env.local。"""
    return get_env_value(name, default=default)


def require(name: str) -> str:
    """读取必需环境变量,缺失抛 RuntimeError。"""
    value = get(name)
    if not value:
        raise RuntimeError(f"缺少必需环境变量: {name}")
    return value
