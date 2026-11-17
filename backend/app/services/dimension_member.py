"""数据维度成员映射表业务规则。

职责：
- 从分析维度与关联数据集两处来源解析层级结构，持久化维度成员映射表；
- 维护版本次序与版本边界（父级更换后的新旧归属各自保留、冲突行不合并）；
- 用锁 + 来源签名保证并发提交只有唯一确认结果；
- 提供按同一版本的回查能力，供跨领域模块（如数据集模块）读取。
"""
from __future__ import annotations

import threading
from datetime import date
from typing import Any

from app.member_hierarchy import (
    apply_conflicts,
    build_mapping_rows,
    source_signature,
)
from app.store import store

MODULE = "dimension_member"
VERSION_MODULE = "dimension_member_version"
DIMENSION_MODULE = "dimension"
DATASET_MODULE = "dataset"

LIST_FIELDS = [
    "映射编号", "映射版本", "维度编号", "维度名称", "关联数据集", "数据集编号",
    "来源输入", "成员名称", "层级深度", "层级路径", "是否叶子", "默认取值",
    "维度版本", "冲突标记", "归属规则", "生效日期",
]
KEYWORD_FIELDS = ["映射编号", "成员名称", "层级路径", "维度编号"]
CONFIRMED_STATUS = "生效中"
SUPERSEDED_STATUS = "已归档"


