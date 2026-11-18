"""抽取规则业务规则：状态流转、字段校验、选择器校验报告与版本边界。

本模块在原有 CRUD/动作接口不变的前提下，新增选择器校验数据报告能力：

- validate_rule 读取「选择器校验样本」，按规则字段逐项校验，失败原因区分为
  空分页（EMPTY_PAGINATION）、匹配失败（MATCH_FAIL）、字段缺失（FIELD_MISSING）；
- 报告按规则版本形成版本边界：规则字段被修改即产生新草稿版本，新版本校验重建报告，
  同版本复核只合并结论——修正分页参数只解除分页字段的阻断，其他失败字段保持原结论；
- 已解除的阻断项在同版本再次校验时不会重现；
- 每条规则对外只暴露一个绑定实际采样版本的报告编号（latest_report_no），
  历史报告保留在报告表中，可按编号追溯；
- 报告送交做完整性核对，错位/丢失/残留按缺陷处置并返回报错原因；
- 报告驱动数据提取生成选择器执行记录（台账由 extractor 服务持有），
  执行只能使用全部阻断已解除且已发布的规则版本，重复送交只保留一个编号。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.validation_engine import (
    BLOCKING_CATEGORIES,
    CATEGORY_LABELS,
    EMPTY_PAGINATION,
    FIELD_MISSING,
    MATCH_FAIL,
    PASS,
    validate_rule,
)
from app.services.sample import sample_service
from app.store import store

MODULE = "selector"
REPORT_MODULE = "selector_validation_report"
LIST_FIELDS = ["规则编号", "归属站点", "列表选择器", "详情选择器", "字段选择器", "分页参数", "等待条件", "字符编码"]
REQUIRED_FIELDS = ["规则编号", "归属站点", "列表选择器"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

# 报告/校验项状态
ITEM_BLOCKED = "阻断"
ITEM_RESOLVED = "已解除"
ITEM_PASS = "通过"
ITEM_HINT = "提示"
REPORT_BLOCKING = "阻断中"
REPORT_PASSED = "已通过"
REPORT_DEFECT = "缺陷"

REPORT_REQUIRED_KEYS = ["report_no", "entry_id", "sample_id", "sample_version", "rule_version", "status", "items"]
ITEM_REQUIRED_KEYS = ["field", "category", "category_label", "blocking", "matched", "detail", "state"]
KNOWN_CATEGORIES = {PASS, EMPTY_PAGINATION, MATCH_FAIL, FIELD_MISSING}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _snapshot(entry: dict[str, Any]) -> dict[str, Any]:
    return {field: entry.get(field) for field in LIST_FIELDS}


def _next_no(rows: list[dict[str, Any]], prefix: str, key: str) -> str:
    seq = 0
    for row in rows:
        value = str(row.get(key, ""))
        if value.startswith(prefix):
            try:
                seq = max(seq, int(value[len(prefix):]))
            except ValueError:
                continue
    return f"{prefix}{seq + 1:04d}"


class SelectorService:
    # ------------------------------------------------------------------ #
    # 原有列表/明细/登记/动作：接口结构保持兼容，仅在明细上附带报告摘要
    # ------------------------------------------------------------------ #
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        status: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = store.rows(MODULE)
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if keyword:
            rows = [row for row in rows if keyword.casefold() in str(row.get("规则编号", "")).casefold()]
        if status:
            rows = [row for row in rows if row.get("status") == status]
        total = len(rows)
        start = (page - 1) * size
        return [self._with_summary(row) for row in rows[start:start + size]], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        entry = store.find(MODULE, entry_id)
        return self._with_summary(entry) if entry else None

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [field for field in REQUIRED_FIELDS if not str(values.get(field) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(row.get("id", 0)) for row in rows), default=0) + 1}
        entry.update({field: values.get(field) for field in LIST_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        # 版本边界：登记即草稿 v1，尚无发布版本与校验报告
        entry["draft_version"] = 1
        entry["published_version"] = None
        entry["latest_report_no"] = None
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"选择器规则 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于抽取规则可执行范围"
        target = ACTION_RULES[action]
        if target not in STATUS_ORDER:
            return None, f"目标状态「{target}」不在允许的状态序列里"
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"选择器规则已{action}"

    # ------------------------------------------------------------------ #
    # 规则修正：修改字段形成新的草稿版本边界，发布版本保持不动
    # ------------------------------------------------------------------ #
    def update_entry(self, entry_id: int, values: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"选择器规则 {entry_id} 不存在或已归档"
        changed = False
        for field in LIST_FIELDS:
            if field in values and str(values.get(field) or "") != str(entry.get(field) or ""):
                entry[field] = values.get(field)
                changed = True
        if not changed:
            return self._with_summary(entry), "规则字段无变化，沿用当前草稿版本"
        entry["draft_version"] = int(entry.get("draft_version", 1)) + 1
        # 已发布规则被修正后回到草稿，发布版本号保留，形成草稿/发布版本冲突的事实基础
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        return self._with_summary(entry), f"规则已修正并生成草稿版本 v{entry['draft_version']}，需重新校验后发布"

    # ------------------------------------------------------------------ #
    # 选择器校验数据报告
    # ------------------------------------------------------------------ #
    def validate_entry(
        self, entry_id: int, sample_id: str | None = None
    ) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"选择器规则 {entry_id} 不存在或已归档"
        sample = self._resolve_sample(entry, sample_id)
        if sample is None:
            return None, f"归属站点「{entry.get('归属站点')}」没有可用校验样本，请先登记样本"

        rule_version = int(entry.get("draft_version", 1))
        fresh_items = validate_rule(_snapshot(entry), sample)
        reports = store.rows(REPORT_MODULE)

        # 同规则同草稿版本同样本的报告已存在：同版本复核，只合并结论，不新建编号
        existing = self._find_report(entry_id, rule_version, str(sample["sample_id"]))
        if existing is not None:
            merged = self._merge_items(existing["items"], fresh_items)
            existing["items"] = merged
            existing["updated_at"] = _now()
            existing["status"] = self._report_status(merged)
            existing["blocking"] = self._has_unresolved_block(merged)
            entry["latest_report_no"] = existing["report_no"]
            return existing, f"复核完成，报告编号 {existing['report_no']} 沿用，已解除的阻断项未重现"

        report = {
            "report_no": _next_no(reports, "SVR-", "report_no"),
            "entry_id": entry_id,
            "rule_code": entry.get("规则编号"),
            "site_name": entry.get("归属站点"),
            "sample_id": sample["sample_id"],
            "sample_name": sample.get("sample_name"),
            "sample_version": sample.get("sample_version"),
            "rule_version": rule_version,
            "rule_version_phase": "published" if entry.get("published_version") == rule_version else "draft",
            "status": REPORT_BLOCKING,
            "blocking": True,
            "integrity_ok": True,
            "integrity_errors": [],
            "items": [self._initial_item_state(item) for item in fresh_items],
            "created_at": _now(),
            "updated_at": _now(),
        }
        report["status"] = self._report_status(report["items"])
        report["blocking"] = self._has_unresolved_block(report["items"])
        reports.append(report)
        # 规则只绑定一个“实际采样版本”的报告编号；旧报告留在表内做历史追溯
        entry["latest_report_no"] = report["report_no"]
        return report, f"校验报告 {report['report_no']} 已生成，绑定样本 {sample['sample_id']}（{sample.get('sample_version')}）"

    def list_reports(self, entry_id: int) -> list[dict[str, Any]]:
        return [row for row in store.rows(REPORT_MODULE) if int(row.get("entry_id", 0)) == entry_id]

    def get_report(self, report_no: str) -> dict[str, Any] | None:
        for row in store.rows(REPORT_MODULE):
            if row.get("report_no") == report_no:
                return row
        return None

    def publish_entry(self, entry_id: int) -> tuple[dict[str, Any] | None, str]:
        """发布当前草稿版本：要求绑定报告无未解除阻断，发布后执行方可使用该版本。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"选择器规则 {entry_id} 不存在或已归档"
        report_no = entry.get("latest_report_no")
        report = self.get_report(str(report_no)) if report_no else None
        if report is None:
            return None, "当前草稿版本尚未生成校验报告，请先校验再发布"
        if int(report.get("rule_version", 0)) != int(entry.get("draft_version", 1)):
            return None, "校验报告绑定的不是当前草稿版本，请修正后重新校验"
        if self._has_unresolved_block(report["items"]):
            failed = [f"{i['field']}（{i['category_label']}）" for i in report["items"] if i.get("state") == ITEM_BLOCKED]
            return None, f"仍有阻断项未解除：{'、'.join(failed)}，不能发布"
        entry["published_version"] = entry["draft_version"]
        report["rule_version_phase"] = "published"
        entry["status"] = "已启用"
        entry["pending"] = False
        entry["abnormal"] = False
        return self._with_summary(entry), f"草稿版本 v{entry['draft_version']} 已发布，执行将只使用该版本"

    def version_conflict(self, entry_id: int) -> dict[str, Any]:
        """核对草稿/发布版本冲突：对外只允许读取绑定实际采样版本的那一个报告。"""
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return {"entry_id": entry_id, "conflict": False, "message": "规则不存在"}
        draft_v = int(entry.get("draft_version", 1))
        published_v = entry.get("published_version")
        bound_no = entry.get("latest_report_no")
        bound = self.get_report(str(bound_no)) if bound_no else None
        conflict = published_v is not None and int(published_v) != draft_v
        return {
            "entry_id": entry_id,
            "draft_version": draft_v,
            "published_version": published_v,
            "conflict": conflict,
            "bound_report_no": bound_no,
            "bound_rule_version": bound.get("rule_version") if bound else None,
            "bound_sample_id": bound.get("sample_id") if bound else None,
            "message": (
                "草稿版本已高于发布版本；最终只读取绑定实际采样版本的报告 "
                f"{bound_no}" if conflict else "草稿与发布版本一致，无版本冲突"
            ),
        }

    # ------------------------------------------------------------------ #
    # 报告完整性核对：错位、丢失、残留按缺陷处置并核对报错原因
    # ------------------------------------------------------------------ #
    def check_integrity(
        self, report_no: str, reported: dict[str, Any] | None = None
    ) -> tuple[dict[str, Any] | None, list[str]]:
        stored = self.get_report(report_no)
        if stored is None:
            return None, [f"报告 {report_no} 不存在，无法核对"]
        target = reported if isinstance(reported, dict) and reported else stored
        errors = self._structural_errors(target)
        if isinstance(reported, dict) and reported:
            # 送交版本与留存版本比对：错位（字段对不上）、丢失（键/项缺失）、残留（多出未知项）
            for key in REPORT_REQUIRED_KEYS:
                if key in stored and key in reported and stored[key] != reported[key]:
                    errors.append(f"字段错位：{key} 送交值与留存报告不一致")
            stored_fields = {i.get("field") for i in stored.get("items", [])}
            reported_fields = {i.get("field") for i in reported.get("items", []) if isinstance(i, dict)}
            for missing in stored_fields - reported_fields:
                errors.append(f"字段丢失：送交报告缺少校验项「{missing}」")
            for residual in reported_fields - stored_fields:
                errors.append(f"数据残留：送交报告出现留存报告没有的校验项「{residual}」")
        stored["integrity_ok"] = not errors
        stored["integrity_errors"] = errors
        if errors:
            stored["status"] = REPORT_DEFECT
            entry = store.find(MODULE, int(stored["entry_id"]))
            if entry is not None:
                entry["abnormal"] = True
        return stored, errors

    # ------------------------------------------------------------------ #
    # 内部工具
    # ------------------------------------------------------------------ #
    def _resolve_sample(self, entry: dict[str, Any], sample_id: str | None) -> dict[str, Any] | None:
        if sample_id:
            return sample_service.get_sample(sample_id)
        return sample_service.find_by_site(str(entry.get("归属站点") or ""))

    def _find_report(self, entry_id: int, rule_version: int, sample_id: str) -> dict[str, Any] | None:
        for row in store.rows(REPORT_MODULE):
            if (
                int(row.get("entry_id", 0)) == entry_id
                and int(row.get("rule_version", 0)) == rule_version
                and row.get("sample_id") == sample_id
                and row.get("status") != REPORT_DEFECT
            ):
                return row
        return None

    @staticmethod
    def _initial_item_state(item: dict[str, Any]) -> dict[str, Any]:
        state = ITEM_PASS
        if item["category"] == EMPTY_PAGINATION:
            state = ITEM_HINT
        elif item["blocking"]:
            state = ITEM_BLOCKED
        return {**item, "state": state, "resolved_at": None}

    def _merge_items(
        self, previous: list[dict[str, Any]], fresh: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """同版本复核合并：已解除阻断不重现；本次通过的阻断项只解除对应字段。"""
        prior = {item["field"]: item for item in previous}
        merged: list[dict[str, Any]] = []
        for item in fresh:
            old = prior.get(item["field"])
            new_item = self._initial_item_state(item)
            if old is not None and old.get("state") == ITEM_RESOLVED:
                # 已解除的阻断项不会重现：保留已解除结论与解除时间
                new_item["state"] = ITEM_RESOLVED
                new_item["resolved_at"] = old.get("resolved_at")
                new_item["detail"] = f"复核通过，阻断已于 {old.get('resolved_at')} 解除"
            elif old is not None and old.get("state") == ITEM_BLOCKED and not new_item["blocking"] and item["category"] == PASS:
                # 本次修正后通过：只解除当前字段自己的阻断
                new_item["state"] = ITEM_RESOLVED
                new_item["resolved_at"] = _now()
                new_item["detail"] = f"修正后复核通过，阻断已于 {new_item['resolved_at']} 解除"
            merged.append(new_item)
        return merged

    @staticmethod
    def _has_unresolved_block(items: list[dict[str, Any]]) -> bool:
        return any(item.get("state") == ITEM_BLOCKED for item in items)

    def _report_status(self, items: list[dict[str, Any]]) -> str:
        if self._has_unresolved_block(items):
            return REPORT_BLOCKING
        return REPORT_PASSED

    @staticmethod
    def _structural_errors(report: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        for key in REPORT_REQUIRED_KEYS:
            if key not in report:
                errors.append(f"报告缺少必需键：{key}")
        items = report.get("items")
        if not isinstance(items, list) or not items:
            errors.append("报告校验项为空或结构丢失")
            return errors
        for index, item in enumerate(items):
            if not isinstance(item, dict):
                errors.append(f"第 {index + 1} 个校验项结构错位，不是对象")
                continue
            for key in ITEM_REQUIRED_KEYS:
                if key not in item:
                    errors.append(f"校验项「{item.get('field', index + 1)}」缺少键：{key}")
            if item.get("category") not in KNOWN_CATEGORIES:
                errors.append(f"校验项「{item.get('field')}」出现未知错误分类：{item.get('category')}")
            if item.get("category") in BLOCKING_CATEGORIES and not item.get("blocking"):
                errors.append(f"校验项「{item.get('field')}」阻断标记与分类不一致")
            if item.get("category") not in KNOWN_CATEGORIES:
                continue
            expected_label = CATEGORY_LABELS[item["category"]]
            if item.get("category_label") and item.get("category_label") != expected_label:
                errors.append(f"校验项「{item.get('field')}」分类文案错位：{item.get('category_label')} ≠ {expected_label}")
        return errors

    def _with_summary(self, entry: dict[str, Any]) -> dict[str, Any]:
        """明细读取时附带报告摘要，不改变既有字段与列表结构。"""
        enriched = dict(entry)
        report_no = entry.get("latest_report_no")
        report = self.get_report(str(report_no)) if report_no else None
        if report is None:
            enriched["validation_summary"] = None
            return enriched
        blocked = [i["field"] for i in report["items"] if i.get("state") == ITEM_BLOCKED]
        hints = [i["field"] for i in report["items"] if i.get("category") == EMPTY_PAGINATION]
        enriched["validation_summary"] = {
            "report_no": report["report_no"],
            "status": report["status"],
            "rule_version": report["rule_version"],
            "sample_id": report["sample_id"],
            "sample_version": report["sample_version"],
            "blocking_fields": blocked,
            "empty_pagination_fields": hints,
            "integrity_ok": report.get("integrity_ok", True),
        }
        return enriched


selector_service = SelectorService()
