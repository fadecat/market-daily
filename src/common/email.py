"""SMTP 邮件层 + HTML 渲染辅助。

合并自旧仓库 ``notifier_email.py``(渲染 + 发信)与各 ``send_*_email.py`` 的
``load_email_config``(SMTP 配置回退)。板块邮件 = 多 section 聚合:用
``compose_sections()`` 拼好正文,再 ``send_email()`` 发出。
"""
from __future__ import annotations

import re
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate
from pathlib import Path
from typing import Any, Iterable, Optional

from . import env

DEFAULT_SMTP_HOST = "smtp.qq.com"
DEFAULT_SMTP_PORT = 465

WECHAT_COLOR_MAP = {
    "warning": "#D93026",
    "info": "#1AAD19",
    "comment": "#888888",
}


# ---------- HTML 渲染辅助 ----------

def _markdown_to_html(text: str) -> str:
    def font_sub(match):
        name = match.group(1)
        color = WECHAT_COLOR_MAP.get(name, name)
        return f'<span style="color:{color}">{match.group(2)}</span>'

    html = re.sub(r'<font color="([^"]+)">(.*?)</font>', font_sub, text, flags=re.DOTALL)
    html = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', html)

    lines = []
    for line in html.split("\n"):
        stripped = line.strip()
        if stripped == "---":
            lines.append("<hr>")
        elif line.startswith("> "):
            lines.append(f'<div style="margin-left:1em;color:#555">{line[2:]}</div>')
        else:
            lines.append(line + "<br>")
    return "\n".join(lines)


def render_markdown(text: str) -> str:
    """渲染单段 markdown 文本块(企业微信方言)为 HTML。"""
    return f'<div style="margin:8px 0">{_markdown_to_html(text)}</div>'


def render_table(headers: Iterable[str], row_specs: Iterable[dict], column_styles: Optional[dict] = None) -> str:
    """渲染数据表。

    headers: list[str]
    row_specs: list[dict],每项形如
        {"cells": [html, ...], "note": "可选,行下方跨列提示", "row_style": "可选,附加样式"}
    column_styles: dict[int, str],按列追加样式
    """
    column_styles = column_styles or {}
    headers = list(headers)
    th_style = (
        "padding:6px 10px;border-bottom:2px solid #333;text-align:left;"
        "background:#f0f0f0;font-weight:bold;white-space:nowrap"
    )
    td_style = "padding:6px 10px;border-bottom:1px solid #eee;white-space:nowrap"
    note_style = (
        f"padding:2px 10px 6px;border-bottom:1px solid #eee;color:{WECHAT_COLOR_MAP['warning']};font-size:12px"
    )

    thead = "".join(
        f'<th style="{th_style};{column_styles.get(index, "")}">{header}</th>'
        for index, header in enumerate(headers)
    )
    body_rows = []
    for spec in row_specs:
        row_style = spec.get("row_style", "")
        cells = []
        for index, cell in enumerate(spec["cells"]):
            extra_style = column_styles.get(index, "")
            cell_style = td_style if not row_style else f"{td_style};{row_style}"
            if extra_style:
                cell_style = f"{cell_style};{extra_style}"
            cells.append(f'<td style="{cell_style}">{cell}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
        note = spec.get("note")
        if note:
            body_rows.append(f'<tr><td colspan="{len(headers)}" style="{note_style}">{note}</td></tr>')

    return (
        '<table cellpadding="0" cellspacing="0" border="0" '
        'style="border-collapse:collapse;font-size:13px;width:100%;margin:8px 0">'
        f"<thead><tr>{thead}</tr></thead>"
        f'<tbody>{"".join(body_rows)}</tbody>'
        "</table>"
    )


# ---------- 邮件正文拼装 ----------

_SEPARATOR = '<hr style="border:0;border-top:1px solid #ddd;margin:12px 0">'
_WRAPPER_OPEN = (
    '<div style="font-family:-apple-system,BlinkMacSystemFont,PingFang SC,Microsoft YaHei,sans-serif;'
    'font-size:14px;line-height:1.6">'
)


def compose_sections(sections: Iterable[str]) -> str:
    """把多个 section 的 HTML 用分隔线拼成一封邮件正文。"""
    parts = [s for s in sections if s]
    return _WRAPPER_OPEN + _SEPARATOR.join(parts) + "</div>"


# ---------- SMTP 配置与发送 ----------

def load_email_config(*, recipient_env_name: str = "RECEIVER_EMAIL") -> dict[str, Any]:
    recipients_raw = env.get(recipient_env_name) or env.get("EMAIL_TO")
    recipients = [r.strip() for r in recipients_raw.replace(";", ",").split(",") if r.strip()]
    username = (env.get("SMTP_USER") or env.get("EMAIL_USER")).strip()
    password = (env.get("SMTP_PASS") or env.get("EMAIL_PASSWORD")).strip()
    if not recipients or not username or not password:
        raise RuntimeError(f"邮件配置不完整,需要 {recipient_env_name}/SMTP_USER/SMTP_PASS")
    host = (env.get("EMAIL_SMTP_HOST") or DEFAULT_SMTP_HOST).strip() or DEFAULT_SMTP_HOST
    port = int(env.get("EMAIL_SMTP_PORT") or str(DEFAULT_SMTP_PORT))
    return {
        "smtp_host": host,
        "smtp_port": port,
        "username": username,
        "password": password,
        "sender": (env.get("EMAIL_FROM") or username).strip() or username,
        "recipients": recipients,
    }


def _image_subtype(path: str | Path) -> str:
    suf = Path(path).suffix.lower()
    return "jpeg" if suf in {".jpg", ".jpeg"} else "png"


def build_message(
    *,
    subject: str,
    html: str,
    sender: str,
    recipients: list[str],
    text: Optional[str] = None,
    inline_images: Optional[dict[str, str]] = None,
) -> EmailMessage:
    """构建 EmailMessage。inline_images: {cid: 图片路径}。"""
    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender
    message["To"] = ", ".join(recipients)
    message["Date"] = formatdate(localtime=True)
    message.set_content(text or "本邮件为 HTML 格式,请使用支持 HTML 的客户端查看。")
    message.add_alternative(html, subtype="html")

    if inline_images:
        html_part = message.get_body(preferencelist=("html",))
        for cid, path in inline_images.items():
            if not (path and Path(path).exists()):
                print(f"[WARN] inline_images[{cid}] 路径不存在,跳过: {path}")
                continue
            html_part.add_related(
                Path(path).read_bytes(),
                maintype="image",
                subtype=_image_subtype(path),
                cid=f"<{cid}>",
            )
    return message


def send_email(
    subject: str,
    html: str,
    *,
    inline_images: Optional[dict[str, str]] = None,
    config: Optional[dict[str, Any]] = None,
) -> bool:
    """发送一封 HTML 邮件。html 为完整正文(可用 compose_sections 生成)。"""
    config = config or load_email_config()
    message = build_message(
        subject=subject,
        html=html,
        sender=config["sender"],
        recipients=config["recipients"],
        inline_images=inline_images,
    )
    context = ssl.create_default_context()
    with smtplib.SMTP_SSL(config["smtp_host"], int(config["smtp_port"]), context=context, timeout=30) as server:
        server.login(config["username"], config["password"])
        server.send_message(message)
    print(f"[INFO] 邮件推送成功: {subject} -> {len(config['recipients'])} 位收件人")
    return True
