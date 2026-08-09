from pathlib import Path

from src.dividend_observation import refresh_local


def test_refresh_local_preview_runs_archive_payload_and_two_previews(monkeypatch, tmp_path):
    calls: list[tuple[str, object]] = []
    raw_payload = {"meta": {"index_name": "红利低波100"}, "series": {}, "latest": {}}
    display_payload = {"meta": {"index_name": "红利低波100"}, "series": {"dates": ["2026-08-09"]}, "latest": {}}

    monkeypatch.setattr(
        refresh_local.refresh_archive,
        "main",
        lambda argv=None: calls.append(("archive", argv)) or 0,
    )
    monkeypatch.setattr(
        refresh_local.data,
        "build_or_load_payload",
        lambda **kwargs: calls.append(("payload", kwargs)) or raw_payload,
    )
    monkeypatch.setattr(
        refresh_local.data,
        "prepare_display_payload",
        lambda payload: calls.append(("display", payload)) or display_payload,
    )
    monkeypatch.setattr(
        refresh_local.research_preview,
        "run_preview",
        lambda **kwargs: calls.append(("research_preview", kwargs)) or Path(tmp_path / "research.html"),
    )
    monkeypatch.setattr(
        refresh_local.email_run,
        "run_preview",
        lambda output_path, payload=None, force_refresh_style_rotation=False: calls.append(
            (
                "email_preview",
                {
                    "output_path": Path(output_path),
                    "payload": payload,
                    "force_refresh_style_rotation": force_refresh_style_rotation,
                },
            )
        )
        or Path(tmp_path / "email.html"),
    )

    code = refresh_local.refresh_local_preview(
        research_output_path=tmp_path / "research.html",
        email_output_path=tmp_path / "email.html",
    )

    assert code == 0
    assert calls[0] == ("archive", ["--config", str(refresh_local.refresh_archive.DEFAULT_CONFIG_PATH)])
    assert calls[1] == (
        "payload",
        {"force_refresh": True, "force_refresh_style_rotation": True},
    )
    assert calls[2] == ("display", raw_payload)
    assert calls[3] == (
        "research_preview",
        {
            "input_path": refresh_local.research_chart.DEFAULT_OUTPUT_PATH,
            "output_path": tmp_path / "research.html",
        },
    )
    assert calls[4] == (
        "email_preview",
        {
            "output_path": tmp_path / "email.html",
            "payload": display_payload,
            "force_refresh_style_rotation": False,
        },
    )


def test_refresh_local_preview_stops_when_archive_refresh_fails(monkeypatch, tmp_path):
    monkeypatch.setattr(refresh_local.refresh_archive, "main", lambda argv=None: 1)
    monkeypatch.setattr(
        refresh_local.data,
        "build_or_load_payload",
        lambda **kwargs: (_ for _ in ()).throw(AssertionError("should not build payload")),
    )

    code = refresh_local.refresh_local_preview(
        research_output_path=tmp_path / "research.html",
        email_output_path=tmp_path / "email.html",
    )

    assert code == 1
