"""集思录账密登录(统一认证层)。

替代旧仓库的 ``JISILU_COOKIE`` / 硬编码 cookie 方案。登录用 AES-ECB 加密账密,
返回 cookie 字符串。板块代码用 ``make_session()`` 一行拿到已登录的 requests.Session。

依赖:pycryptodome(提供 ``Crypto.Cipher.AES``)。
"""
from __future__ import annotations

import binascii
import logging
import time
from typing import Optional

import requests

from . import alerts, env

try:
    from Crypto.Cipher import AES
    from Crypto.Util.Padding import pad
except ImportError:  # pragma: no cover
    AES = None
    pad = None


AES_KEY = "397151C04723421F"
LOGIN_URL = "https://www.jisilu.cn/webapi/account/login_process/"
ETF_LIST_URL = "https://www.jisilu.cn/data/etf/etf_list/"
GOLD_LIST_URL = "https://www.jisilu.cn/data/etf/gold_list/"
QDII_LIST_E_URL = "https://www.jisilu.cn/data/qdii/qdii_list/E"
QDII_LIST_L_URL = "https://www.jisilu.cn/data/qdii/qdii_list/L"

LOGIN_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
    "Origin": "https://www.jisilu.cn",
    "Referer": "https://www.jisilu.cn/account/login/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}
ETF_LIST_HEADERS = {
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Referer": "https://www.jisilu.cn/data/etf/",
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/142.0.0.0 Safari/537.36",
    "X-Requested-With": "XMLHttpRequest",
}

logger = logging.getLogger(__name__)


def jslencode(text: str) -> str:
    """集思录登录接口要求的 AES-ECB(hex)加密。"""
    if AES is None or pad is None:
        raise RuntimeError("缺少 pycryptodome 依赖,请先执行: pip install pycryptodome")
    key = AES_KEY.encode("utf-8")
    cipher = AES.new(key, AES.MODE_ECB)
    encrypted_bytes = cipher.encrypt(pad(text.encode("utf-8"), AES.block_size))
    return binascii.hexlify(encrypted_bytes).decode("utf-8")


def build_cookie_string(cookies: requests.cookies.RequestsCookieJar) -> str:
    return "; ".join(f"{key}={value}" for key, value in cookies.items())


def apply_cookie_string(session: requests.Session, cookie_str: str) -> None:
    """把 ``key=value; ...`` 形式的 cookie 字符串塞进 session。"""
    for cookie_part in cookie_str.split(";"):
        piece = cookie_part.strip()
        if not piece or "=" not in piece:
            continue
        name, value = piece.split("=", 1)
        session.cookies.set(name.strip(), value.strip())


def _build_etf_list_params() -> dict[str, str]:
    timestamp_ms = str(int(time.time() * 1000))
    return {"___jsl": f"LST___t={timestamp_ms}", "rp": "25"}


def login_jisilu(
    username: Optional[str] = None,
    password: Optional[str] = None,
    session: Optional[requests.Session] = None,
) -> str:
    """登录集思录,返回 ``key=value; ...`` 格式 cookie 字符串;失败返回空串。

    凭据默认从环境变量 ``JISILU_USERNAME`` / ``JISILU_PASSWORD`` 读取。
    """
    username = (username or env.get("JISILU_USERNAME")).strip()
    password = (password or env.get("JISILU_PASSWORD")).strip()
    if not username or not password:
        logger.error("集思录用户名或密码为空,请配置 JISILU_USERNAME/JISILU_PASSWORD")
        return ""

    data = {
        "return_url": "https://www.jisilu.cn/",
        "user_name": jslencode(username),
        "password": jslencode(password),
        "auto_login": "1",
        "aes": "1",
    }
    request_session = session or requests.Session()
    try:
        def _login() -> str:
            response = request_session.post(LOGIN_URL, headers=LOGIN_HEADERS, data=data, timeout=10)
            response.raise_for_status()
            result = response.json()
            logger.info("登录响应: %s", result)
            if result.get("code") != 200:
                logger.error("登录失败(账密): %s", result.get("msg", "未知错误"))
                return ""
            cookie_str = build_cookie_string(response.cookies) or build_cookie_string(request_session.cookies)
            if not cookie_str:
                logger.error("登录成功但未获取到 Cookie")
                return ""
            logger.info("集思录登录成功")
            return cookie_str

        # 网络层(超时/连接/HTTP5xx)退避重试;账密错(code!=200)返回空串不重试
        return alerts.run_with_retry("集思录登录", _login)
    except Exception as exc:  # pragma: no cover  # 重试耗尽仍失败
        logger.exception("集思录登录异常(可能网络故障): %s", exc)
        return ""
    finally:
        if session is None:
            request_session.close()


