"""src/preview/generate.py 的单元测试。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

from src.preview import generate


def _patch_boards(
    monkeypatch,
    *,
    commodity_code: int = 0,
    fail: tuple[str, ...] = (),
) -> None:
    """把 4 个 board 的 run_preview 替换为写入临时文件的假实现。"""

    def make(board: str):
        def fake(output_path):  # noqa: ANN001 - run_preview 统一关键字调用
            if board in fail:
                raise RuntimeError(f"{board} boom")
            p = Path(output_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(f"<html>{board}</html>", encoding="utf-8")
            return commodity_code if board == "commodity" else p

        return fake

    for board in ("valuation", "rotation", "convertible", "commodity"):
        mod = getattr(generate, f"{board}_run")
        monkeypatch.setattr(mod, "run_preview", make(board))


# ----------------------------- generate_board -----------------------------


def test_generate_board_path_return(monkeypatch, tmp_path):
    """valuation/rotation/convertible 返回 Path 视为成功。"""
    _patch_boards(monkeypatch)
    out = tmp_path / "valuation.html"
    ok, info = generate.generate_board("valuation", out)
    assert ok is True
    assert str(out) in info
    assert out.read_text(encoding="utf-8") == "<html>valuation</html>"


def test_generate_board_commodity_success(monkeypatch, tmp_path):
    """commodity 返回 0 视为成功。"""
    _patch_boards(monkeypatch, commodity_code=0)
    out = tmp_path / "commodity.html"
    ok, info = generate.generate_board("commodity", out)
    assert ok is True
    assert "退出码 0" in info


def test_generate_board_commodity_failure(monkeypatch, tmp_path):
    """commodity 返回非 0 视为失败。"""
    _patch_boards(monkeypatch, commodity_code=1)
    out = tmp_path / "commodity.html"
    ok, info = generate.generate_board("commodity", out)
    assert ok is False
    assert "退出码 1" in info


def test_generate_board_creates_parent(tmp_path, monkeypatch):
    _patch_boards(monkeypatch)
    out = tmp_path / "nested" / "deep" / "rotation.html"
    ok, _ = generate.generate_board("rotation", out)
    assert ok and out.exists()


def test_generate_board_unknown_raises(tmp_path):
    with pytest.raises(KeyError):
        generate.generate_board("nope", tmp_path / "x.html")


def test_generate_board_propagates_exception(monkeypatch, tmp_path):
    """run_preview 抛错时 generate_board 不吞异常(由 generate 捕获)。"""
    _patch_boards(monkeypatch, fail=("convertible",))
    with pytest.raises(RuntimeError, match="convertible boom"):
        generate.generate_board("convertible", tmp_path / "c.html")


# -------------------------------- generate --------------------------------


def test_generate_all_success(monkeypatch, tmp_path, capsys):
    _patch_boards(monkeypatch)
    rc = generate.generate(preview_dir=tmp_path)
    assert rc == 0
    for board in ("valuation", "rotation", "convertible", "commodity"):
        assert (tmp_path / f"{board}.html").exists()
    out = capsys.readouterr()
    assert "全部 4 板块" in out.out


def test_generate_subset(monkeypatch, tmp_path):
    _patch_boards(monkeypatch)
    rc = generate.generate(["valuation", "convertible"], preview_dir=tmp_path)
    assert rc == 0
    assert (tmp_path / "valuation.html").exists()
    assert (tmp_path / "convertible.html").exists()
    assert not (tmp_path / "rotation.html").exists()
    assert not (tmp_path / "commodity.html").exists()


def test_generate_unknown_board(monkeypatch, tmp_path, capsys):
    _patch_boards(monkeypatch)
    rc = generate.generate(["foo"], preview_dir=tmp_path)
    assert rc == 2
    err = capsys.readouterr().err
    assert "未知板块" in err and "foo" in err
    # 不应生成任何文件
    assert not list(tmp_path.glob("*.html"))


def test_generate_exception_failure(monkeypatch, tmp_path, capsys):
    """某板块 run_preview 抛错 -> 该板块计入失败,其余继续。"""
    _patch_boards(monkeypatch, fail=("rotation",))
    rc = generate.generate(preview_dir=tmp_path)
    assert rc == 1
    err = capsys.readouterr().err
    assert "rotation boom" in err
    assert "1 板块预览失败" in err
    # 其余 3 个仍成功生成
    assert (tmp_path / "valuation.html").exists()
    assert (tmp_path / "convertible.html").exists()
    assert (tmp_path / "commodity.html").exists()


def test_generate_commodity_code_failure(monkeypatch, tmp_path, capsys):
    """commodity 返回非 0 -> 计入失败,返回 1。"""
    _patch_boards(monkeypatch, commodity_code=1)
    rc = generate.generate(preview_dir=tmp_path)
    assert rc == 1
    combined = capsys.readouterr()
    assert "退出码 1" in combined.out or "退出码 1" in combined.err
    assert "commodity" in combined.err


def test_generate_multiple_failures(monkeypatch, tmp_path, capsys):
    _patch_boards(monkeypatch, fail=("valuation", "rotation"))
    rc = generate.generate(preview_dir=tmp_path)
    assert rc == 1
    err = capsys.readouterr().err
    assert "2 板块预览失败" in err
    assert "valuation" in err and "rotation" in err


def test_generate_creates_preview_dir(monkeypatch, tmp_path):
    _patch_boards(monkeypatch)
    out_dir = tmp_path / "fresh"  # 不存在
    rc = generate.generate(["valuation"], preview_dir=out_dir)
    assert rc == 0
    assert out_dir.is_dir()


# ---------------------------------- main ----------------------------------


def test_main_no_args_all(monkeypatch, tmp_path):
    """main() 不带参数 -> generate(None) 生成全部。"""
    _patch_boards(monkeypatch)
    monkeypatch.setattr(generate, "_PREVIEW_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate"])
    rc = generate.main()
    assert rc == 0
    assert (tmp_path / "valuation.html").exists()
    assert (tmp_path / "commodity.html").exists()


def test_main_with_board_arg(monkeypatch, tmp_path):
    _patch_boards(monkeypatch)
    monkeypatch.setattr(generate, "_PREVIEW_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate", "valuation"])
    rc = generate.main()
    assert rc == 0
    assert (tmp_path / "valuation.html").exists()
    assert not (tmp_path / "rotation.html").exists()


def test_main_unknown_board(monkeypatch, tmp_path):
    _patch_boards(monkeypatch)
    monkeypatch.setattr(generate, "_PREVIEW_DIR", tmp_path)
    monkeypatch.setattr(sys, "argv", ["generate", "bogus"])
    rc = generate.main()
    assert rc == 2
