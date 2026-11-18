"""选择器校验样本服务：样本登记、按归属站点读取。

样本表独立于选择器规则表，校验时按规则的「归属站点」绑定实际采样版本。
"""
from __future__ import annotations

from typing import Any

from app.samples import BUILTIN_SAMPLES
from app.store import store

MODULE = "selector_sample"


class SampleService:
    def _table(self) -> list[dict[str, Any]]:
        table = store.rows(MODULE)
        if not table:
            # 惰性加载内置样本，避免改动 seed.py 的既有结构
            table.extend(dict(item) for item in BUILTIN_SAMPLES)
        return table

    def list_samples(self, site_name: str | None = None) -> list[dict[str, Any]]:
        rows = self._table()
        if site_name:
            keyword = site_name.casefold()
            rows = [row for row in rows if keyword in str(row.get("site_name", "")).casefold()]
        return rows

    def get_sample(self, sample_id: str) -> dict[str, Any] | None:
        for row in self._table():
            if row.get("sample_id") == sample_id:
                return row
        return None

    def find_by_site(self, site_name: str) -> dict[str, Any] | None:
        for row in self._table():
            if row.get("site_name") == site_name:
                return row
        return None

    def register_sample(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        sample_id = str(values.get("sample_id") or "").strip()
        site_name = str(values.get("site_name") or "").strip()
        html = str(values.get("html") or "")
        if not sample_id or not site_name:
            return None, "样本编号与归属站点为必填项"
        if not html.strip():
            return None, "样本 HTML 内容为空，无法用于选择器校验"
        table = self._table()
        for row in table:
            if row.get("sample_id") == sample_id:
                return None, f"样本编号 {sample_id} 已存在，重复送交只保留一个编号"
        entry = {
            "sample_id": sample_id,
            "sample_name": str(values.get("sample_name") or sample_id),
            "site_name": site_name,
            "sample_version": str(values.get("sample_version") or "v1"),
            "pagination": values.get("pagination") or {"type": "none", "selector": "", "param": ""},
            "html": html,
        }
        table.append(entry)
        return entry, "校验样本已登记"


sample_service = SampleService()
