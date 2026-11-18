"""数据提取 DAG 拓扑接口：返回节点、连线图结构。"""
from __future__ import annotations

import csv
import io
import json
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.extractor import ExtractorService

router = APIRouter(prefix="/api/extractor", tags=["数据提取"])
service = ExtractorService()
LIST_FIELDS = ["模板编号", "模板名称", "输入URL", "字段映射", "清洗规则", "输出格式", "版本号", "生效批次"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


@router.get("/execution-records")
def list_execution_records(
    entry_id: int | None = Query(default=None),
    report_no: str | None = Query(default=None),
) -> dict[str, Any]:
    """选择器执行记录台账：由选择器校验数据报告动作驱动生成，可按规则或报告过滤。"""
    items = service.list_execution_records(entry_id=entry_id, report_no=report_no)
    return {"items": items, "total": len(items)}


@router.get("/execution-records/{record_no}")
def get_execution_record(record_no: str) -> dict[str, Any]:
    record = service.get_execution_record(record_no)
    if record is None:
        raise HTTPException(status_code=404, detail=f"执行记录 {record_no} 不存在")
    return record


@router.get("")
def get_graph() -> dict[str, Any]:
    """返回节点与边组成的 DAG 图数据。"""
    return service.get_graph()


@router.get("/export")
def export_entries() -> StreamingResponse:
    nodes, edges = service.raw_data()
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(["type", "id", "name", "from", "to", "label", "status"])
    for node in nodes:
        writer.writerow(["node", node.get("id",""), csv_cell(node.get("name","")), "", "", "", node.get("status","")])
    for edge in edges:
        writer.writerow(["edge", "", "", edge.get("from",""), edge.get("to",""), edge.get("label",""), ""])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv; charset=utf-8",
                             headers={"Content-Disposition": "attachment; filename=extractor.csv"})


@router.get("/{entry_id}")
def get_entry(entry_id: int) -> dict:
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"提取模板 {entry_id} 不存在")
    return entry


@router.post("")
def create_entry(payload: EntryPayload) -> ActionResult:
    entry_or_none, missing = service.create_entry(payload.values)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="提取模板已登记", entry=entry_or_none)


@router.post("/{entry_id}/actions")
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    entry_or_none, message = service.run_action(entry_id, payload.action)
    if entry_or_none is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry_or_none)
