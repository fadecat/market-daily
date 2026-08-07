"""生成各板块的本地预览 HTML(不发信、不走静默守卫)。

每个板块调用对应 board 的 ``run_preview``,把图片以 base64 内嵌后写入
``preview/<board>.html``,可直接用浏览器打开。

用法::

    python -m src.preview.generate                       # 生成全部 5 板块
    python -m src.preview.generate valuation             # 仅生成市场估值
    python -m src.preview.generate rotation convertible  # 指定多个板块

板块名:valuation(市场估值)、rotation(资产轮动)、convertible(转债行情)、
coal(煤炭日报)、commodity(商品极值)。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from src.coal import run as coal_run
from src.commodity import run as commodity_run
from src.convertible import run as convertible_run
from src.rotation import run as rotation_run
from src.valuation import run as valuation_run

_REPO_ROOT = Path(__file__).resolve().parents[2]
_PREVIEW_DIR = _REPO_ROOT / "preview"

# board key -> (中文标签, run 模块)。
# run_preview 在调用时按模块属性查找,便于测试 monkeypatch;coal 的
# run_preview 返回 int 状态码,commodity 与其余返回写入的 Path。
_BOARDS: dict[str, tuple[str, object]] = {
    "valuation": ("市场估值", valuation_run),
    "rotation": ("资产轮动", rotation_run),
    "convertible": ("转债行情", convertible_run),
    "coal": ("煤炭日报", coal_run),
    "commodity": ("商品极值", commodity_run),
}


def generate_board(board_key: str, output_path: Path) -> tuple[bool, str]:
    """生成单个板块预览。

    返回 ``(是否成功, 信息)``。coal 依据返回的 int 状态码判定,
    其余板块返回 Path 即视为成功。失败时由调用方捕获异常。
    """
    if board_key not in _BOARDS:
        raise KeyError(board_key)
    mod = _BOARDS[board_key][1]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    result = mod.run_preview(output_path=output_path)
    if isinstance(result, int):
        return (result == 0, f"退出码 {result}")
    return (True, str(result))


def generate(boards: list[str] | None = None, preview_dir: Path | None = None) -> int:
    """生成指定(或全部)板块预览。

    Args:
        boards: 板块名列表;``None`` 表示全部 4 板块。
        preview_dir: 预览输出目录,默认仓库内 ``preview/``。

    Returns:
        0 全部成功,1 至少一个失败,2 板块名非法。
    """
    targets = boards or list(_BOARDS.keys())
    unknown = [t for t in targets if t not in _BOARDS]
    if unknown:
        print(
            f"[ERROR] 未知板块: {unknown}。可选: {sorted(_BOARDS.keys())}",
            file=sys.stderr,
        )
        return 2

    out_dir = Path(preview_dir) if preview_dir else _PREVIEW_DIR
    out_dir.mkdir(parents=True, exist_ok=True)

    failures: list[str] = []
    for board_key in targets:
        label = _BOARDS[board_key][0]
        out = out_dir / f"{board_key}.html"
        try:
            ok, info = generate_board(board_key, out)
            print(f"[{'OK' if ok else 'FAIL'}] {label} -> {out} ({info})")
            if not ok:
                failures.append(board_key)
        except Exception as exc:  # noqa: BLE001
            print(f"[FAIL] {label} -> {out} ({exc})", file=sys.stderr)
            failures.append(board_key)

    if failures:
        print(
            f"\n[ERROR] {len(failures)} 板块预览失败: {failures}",
            file=sys.stderr,
        )
        return 1
    print(f"\n[OK] 全部 {len(targets)} 板块预览已生成于 {out_dir}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成各板块本地预览 HTML")
    parser.add_argument(
        "boards",
        nargs="*",
        help="板块名(可选): valuation rotation convertible coal commodity。留空=全部",
    )
    args = parser.parse_args()
    return generate(args.boards)


if __name__ == "__main__":
    raise SystemExit(main())