def make_session(
    username: Optional[str] = None,
    password: Optional[str] = None,
) -> requests.Session:
    """登录集思录并返回已塞 cookie 的 Session。登录失败抛 RuntimeError。"""
    cookie = login_jisilu(username, password)
    if not cookie:
        raise RuntimeError("集思录登录失败,请检查网络或 JISILU_USERNAME/JISILU_PASSWORD(网络异常详见日志)")
    session = requests.Session()
    apply_cookie_string(session, cookie)
    return session


def get_cookie(username: Optional[str] = None, password: Optional[str] = None) -> str:
    """登录并返回 cookie 字符串(给需要手动带 Cookie header 的接口)。失败抛 RuntimeError。"""
    cookie = login_jisilu(username, password)
    if not cookie:
        raise RuntimeError("集思录登录失败,请检查网络或 JISILU_USERNAME/JISILU_PASSWORD(网络异常详见日志)")
    return cookie


def fetch_jisilu_list(
    url: str, cookie_str: str, session: Optional[requests.Session] = None
) -> dict:
    """用 cookie 拉取集思录列表接口(etf_list/gold_list/qdii_list 等),返回原始 json。"""
    if not cookie_str:
        logger.error("Cookie 为空,无法请求集思录列表: %s", url)
        return {}
    request_session = session or requests.Session()
    apply_cookie_string(request_session, cookie_str)
    try:
        response = request_session.get(
            url, headers=ETF_LIST_HEADERS, params=_build_etf_list_params(), timeout=10
        )
        response.raise_for_status()
        return response.json()
    except Exception as exc:  # pragma: no cover
        logger.exception("请求集思录列表异常(%s): %s", url, exc)
        return {}
    finally:
        if session is None:
            request_session.close()


def fetch_etf_list(cookie_str: str, session: Optional[requests.Session] = None) -> dict:
    """用 cookie 拉取集思录 ETF 列表(向后兼容)。"""
    return fetch_jisilu_list(ETF_LIST_URL, cookie_str, session)


# 实时列表接口:股票ETF / 黄金ETF / QDII(ETF+LOF),覆盖资产轮动全部标的。
# etf/gold 列表日期字段为 last_dt;qdii 列表为 price_dt(last_dt 恒 None)。
REALTIME_LIST_URLS = [ETF_LIST_URL, GOLD_LIST_URL, QDII_LIST_E_URL, QDII_LIST_L_URL]


def fetch_realtime_lists(
    cookie_str: str, session: Optional[requests.Session] = None
) -> list[dict]:
    """合并拉取股票ETF/黄金ETF/QDII 实时列表,按 fund_id 去重,返回 cell 字典列表。"""
    own_session = session is None
    request_session = session or requests.Session()
    apply_cookie_string(request_session, cookie_str)
    seen: dict[str, dict] = {}
    try:
        for url in REALTIME_LIST_URLS:
            payload = fetch_jisilu_list(url, cookie_str, request_session)
            for row in payload.get("rows", []):
                cell = row.get("cell", {}) if isinstance(row, dict) else {}
                fid = str(cell.get("fund_id", "")).strip()
                if fid and fid not in seen:
                    seen[fid] = cell
        return list(seen.values())
    finally:
        if own_session:
            request_session.close()
