"""数据维度成员映射表接口。

提供：
- 映射表分页筛选（可按数据集、层级路径、成员、来源、版本定位）与 CSV 导出；
- 成员明细、同成员另一处来源输入回查；
- 版本次序、按同一版本的成员回查；
- 从分析维度与关联数据集重新解析持久化、调用链核对。
"""
from __future__ import annotations

import csv
import io

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import StreamingResponse

from app.schemas import ActionResult, PageResult
from app.services.dimension_member import LIST_FIELDS, service

router = APIRouter(prefix="/api/dimension-member", tags=["数据维度成员映射表"])


def query_filters(request: Request) -> dict[str, str]:
    """只接受映射表声明过的字段，忽略分页等保留参数。"""
    return {
        key: value.strip()
        for key, value in request.query_params.items()
        if key in LIST_FIELDS and value.strip()
    }


@router.get("", response_model=PageResult[dict])
def list_entries(
    request: Request,
    keyword: str | None = Query(default=None, description="按映射编号、成员名称、层级路径或维度编号检索"),
    version: str | None = Query(default=None, description="映射版本，缺省返回全部版本"),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=200),
) -> PageResult[dict]:
    """按数据集、层级条件、来源输入与版本过滤维度成员映射表。"""
    items, total = service.list_entries(
        filters=query_filters(request), keyword=keyword, version=version, page=page, size=size)
    return PageResult(items=items, total=total, page=page, size=size)


@router.get("/export")
def export_entries(
    request: Request,
    keyword: str | None = Query(default=None),
    version: str | None = Query(default=None),
) -> StreamingResponse:
    """下载当前筛选条件下的 UTF-8 CSV；必须声明在 /{entry_id} 之前。"""
    items, _ = service.list_entries(
        filters=query_filters(request), keyword=keyword, version=version, page=1, size=1000000)
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
        headers={"Content-Disposition": 'attachment; filename="dimension_member.csv"'})


@router.get("/versions", response_model=list[dict])
def list_versions() -> list[dict]:
    """返回映射版本次序，最新确认版本在前。"""
    return service.list_versions()


@router.get("/lookup", response_model=list[dict])
def lookup_members(
    dataset: str | None = Query(default=None, description="数据集名称或编号"),
    dimension: str | None = Query(default=None, description="维度编号"),
    path: str | None = Query(default=None, description="层级路径关键字"),
    member: str | None = Query(default=None, description="成员名称关键字"),
    version: str | None = Query(default=None, description="缺省锁定最新已确认版本"),
    source: str | None = Query(default=None, description="分析维度 / 关联数据集"),
) -> list[dict]:
    """跨领域模块按同一版本回查成员，层级路径与数据集引用来自同一份映射结果。"""
    return service.lookup_members(
        dataset=dataset, dimension_code=dimension, path=path,
        member_name=member, version=version, source=source)


@router.get("/chain-check")
def chain_check() -> dict:
    """按既有系统调用链核对映射表变化、悬空数据集引用与未解析维度版本。"""
    return service.check_chain()


@router.post("/sync", response_model=ActionResult)
def sync_mapping() -> ActionResult:
    """重新解析分析维度与关联数据集并持久化下一版本；并发提交只确认一个结果。"""
    result = service.sync_from_dimensions()
    return ActionResult(ok=bool(result.get("ok")), message=result.pop("message", ""), entry=result)


@router.get("/{entry_id}", response_model=dict)
def get_entry(entry_id: int) -> dict:
    """读取单条维度成员映射明细，展示当前归属路径与被引用的维度版本。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"维度成员映射 {entry_id} 不存在或已归档")
    return entry


@router.get("/{entry_id}/sources", response_model=dict)
def entry_sources(entry_id: int) -> dict:
    """重新进入映射表时查看该成员另一处来源输入及来源原始记录。"""
    entry = service.get_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"维度成员映射 {entry_id} 不存在或已归档")
    return service.source_entries(entry)