class DimensionMemberService:
    def __init__(self) -> None:
        # 同步是写操作：串行化提交，配合来源签名去重，保证并发下只确认一个版本。
        self._lock = threading.Lock()

    # ---------- 基础查询 ----------
    def list_entries(
        self,
        *,
        filters: dict[str, str] | None = None,
        keyword: str | None = None,
        version: str | None = None,
        page: int = 1,
        size: int = 20,
    ) -> tuple[list[dict[str, Any]], int]:
        rows = [row for row in store.rows(MODULE)]
        for field, expected in (filters or {}).items():
            rows = [row for row in rows if expected.casefold() in str(row.get(field, "")).casefold()]
        if version:
            rows = [row for row in rows if row.get("映射版本") == version]
        if keyword:
            needle = keyword.casefold()
            rows = [
                row for row in rows
                if any(needle in str(row.get(field, "")).casefold() for field in KEYWORD_FIELDS)
            ]
        total = len(rows)
        start = (page - 1) * size
        return rows[start:start + size], total

    def get_entry(self, entry_id: int) -> dict[str, Any] | None:
        return store.find(MODULE, entry_id)

    def list_versions(self) -> list[dict[str, Any]]:
        """版本次序按序号倒序返回，最新创建的版本排在最前。"""
        return sorted(store.rows(VERSION_MODULE), key=lambda row: int(row.get("version_no", 0)), reverse=True)

    def latest_version_label(self) -> str | None:
        """当前确认版本：去重复用旧版本时生效状态会随来源回滚切换。"""
        for row in self.list_versions():
            if row.get("status") == CONFIRMED_STATUS:
                return str(row["version_label"])
        versions = self.list_versions()
        return str(versions[0]["version_label"]) if versions else None

    def lookup_members(
        self,
        *,
        dataset: str | None = None,
        dimension_code: str | None = None,
        path: str | None = None,
        member_name: str | None = None,
        version: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        """按同一版本回查成员；默认锁定最新已确认版本，避免跨版本读到不同口径。"""
        label = version or self.latest_version_label()
        if label is None:
            return []
        rows = [row for row in store.rows(MODULE) if row.get("映射版本") == label]
        if dataset:
            needle = dataset.casefold()
            rows = [row for row in rows
                    if needle in str(row.get("关联数据集", "")).casefold()
                    or needle in str(row.get("数据集编号", "")).casefold()]
        if dimension_code:
            needle = dimension_code.casefold()
            rows = [row for row in rows if needle in str(row.get("维度编号", "")).casefold()]
        if path:
            needle = path.casefold()
            rows = [row for row in rows if needle in str(row.get("层级路径", "")).casefold()]
        if member_name:
            needle = member_name.casefold()
            rows = [row for row in rows if needle in str(row.get("成员名称", "")).casefold()]
        if source:
            rows = [row for row in rows if row.get("来源输入") == source]
        return rows

    # ---------- 映射表构建与持久化 ----------
    def _dimension_key(self, dim: dict[str, Any]) -> tuple[str, str]:
        return (
            str(dim.get("维度编号", "")).strip(),
            "|".join([
                str(dim.get("层级结构", "")).strip(),
                str(dim.get("关联数据集", "")).strip(),
                str(dim.get("默认取值", "")).strip(),
            ]),
        )

    def _next_dimension_versions(self) -> dict[str, str]:
        """沿用最新已确认版本的维度版本号；来源变化后会出现映射表里没有的新号。"""
        versions = self.list_versions()
        latest = versions[0] if versions else None
        counters: dict[str, int] = dict(latest.get("dimension_versions") or {}) if latest else {}
        result: dict[str, str] = {}
        for dim in store.rows(DIMENSION_MODULE):
            code = str(dim.get("维度编号", "")).strip()
            result[code] = f"{code}@v{counters.get(code, 1)}"
        return result

    def check_chain(self) -> dict[str, Any]:
        """按既有系统调用链核对映射表与来源输入是否一致，供跨领域模块读取前检查。"""
        dimensions = store.rows(DIMENSION_MODULE)
        datasets = store.rows(DATASET_MODULE)
        current_signature = source_signature(dimensions, datasets)
        versions = self.list_versions()
        latest_label = self.latest_version_label()
        latest = next((row for row in versions if row.get("version_label") == latest_label), None)
        dimension_versions = self._next_dimension_versions()
        dataset_names = {str(row.get("数据集名称", "")).strip() for row in datasets}

        missing_dataset_refs = [
            {"维度编号": row.get("维度编号"), "关联数据集": row.get("关联数据集")}
            for row in dimensions
            if str(row.get("关联数据集", "")).strip()
            and str(row.get("关联数据集", "")).strip() not in dataset_names
        ]
        in_sync = bool(latest) and latest.get("source_signature") == current_signature
        unresolved = []
        if not in_sync:
            # 仅在生效版本落后于来源输入时才提示未解析版本；已同步（含签名回滚）时
            # 生效版本里的维度版本就是当前口径，历史版本的旧号不算未解析。
            for dim in dimensions:
                label = dimension_versions.get(str(dim.get("维度编号", "")).strip())
                if label and not any(
                    row.get("映射版本") == latest_label and row.get("维度版本") == label
                    for row in store.rows(MODULE)
                ):
                    unresolved.append({"维度编号": dim.get("维度编号"), "维度版本": label})

        conflict_count = sum(
            1 for row in store.rows(MODULE)
            if latest_label and row.get("映射版本") == latest_label and row.get("冲突标记") != "无"
        )
        return {
            "ok": in_sync and not missing_dataset_refs and not unresolved,
            "in_sync": in_sync,
            "latest_version": latest_label,
            "current_signature": current_signature,
            "confirmed_signature": latest.get("source_signature") if latest else None,
            "dimension_total": len(dimensions),
            "mapping_row_total": len(store.rows(MODULE)),
            "conflict_row_total": conflict_count,
            "missing_dataset_refs": missing_dataset_refs,
            "unresolved_dimension_versions": unresolved,
            "version_total": len(versions),
        }

    def sync_from_dimensions(self, *, trigger: str = "手动同步") -> dict[str, Any]:
        """解析当前分析维度与关联数据集，生成并持久化下一版维度成员映射表。

        并发提交时锁内串行；来源签名与最新版本一致时直接返回该版本，确保最终只有
        唯一确认结果。父级更换会产生冲突行，新旧版本各自保留版本边界。
        """
        with self._lock:
            dimensions = [dict(row) for row in store.rows(DIMENSION_MODULE)]
            datasets = [dict(row) for row in store.rows(DATASET_MODULE)]
            signature = source_signature(dimensions, datasets)
            version_rows = store.rows(VERSION_MODULE)
            for existing in sorted(version_rows, key=lambda row: int(row.get("version_no", 0)), reverse=True):
                if existing.get("source_signature") == signature:
                    # 来源输入回滚到历史版本时，生效状态随同一份映射结果切回；中间版本归档，
                    # 版本边界（映射行与冲突标记）原样保留。
                    if existing.get("status") != CONFIRMED_STATUS:
                        for row in version_rows:
                            row["status"] = SUPERSEDED_STATUS if row is not existing else CONFIRMED_STATUS
                        # 映射行状态与版本表保持一致：复用版本恢复生效，其余版本全部归档。
                        for row in store.rows(MODULE):
                            row["status"] = (
                                CONFIRMED_STATUS
                                if row.get("映射版本") == existing["version_label"]
                                else SUPERSEDED_STATUS
                            )
                    return {
                        "ok": True,
                        "deduplicated": True,
                        "message": f"来源输入与{existing['version_label']}一致，沿用该已确认版本",
                        "version": existing["version_label"],
                        "version_no": existing["version_no"],
                    }

            prev = version_rows[-1] if version_rows else None
            version_no = int(prev["version_no"]) + 1 if prev else 1
            label = f"映射V{version_no}"
            today = date.today().isoformat()

            dimension_versions = self._bump_dimension_versions(prev, dimensions)
            prev_rows = [
                row for row in store.rows(MODULE)
                if prev and row.get("映射版本") == prev["version_label"]
            ]

            # 旧版本整体归档并补历史引用标记：新版本里父级已变更的成员，旧路径保留。
            if prev is not None:
                next_id = max((int(row.get("id", 0)) for row in store.rows(MODULE)), default=0) + 1
                successor_preview = build_mapping_rows(
                    dimensions, datasets,
                    version_label=label, effective_date=today,
                    dimension_versions=dimension_versions, start_id=next_id,
                )
                for row in prev_rows:
                    row["status"] = SUPERSEDED_STATUS
                    row["pending"] = False
                    row["冲突标记"] = "无"
                    row["归属规则"] = ""
                    row["abnormal"] = False
                apply_conflicts(prev_rows, successor=successor_preview, effective_date=today)

            # 回滚后再确认新版本时，其余生效版本一并归档，任意时刻只有唯一确认版本。
            for row in version_rows:
                row["status"] = SUPERSEDED_STATUS
            for row in store.rows(MODULE):
                if row.get("映射版本") != label:
                    row["status"] = SUPERSEDED_STATUS

            next_id = max((int(row.get("id", 0)) for row in store.rows(MODULE)), default=0) + 1
            new_rows = build_mapping_rows(
                dimensions, datasets,
                version_label=label, effective_date=today,
                dimension_versions=dimension_versions, start_id=next_id,
                baseline=prev_rows or None,
            )
            store.rows(MODULE).extend(new_rows)

            version_entry = {
                "id": max((int(row.get("id", 0)) for row in version_rows), default=0) + 1,
                "version_no": version_no,
                "version_label": label,
                "source_signature": signature,
                "row_total": len(new_rows),
                "conflict_total": sum(1 for row in new_rows if row.get("冲突标记") != "无"),
                "effective_date": today,
                "trigger": trigger,
                "dimension_versions": {
                    code: int(version.split("@v", 1)[1]) for code, version in dimension_versions.items()
                },
                "dimension_keys": {code: key for code, key in (
                    self._dimension_key(dim) for dim in dimensions
                )},
                "status": CONFIRMED_STATUS,
                "pending": False,
                "abnormal": False,
            }
            version_rows.append(version_entry)
            return {
                "ok": True,
                "deduplicated": False,
                "message": f"{label} 已按生效日期 {today} 确认",
                "version": label,
                "version_no": version_no,
                "row_total": len(new_rows),
                "conflict_total": version_entry["conflict_total"],
            }

    def _bump_dimension_versions(
        self,
        prev: dict[str, Any] | None,
        dimensions: list[dict[str, Any]],
    ) -> dict[str, str]:
        """同编号维度内容变化时维度版本号 +1，未变化沿用旧号。"""
        prev_keys: dict[str, str] = dict(prev.get("dimension_keys") or {}) if prev else {}
        prev_counters: dict[str, int] = dict(prev.get("dimension_versions") or {}) if prev else {}
        counters = {code: int(no) for code, no in prev_counters.items()}
        result: dict[str, str] = {}
        for dim in dimensions:
            code, key = self._dimension_key(dim)
            no = counters.get(code, 1)
            if code in counters and prev_keys.get(code) != key:
                no = counters[code] + 1
            counters[code] = no
            result[code] = f"{code}@v{no}"
        return result

    # ---------- 来源回查 ----------
    def source_entries(self, member_row: dict[str, Any]) -> dict[str, Any]:
        """重新进入映射表时查看另一处来源：返回同一成员两处来源输入与来源原始记录。"""
        name = member_row.get("成员名称")
        label = member_row.get("映射版本")
        siblings = [
            row for row in store.rows(MODULE)
            if row.get("映射版本") == label
            and row.get("成员名称") == name
            and row.get("维度编号") == member_row.get("维度编号")
            and row.get("层级路径") == member_row.get("层级路径")
        ]
        sources = [
            {
                "来源输入": row.get("来源输入"),
                "映射编号": row.get("映射编号"),
                "层级路径": row.get("层级路径"),
                "维度版本": row.get("维度版本"),
            }
            for row in sorted(siblings, key=lambda row: row.get("来源输入", ""))
        ]
        return {
            "member": {"成员名称": name, "映射版本": label, "层级路径": member_row.get("层级路径")},
            "sources": sources,
            "dimension_source": store.find(DIMENSION_MODULE, int(member_row.get("source_dimension_id") or 0)),
            "dataset_source": (
                store.find(DATASET_MODULE, int(member_row["source_dataset_id"]))
                if member_row.get("source_dataset_id") else None
            ),
        }


# 服务在模块导入时即为单例，路由与维度动作共用同一份锁和版本状态。
service = DimensionMemberService()
