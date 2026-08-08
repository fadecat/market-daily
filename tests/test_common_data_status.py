"""统一数据状态和 webhook 文案测试。"""
from __future__ import annotations

import requests

from src.common.data_status import (
    build_data_alert_title,
    classify_failure,
    dataset_status,
    format_data_failure_detail,
)


def test_archive_dataset_title_uses_business_name():
    assert build_data_alert_title(
        "index_eod",
        code="000300",
        target_name="沪深300",
    ) == "市场估值数据刷新失败：沪深300"


def test_dataset_status_describes_real_impact():
    status = dataset_status("fx")
    assert status["label"] == "汇率"
    assert "汇率图" in status["scope"]


def test_classify_network_failure():
    error = requests.ConnectionError("connection reset")
    assert classify_failure(error) == "网络抓取失败"


def test_classify_field_failure():
    error = RuntimeError("Income statement row not found: 归属母公司净利润")
    assert classify_failure(error) == "字段解析失败"


def test_format_detail_contains_impact_and_raw_error():
    detail = format_data_failure_detail(
        "fx",
        error=RuntimeError("ProxyError: remote end closed connection"),
    )
    assert "影响范围：市场估值汇率图及汇率归档回退" in detail
    assert "原因分类：网络抓取失败" in detail
    assert "原始错误：ProxyError" in detail
