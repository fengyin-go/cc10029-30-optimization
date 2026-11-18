"""数据提取 DAG 图服务：节点/边示例结构、流转规则与选择器执行记录台账。

选择器校验数据报告的「驱动数据提取」动作落在本模块：
- 只接受全部阻断已解除且已发布的规则版本（执行只能使用已解除阻断的规则版本）；
- 报告与执行记录在同一事务里提交，失败回滚并保留上一版本；
- 同一报告重复送交只保留一个执行编号，不创建重复记录（幂等）。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any

from app.services.selector import (
    ITEM_BLOCKED,
    REPORT_DEFECT,
    selector_service,
)
from app.store import StoreError, store

MODULE = "extractor"
LEDGER_MODULE = "selector_execution_record"
REQUIRED_FIELDS = ["模板编号", "模板名称", "输入URL"]
STATUS_ORDER = ["草稿", "待校验", "已启用", "已停用"]
ACTION_RULES = {"提交校验": "待校验", "启用配置": "已启用", "停用配置": "已停用"}
NEGATIVE_ACTIONS = ["停用配置"]

EXEC_ACCEPTED = "已受理"
EXEC_REJECTED = "已驳回"


def csv_cell(value: Any) -> str:
    text = str(value) if value is not None else ""
    if text and text[0] in "=+-@":
        text = "'" + text
    return '"' + text.replace('"', '""') + '"'


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


class ExtractorService:
    def get_graph(self) -> dict[str, Any]:
        rows = store.rows(MODULE)
        nodes: list[dict] = []
        edges: list[dict] = []
        for i, row in enumerate(rows):
            nodes.append({"id": int(row.get("id", i+1)), "name": str(row.get(list(row.keys())[1] if len(row)>1 else "name", "")), "status": str(row.get("status", STATUS_ORDER[0])),
                          **{k: row[k] for k in row if k not in ("id","status","pending","abnormal")}})
        for i in range(len(nodes)-1):
            edges.append({"from": nodes[i]["id"], "to": nodes[i+1]["id"], "label": "依赖"})
        return {"nodes": nodes, "edges": edges}

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def create_entry(self, values: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
        missing = [f for f in REQUIRED_FIELDS if not str(values.get(f) or "").strip()]
        if missing:
            return None, missing
        rows = store.rows(MODULE)
        entry = {"id": max((int(r.get("id",0)) for r in rows), default=0) + 1}
        entry.update({f: values.get(f) for f in REQUIRED_FIELDS})
        entry["status"] = STATUS_ORDER[0]
        entry["pending"] = True
        entry["abnormal"] = False
        rows.append(entry)
        return entry, []

    def run_action(self, entry_id: int, action: str) -> tuple[dict[str, Any] | None, str]:
        entry = store.find(MODULE, entry_id)
        if entry is None:
            return None, f"提取模板 {entry_id} 不存在或已归档"
        if action not in ACTION_RULES:
            return None, f"动作「{action}」不属于数据提取可执行范围"
        target = ACTION_RULES[action]
        entry["status"] = target
        entry["pending"] = target != STATUS_ORDER[-1]
        entry["abnormal"] = action in NEGATIVE_ACTIONS
        return entry, f"提取模板已{action}"

    def raw_data(self) -> tuple[list[dict], list[dict]]:
        g = self.get_graph()
        return g["nodes"], g["edges"]

    # ------------------------------------------------------------------ #
    # 选择器执行记录台账：由选择器校验数据报告动作驱动
    # ------------------------------------------------------------------ #
    def list_execution_records(
        self, *, entry_id: int | None = None, report_no: str | None = None
    ) -> list[dict[str, Any]]:
        rows = store.rows(LEDGER_MODULE)
        if entry_id is not None:
            rows = [row for row in rows if int(row.get("entry_id", 0)) == entry_id]
        if report_no:
            rows = [row for row in rows if row.get("report_no") == report_no]
        return rows

    def get_execution_record(self, record_no: str) -> dict[str, Any] | None:
        for row in store.rows(LEDGER_MODULE):
            if row.get("record_no") == record_no:
                return row
        return None

    def drive_from_report(
        self, report_no: str, *, request_no: str | None = None, fail_commit: bool = False
    ) -> tuple[dict[str, Any] | None, str]:
        """读取选择器校验数据报告并生成执行记录，全过程事务化。

        fail_commit 仅用于演练事务失败：置 True 时在写入后、提交前抛错，
        验证报告回写与执行记录一起回滚、上一版本保留。
        """
        report = selector_service.get_report(report_no)
        if report is None:
            return None, f"校验报告 {report_no} 不存在，数据提取无法启动"

        ledger = store.rows(LEDGER_MODULE)

        # 幂等：同一报告重复送交只保留一个执行编号
        duplicate = next((row for row in ledger if row.get("report_no") == report_no), None)
        if duplicate is not None:
            return duplicate, f"报告 {report_no} 已送交过，沿用执行编号 {duplicate['record_no']}，不创建重复记录"
        if request_no:
            idem = next((row for row in ledger if row.get("request_no") == request_no), None)
            if idem is not None:
                return idem, f"请求号 {request_no} 已受理，沿用执行编号 {idem['record_no']}"

        accepted, reason = self._evaluate_report(report)
        try:
            with store.transaction():
                record = {
                    "record_no": self._next_record_no(ledger),
                    "report_no": report_no,
                    "request_no": request_no,
                    "entry_id": report.get("entry_id"),
                    "rule_code": report.get("rule_code"),
                    "rule_version": report.get("rule_version"),
                    "sample_id": report.get("sample_id"),
                    "sample_version": report.get("sample_version"),
                    "status": EXEC_ACCEPTED if accepted else EXEC_REJECTED,
                    "reason": reason,
                    "created_at": _now(),
                }
                ledger.append(record)
                # 报告侧登记执行台账编号，双方在同一事务提交
                report["execution_record_no"] = record["record_no"]
                if fail_commit:
                    raise StoreError("模拟事务提交失败：报告与执行记录一并回滚")
        except StoreError as exc:
            # 回滚后重新读取，确认上一版本仍在、没有残留执行记录
            return None, f"事务提交失败已回滚，保留上一版本：{exc}"

        if accepted:
            return record, f"报告 {report_no} 驱动数据提取，执行编号 {record['record_no']} 已受理"
        return record, f"执行被驳回（执行编号 {record['record_no']}）：{reason}"

    def _evaluate_report(self, report: dict[str, Any]) -> tuple[bool, str]:
        """执行准入：只能使用已解除全部阻断、已发布且报告完整的规则版本。"""
        if report.get("status") == REPORT_DEFECT or not report.get("integrity_ok", True):
            return False, "报告存在错位/丢失/残留缺陷，按缺陷处置，禁止执行"
        blocked = [item["field"] for item in report.get("items", []) if item.get("state") == ITEM_BLOCKED]
        if blocked:
            return False, f"规则字段仍有阻断项：{'、'.join(blocked)}"
        entry = store.find("selector", int(report["entry_id"]))
        if entry is None:
            return False, "关联选择器规则不存在或已归档"
        if entry.get("published_version") != report.get("rule_version"):
            return False, (
                f"报告绑定草稿版本 v{report.get('rule_version')}，"
                f"当前发布版本为 v{entry.get('published_version')}，存在版本冲突，只能执行已发布版本"
            )
        return True, "全部阻断已解除且版本已发布，准予执行"

    @staticmethod
    def _next_record_no(rows: list[dict[str, Any]]) -> str:
        seq = 0
        for row in rows:
            value = str(row.get("record_no", ""))
            if value.startswith("SER-"):
                try:
                    seq = max(seq, int(value[len("SER-"):]))
                except ValueError:
                    continue
        return f"SER-{seq + 1:04d}"


extractor_service = ExtractorService()
