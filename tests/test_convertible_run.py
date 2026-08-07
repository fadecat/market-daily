"""转债行情板块编排层测试(不触网)。"""
from __future__ import annotations

from pathlib import Path


def test_run_send_reads_images_inside_tempdir(monkeypatch):
    """send_email 须在 tempdir 存活期内调用,否则内嵌图已被清理(破图)。

    回归 P0-2:此前 send_email 在 ``with tempfile.TemporaryDirectory()`` 块外,
    tmpdir 退出即清理,build_message 检查路径不存在后静默跳过 -> 每日邮件破图。
    """
    from src.convertible import run as cb_run
    from src.common import email as email_mod

    def fake_build(work_dir):
        img = Path(work_dir) / "chart.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n")
        return {
            "fragments": ['<img src="cid:chart">'],
            "inline_images": {"chart": str(img)},
            "as_of_date": "2026-08-07",
        }

    captured = {}

    def fake_send(subject, html, *, inline_images=None, config=None):
        if inline_images:
            captured["exists"] = all(Path(p).exists() for p in inline_images.values())
        captured["called"] = True
        return True

    monkeypatch.setattr(cb_run, "_build_sections", fake_build)
    monkeypatch.setattr(email_mod, "send_email", fake_send)
    assert cb_run.run_send() == 0
    assert captured.get("called") is True
    assert captured.get("exists") is True  # 关键:发信时图还在(tempdir 未清理)


def test_run_send_alerts_on_send_failure(monkeypatch):
    """发信失败须 notify_alert(回归 P1-2:此前 try 未覆盖 send_email)。"""
    from src.convertible import run as cb_run
    from src.common import email as email_mod, alerts

    def fake_build(work_dir):
        return {"fragments": ["<div>x</div>"], "inline_images": {}, "as_of_date": "2026-08-07"}

    alerted = {}

    def fake_alert(title, detail):
        alerted["title"] = title

    monkeypatch.setattr(cb_run, "_build_sections", fake_build)
    monkeypatch.setattr(email_mod, "send_email", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("smtp down")))
    monkeypatch.setattr(alerts, "notify_alert", fake_alert)

    import pytest
    with pytest.raises(RuntimeError):
        cb_run.run_send()
    assert alerted.get("title") == "转债行情板块运行失败"
