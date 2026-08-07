"""irm 平台路由验证(P1-5):科创板 688/689 应走上证 e 互动。不触网。"""
from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.convertible.irm import query  # noqa: E402


def test_query_irm_routes_kechuang_to_sse(monkeypatch):
    """P1-5: 688/689 科创板应路由到 _query_sse,而非深交所 _query_cninfo。"""
    routes = []
    monkeypatch.setattr(query, "_query_sse", lambda name: routes.append(("sse", name)) or [])
    monkeypatch.setattr(query, "_query_cninfo", lambda name: routes.append(("cninfo", name)) or [])

    query.query_irm("科创A", "688001")
    query.query_irm("科创B", "689009")
    query.query_irm("沪市主板", "600000")
    query.query_irm("深市主板", "000001")

    sse_hits = [r for r in routes if r[0] == "sse"]
    cninfo_hits = [r for r in routes if r[0] == "cninfo"]
    assert len(sse_hits) == 3   # 688 / 689 / 600 -> 上证 e 互动
    assert len(cninfo_hits) == 1  # 000 -> 深交所互动易
