"""层级结构解析与维度成员映射表构建。

这里只放不依赖内存仓库的纯函数：层级字符串解析、映射行生成、版本间冲突标记与
来源签名。服务层负责版本号、持久化和并发控制，保证同一份来源输入只能得到同一份
映射结果，层级路径与数据集引用始终来自同一次构建。
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

# 一条层级记录内的多个分支分隔符：分号（中英文）、换行
ENTRY_SPLIT = re.compile(r"[;；\n]+")
# 单条路径上的父子层级分隔符：斜杠、大于号、竖线（中英文）
PATH_SPLIT = re.compile(r"[/>＞|｜]")

SOURCE_DIMENSION = "分析维度"
SOURCE_DATASET = "关联数据集"
SOURCE_ORDER = [SOURCE_DIMENSION, SOURCE_DATASET]

CONFLICT_NONE = "无"
CONFLICT_PARENT_CHANGED = "父级变更"
CONFLICT_HISTORICAL = "历史引用"

RULE_NEW_ATTRIBUTION = "按生效日期采用新归属"
RULE_KEEP_HISTORICAL = "按数据集现存路径采用旧归属"


def parse_hierarchy(raw: Any) -> list[dict[str, Any]]:
    """把层级结构文本解析成成员节点列表。

    例如 ``全国/华东/上海; 全国/华北/北京`` 解析为 5 个去重节点，每个节点带
    成员名称、完整层级路径、层级深度，并依据是否还有下级节点判断叶子。
    """
    nodes: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for entry in ENTRY_SPLIT.split(str(raw or "")):
        chain = [part.strip() for part in PATH_SPLIT.split(entry) if part.strip()]
        path_parts: list[str] = []
        for depth, name in enumerate(chain, start=1):
            path_parts.append(name)
            path = "/".join(path_parts)
            if path in seen_paths:
                continue
            seen_paths.add(path)
            nodes.append({
                "成员名称": name,
                "层级路径": path,
                "层级深度": depth,
            })
    for node in nodes:
        prefix = node["层级路径"] + "/"
        node["是否叶子"] = "是" if not any(path != node["层级路径"] and path.startswith(prefix)
                                          for path in seen_paths) else "否"
    return nodes


def source_signature(dimensions: list[dict[str, Any]], datasets: list[dict[str, Any]]) -> str:
    """计算来源输入签名：分析维度与关联数据集任一变化都会得到不同签名。"""
    dataset_names = {str(row.get("数据集名称", "")).strip() for row in datasets}
    payload = [
        [
            str(row.get("维度编号", "")).strip(),
            str(row.get("层级结构", "")).strip(),
            str(row.get("关联数据集", "")).strip(),
            str(row.get("默认取值", "")).strip(),
            str(row.get("关联数据集", "")).strip() in dataset_names,
        ]
        for row in dimensions
    ]
    digest = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(digest.encode("utf-8")).hexdigest()[:16]


def _index_paths(rows: list[dict[str, Any]]) -> dict[str, set[str]]:
    index: dict[str, set[str]] = {}
    for row in rows:
        index.setdefault(str(row["成员名称"]), set()).add(str(row["层级路径"]))
    return index


def apply_conflicts(
    rows: list[dict[str, Any]],
    *,
    baseline: list[dict[str, Any]] | None = None,
    successor: list[dict[str, Any]] | None = None,
    effective_date: str = "",
) -> None:
    """按版本边界给映射行打冲突标记。

    - 给新版本行传 ``baseline``（上一版本行）：成员还在但路径变了，说明父级更换，
      按生效日期采用新归属。
    - 给旧版本行传 ``successor``（新版本行）：旧路径在新版本已不存在，属于历史
      数据集引用，按数据集现存路径保留旧归属。
    冲突行置 abnormal，版本本身不合并、不覆盖。
    """
    old_paths = _index_paths(baseline or [])
    new_paths = _index_paths(successor or [])
    for row in rows:
        name = str(row["成员名称"])
        path = str(row["层级路径"])
        if baseline and name in old_paths and path not in old_paths[name]:
            row["冲突标记"] = CONFLICT_PARENT_CHANGED
            row["归属规则"] = f"{RULE_NEW_ATTRIBUTION}（生效日期 {effective_date}）"
            row["abnormal"] = True
        elif successor and name in new_paths and path not in new_paths[name]:
            row["冲突标记"] = CONFLICT_HISTORICAL
            row["归属规则"] = RULE_KEEP_HISTORICAL
            row["abnormal"] = True


def build_mapping_rows(
    dimensions: list[dict[str, Any]],
    datasets: list[dict[str, Any]],
    *,
    version_label: str,
    effective_date: str,
    dimension_versions: dict[str, str],
    start_id: int,
    status: str = "生效中",
    baseline: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """按分析维度与关联数据集构建一份完整的维度成员映射表。

    每个层级节点生成映射行；当维度的关联数据集能在数据集清单中匹配上时，同一成员
    会保留两处来源输入（分析维度、关联数据集），两处的层级路径与维度版本完全一致，
    避免筛选落到不同版本的成员上。
    """
    dataset_by_name = {str(row.get("数据集名称", "")).strip(): row for row in datasets}
    rows: list[dict[str, Any]] = []
    next_id = start_id
    for dim in dimensions:
        dim_code = str(dim.get("维度编号", "")).strip()
        dim_name = str(dim.get("维度名称", "")).strip()
        dataset_name = str(dim.get("关联数据集", "")).strip()
        dataset = dataset_by_name.get(dataset_name)
        dim_version = dimension_versions.get(dim_code, f"{dim_code}@v1")
        default_value = str(dim.get("默认取值", "")).strip()
        sources = SOURCE_ORDER if dataset is not None else [SOURCE_DIMENSION]
        for node in parse_hierarchy(dim.get("层级结构")):
            member_default = default_value if node["是否叶子"] == "是" and node["成员名称"] == default_value else ""
            common = {
                "映射版本": version_label,
                "维度编号": dim_code,
                "维度名称": dim_name,
                "数据集编号": str(dataset.get("数据集编号", "")) if dataset else "",
                "关联数据集": dataset_name,
                "成员名称": node["成员名称"],
                "层级深度": node["层级深度"],
                "层级路径": node["层级路径"],
                "是否叶子": node["是否叶子"],
                "默认取值": member_default,
                "维度版本": dim_version,
                "生效日期": effective_date,
                "归属规则": "",
                "冲突标记": CONFLICT_NONE,
                "source_dimension_id": int(dim.get("id", 0) or 0),
                "source_dataset_id": int(dataset.get("id", 0) or 0) if dataset else None,
                "status": status,
                "pending": False,
                "abnormal": False,
            }
            for source in sources:
                row: dict[str, Any] = {"id": next_id, "映射编号": f"MEM-{next_id:04d}"}
                row.update(common)
                row["来源输入"] = source
                rows.append(row)
                next_id += 1
    if baseline is not None:
        apply_conflicts(rows, baseline=baseline, effective_date=effective_date)
    return rows
