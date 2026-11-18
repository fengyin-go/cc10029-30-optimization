"""抽取规则接口：维护选择器规则，覆盖提交校验、启用配置、停用配置等动作。

在既有列表/导出/明细/登记/动作接口结构不变的前提下，追加：
- 规则修正（PUT）、选择器校验报告（validate/reports/publish/conflict/integrity）；
- 报告驱动数据提取的动作（drive-extraction）；
- 校验样本登记与读取（samples）。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionPayload, ActionResult, EntryPayload, PageResult
from app.services.sample import sample_service
from app.services.selector import SelectorService

router = APIRouter(prefix="/api/selector", tags=["抽取规则"])

service = SelectorService()

LIST_FIELDS = ["规则编号", "归属站点", "列表选择器", "详情选择器", "字段选择器", "分页参数", "等待条件", "字符编码"]
STATUSES = ["草稿", "待校验", "已启用", "已停用"]


def query_filters(request: Request) -> dict[str, str]:
    """只接受当前模块声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("/samples")
def list_samples(site_name: str | None = Query(default=None)) -> dict:
    """读取选择器校验样本；可按归属站点过滤。"""
    return {"items": sample_service.list_samples(site_name), "total": len(sample_service.list_samples(site_name))}


@router.post("/samples", response_model=ActionResult)
def register_sample(payload: dict[str, object]) -> ActionResult:
    """登记一份校验样本；重复样本编号只保留一个。"""
    entry, message = sample_service.register_sample(payload)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按规则编号检索"),
    status: str | None = Query(default=None, description="草稿、待校验、已启用、已停用"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按页面字段、规则编号关键字与状态过滤抽取规则列表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    status: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, status=status, page=1, size=1000000)
    output = io.StringIO()
    output.write("﻿")
    writer = csv.writer(output, lineterminator="\n")
    writer.writerow(["id", *LIST_FIELDS, "status"])
    for item in items:
        row = []
        for value in [item.get("id"), *(item.get(field, "") for field in LIST_FIELDS), item.get("status", "")]:
            text = "" if value is None else str(value)
            row.append("'" + text if text.startswith(("=", "+", "-", "@")) else text)
        writer.writerow(row)
    return StreamingResponse(
        iter([output.getvalue()]), media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="selector.csv"'})


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条选择器规则明细；不存在时给出可读的错误说明。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"选择器规则 {entry_id} 不存在或已归档")
    return entry


@router.post("", response_model=ActionResult)
def create_entry(payload: dict[str, object]) -> ActionResult:
    """登记一条选择器规则，缺字段时说明原因而不是静默丢弃。"""
    entry, missing = service.create_entry(payload)
    if missing:
        return ActionResult(ok=False, message=f"缺少必填字段：{'、'.join(missing)}")
    return ActionResult(ok=True, message="选择器规则已登记", entry=entry)


@router.put("/{entry_id}", response_model=ActionResult)
def update_entry(entry_id: int, payload: EntryPayload) -> ActionResult:
    """修正规则字段：任何字段变化都会形成新的草稿版本边界，发布版本保持不动。"""
    entry, message = service.update_entry(entry_id, payload.values)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.post("/{entry_id}/validate", response_model=ActionResult)
def validate_entry(entry_id: int, payload: dict[str, object] | None = None) -> ActionResult:
    """读取样本校验分页参数与各选择器字段，生成/复核选择器校验数据报告。"""
    payload = payload or {}
    sample_id = str(payload.get("sample_id") or "").strip() or None
    report, message = service.validate_entry(entry_id, sample_id)
    if report is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=report)


@router.get("/{entry_id}/reports")
def list_reports(entry_id: int) -> dict:
    """列出一条规则的全部报告版本；页面读取的绑定报告以明细里的 latest_report_no 为准。"""
    if service.get_entry(entry_id) is None:
        raise HTTPException(status_code=404, detail=f"选择器规则 {entry_id} 不存在或已归档")
    items = service.list_reports(entry_id)
    return {"items": items, "total": len(items)}


@router.get("/reports/{report_no}")
def get_report(report_no: str) -> dict:
    """按编号读取选择器校验数据报告，供调用方继续读取（兼容既有调用方读取结构）。"""
    report = service.get_report(report_no)
    if report is None:
        raise HTTPException(status_code=404, detail=f"校验报告 {report_no} 不存在")
    return report


@router.post("/reports/{report_no}/integrity", response_model=ActionResult)
def check_report_integrity(report_no: str, payload: dict[str, object] | None = None) -> ActionResult:
    """核对报告是否错位、丢失或残留；发现问题按缺陷处置并返回具体报错原因。"""
    report, errors = service.check_integrity(report_no, payload)
    if report is None:
        return ActionResult(ok=False, message="；".join(errors))
    if errors:
        return ActionResult(ok=False, message="报告存在缺陷：" + "；".join(errors), entry=report)
    return ActionResult(ok=True, message="报告完整性核对通过", entry=report)


@router.post("/{entry_id}/publish", response_model=ActionResult)
def publish_entry(entry_id: int) -> ActionResult:
    """发布当前草稿版本：阻断全部解除后才允许，执行只认已发布版本。"""
    entry, message = service.publish_entry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)


@router.get("/{entry_id}/conflict")
def version_conflict(entry_id: int) -> dict:
    """核对草稿规则与发布规则的版本冲突及唯一绑定报告。"""
    return service.version_conflict(entry_id)


@router.post("/{entry_id}/drive-extraction", response_model=ActionResult)
def drive_extraction(entry_id: int, payload: dict[str, object] | None = None) -> ActionResult:
    """报告动作驱动数据提取：生成选择器执行记录，重复送交不产生重复编号。"""
    from app.services.extractor import extractor_service

    payload = payload or {}
    entry = service.get_entry(entry_id)
    if entry is None:
        return ActionResult(ok=False, message=f"选择器规则 {entry_id} 不存在或已归档")
    report_no = entry.get("latest_report_no")
    if not report_no:
        return ActionResult(ok=False, message="规则尚未生成校验报告，无法驱动数据提取")
    request_no = str(payload.get("request_no") or "").strip() or None
    fail_commit = bool(payload.get("fail_commit"))
    record, message = extractor_service.drive_from_report(
        str(report_no), request_no=request_no, fail_commit=fail_commit)
    if record is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=record)


@router.post("/{entry_id}/actions", response_model=ActionResult)
def run_action(entry_id: int, payload: ActionPayload) -> ActionResult:
    """对单条选择器规则执行提交校验、启用配置、停用配置；不允许的动作会被拦下并说明原因。"""
    entry, message = service.run_action(entry_id, payload.action.strip())
    if entry is None:
        return ActionResult(ok=False, message=message)
    return ActionResult(ok=True, message=message, entry=entry)
