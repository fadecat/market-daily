import json

from src.dividend_observation import run

from src.dividend_observation import data


def test_load_payload_reads_existing_research_json(tmp_path):
    payload_path = tmp_path / "dividend_observation_930955.json"
    payload_path.write_text(
        json.dumps({"meta": {"index_code": "930955"}, "series": {}, "latest": {}}),
        encoding="utf-8",
    )

    payload = data.load_payload(payload_path)

    assert payload["meta"]["index_code"] == "930955"


def test_run_preview_writes_html(monkeypatch, tmp_path):
    raw_payload = {
        "meta": {"analysis_window_years": 3, "display_window_years": 2, "index_name": "红利低波100"},
        "latest": {},
        "series": {},
    }
    display_payload = {
        "meta": {"analysis_window_years": 3, "display_window_years": 2, "index_name": "红利低波100"},
        "latest": {},
        "series": {"dates": ["2026-08-07"]},
    }
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        run.data,
        "build_or_load_payload",
        lambda **_: raw_payload,
    )
    monkeypatch.setattr(
        run.data,
        "prepare_display_payload",
        lambda payload: display_payload,
    )
    monkeypatch.setattr(
        run.charts,
        "generate_chart_bundle",
        lambda payload, work_dir: captured.update({"chart_payload": payload}) or {},
    )
    monkeypatch.setattr(
        run.render,
        "build_preview_html",
        lambda payload, charts, data_uri_map: captured.update({"render_payload": payload}) or "<html>preview</html>",
    )

    output = run.run_preview(tmp_path / "preview.html")

    assert output.exists()
    assert output.read_text(encoding="utf-8") == "<html>preview</html>"
    assert captured["chart_payload"] is display_payload
    assert captured["render_payload"] is display_payload


def test_run_send_uses_dedicated_recipient_and_still_sends_on_partial_chart_failure(
    monkeypatch,
):
    payload = {
        "meta": {"analysis_window_years": 3, "index_name": "红利低波100"},
        "latest": {"date": "2026-08-07", "event_state": "temporary_recovery"},
        "series": {},
    }
    sent: dict[str, object] = {}
    display_payload = {
        "meta": {"analysis_window_years": 3, "display_window_years": 2, "index_name": "红利低波100"},
        "latest": {"date": "2026-08-07", "event_state": "temporary_recovery"},
        "series": {"dates": ["2026-08-07"]},
    }

    monkeypatch.setattr(run.data, "build_or_load_payload", lambda **_: payload)
    monkeypatch.setattr(
        run.data,
        "prepare_display_payload",
        lambda payload: display_payload,
    )
    monkeypatch.setattr(
        run.charts,
        "generate_chart_bundle",
        lambda payload, work_dir: {
            "price": run.charts.ChartResult(
                cid="price",
                image_path=None,
                error="该图暂无数据",
            )
        },
    )
    monkeypatch.setattr(
        run.render,
        "build_email_html",
        lambda payload, charts: "<html>mail</html>",
    )
    monkeypatch.setattr(
        run.email,
        "load_email_config",
        lambda **kwargs: {
            "sender": "sender@example.com",
            "recipients": ["only@example.com"],
            "username": "sender@example.com",
            "password": "secret",
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
        },
    )
    monkeypatch.setattr(
        run.email,
        "send_email",
        lambda subject, html, inline_images=None, config=None: sent.update(
            {
                "subject": subject,
                "html": html,
                "config": config,
                "inline_images": inline_images,
            }
        )
        or True,
    )

    code = run.run_send()

    assert code == 0
    assert sent["config"]["recipients"] == ["only@example.com"]
    assert sent["html"] == "<html>mail</html>"


def test_run_send_force_refreshes_style_rotation_payload(monkeypatch):
    payload = {
        "meta": {"analysis_window_years": 3, "index_name": "红利低波100"},
        "latest": {"date": "2026-08-07", "event_state": "temporary_recovery"},
        "series": {},
    }
    captured: dict[str, object] = {}

    def fake_build_or_load_payload(**kwargs):
        captured.update(kwargs)
        return payload

    monkeypatch.setattr(run.data, "build_or_load_payload", fake_build_or_load_payload)
    monkeypatch.setattr(run.data, "prepare_display_payload", lambda payload: payload)
    monkeypatch.setattr(run.charts, "generate_chart_bundle", lambda payload, work_dir: {})
    monkeypatch.setattr(run.render, "build_email_html", lambda payload, charts: "<html>mail</html>")
    monkeypatch.setattr(
        run.email,
        "load_email_config",
        lambda **kwargs: {
            "sender": "sender@example.com",
            "recipients": ["only@example.com"],
            "username": "sender@example.com",
            "password": "secret",
            "smtp_host": "smtp.qq.com",
            "smtp_port": 465,
        },
    )
    monkeypatch.setattr(run.email, "send_email", lambda *args, **kwargs: True)

    assert run.run_send() == 0
    assert captured["force_refresh_style_rotation"] is True


def test_dividend_observation_workflow_uses_dedicated_receiver_variable():
    from pathlib import Path

    workflow = Path(".github/workflows/dividend-observation.yml")

    assert workflow.exists()
    text = workflow.read_text(encoding="utf-8")
    assert "DIVIDEND_OBSERVATION_RECEIVER_EMAIL" in text
    assert "python -m src.dividend_observation.run" in text
    assert "1-6" in text
