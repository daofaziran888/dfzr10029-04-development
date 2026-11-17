"""维度成员映射表的单元测试：只用标准库，直接 .venv/bin/python -m unittest 即可。

覆盖：
- 层级结构解析（多分隔符、去重、叶子判定、默认取值）；
- 映射构建（两处来源输入、层级路径与维度版本一致）；
- 版本边界（父级变更新归属 / 历史引用旧归属、冲突不覆盖）；
- 服务版本次序、签名去重、并发提交唯一确认、回滚复用历史版本。
"""
from __future__ import annotations

import threading
import unittest
from unittest import mock

from app import member_hierarchy as mh
from app.member_hierarchy import (
    CONFLICT_HISTORICAL,
    CONFLICT_PARENT_CHANGED,
    SOURCE_DATASET,
    SOURCE_DIMENSION,
    build_mapping_rows,
    parse_hierarchy,
    source_signature,
)
from app.services import dimension_member as dm_module


class HierarchyParseTest(unittest.TestCase):
    def test_parse_multi_separator_and_dedup(self) -> None:
        nodes = parse_hierarchy("全国/华东/上海; 全国/华东/上海；全国＞华北＞北京")
        paths = [node["层级路径"] for node in nodes]
        self.assertEqual(paths, [
            "全国", "全国/华东", "全国/华东/上海",
            "全国/华北", "全国/华北/北京",
        ])
        leaf_names = {node["成员名称"] for node in nodes if node["是否叶子"] == "是"}
        self.assertEqual(leaf_names, {"上海", "北京"})
        depths = {node["层级路径"]: node["层级深度"] for node in nodes}
        self.assertEqual(depths["全国/华东/上海"], 3)

    def test_parse_empty(self) -> None:
        self.assertEqual(parse_hierarchy(""), [])
        self.assertEqual(parse_hierarchy(None), [])

    def test_signature_changes_with_source(self) -> None:
        dims = [{"维度编号": "D1", "层级结构": "A/B", "关联数据集": "DS1", "默认取值": "B"}]
        datasets = [{"数据集名称": "DS1"}]
        base = source_signature(dims, datasets)
        changed_hierarchy = source_signature([{**dims[0], "层级结构": "A/C"}], datasets)
        changed_dataset_list = source_signature(dims, [])
        self.assertNotEqual(base, changed_hierarchy)
        self.assertNotEqual(base, changed_dataset_list)
        self.assertEqual(base, source_signature([dict(dims[0])], [dict(datasets[0])]))


class MappingBuildTest(unittest.TestCase):
    def setUp(self) -> None:
        self.dimensions = [{
            "id": 1, "维度编号": "DIME-0001", "维度名称": "区域",
            "关联数据集": "销售明细数据集",
            "层级结构": "全国/华东/上海; 全国/华北/北京",
            "默认取值": "上海",
        }]
        self.datasets = [{"id": 11, "数据集编号": "DATA-0001", "数据集名称": "销售明细数据集"}]

    def test_two_sources_share_same_path_and_version(self) -> None:
        rows = build_mapping_rows(
            self.dimensions, self.datasets,
            version_label="映射V1", effective_date="2026-10-01",
            dimension_versions={"DIME-0001": "DIME-0001@v1"}, start_id=1,
        )
        # 5 个节点 × 2 处来源
        self.assertEqual(len(rows), 10)
        self.assertEqual({row["来源输入"] for row in rows}, {SOURCE_DIMENSION, SOURCE_DATASET})
        for source in (SOURCE_DIMENSION, SOURCE_DATASET):
            leaf = next(row for row in rows if row["成员名称"] == "上海" and row["来源输入"] == source)
            self.assertEqual(leaf["层级路径"], "全国/华东/上海")
            self.assertEqual(leaf["维度版本"], "DIME-0001@v1")
            self.assertEqual(leaf["默认取值"], "上海")
            self.assertEqual(leaf["关联数据集"], "销售明细数据集")
            self.assertEqual(leaf["数据集编号"], "DATA-0001")

    def test_dimension_without_dataset_keeps_single_source(self) -> None:
        rows = build_mapping_rows(
            self.dimensions, [],
            version_label="映射V1", effective_date="2026-10-01",
            dimension_versions={"DIME-0001": "DIME-0001@v1"}, start_id=1,
        )
        self.assertEqual({row["来源输入"] for row in rows}, {SOURCE_DIMENSION})
        self.assertTrue(all(row["数据集编号"] == "" for row in rows))

    def test_parent_change_conflict_boundaries(self) -> None:
        old_rows = build_mapping_rows(
            [{**self.dimensions[0], "层级结构": "全国/华北/上海; 全国/华北/北京"}],
            self.datasets,
            version_label="映射V1", effective_date="2026-10-01",
            dimension_versions={"DIME-0001": "DIME-0001@v1"}, start_id=1,
            status="已归档",
        )
        new_rows = build_mapping_rows(
            self.dimensions, self.datasets,
            version_label="映射V2", effective_date="2026-11-01",
            dimension_versions={"DIME-0001": "DIME-0001@v2"}, start_id=100,
            baseline=old_rows,
        )
        mh.apply_conflicts(old_rows, successor=new_rows, effective_date="2026-11-01")

        new_sh = [row for row in new_rows if row["成员名称"] == "上海"]
        old_sh = [row for row in old_rows if row["成员名称"] == "上海"]
        self.assertTrue(all(row["冲突标记"] == CONFLICT_PARENT_CHANGED for row in new_sh))
        self.assertTrue(all(row["abnormal"] for row in new_sh))
        self.assertTrue(all("新归属" in row["归属规则"] for row in new_sh))
        self.assertTrue(all(row["冲突标记"] == CONFLICT_HISTORICAL for row in old_sh))
        self.assertTrue(all("旧归属" in row["归属规则"] for row in old_sh))
        # 版本边界：旧路径原样保留，没有被新结构覆盖
        self.assertTrue(all(row["层级路径"] == "全国/华北/上海" for row in old_sh))
        self.assertTrue(all(row["层级路径"] == "全国/华东/上海" for row in new_sh))


class ServiceVersionTest(unittest.TestCase):
    def setUp(self) -> None:
        # 每个用例独立的内存仓库，避免全局 store 串数据。
        self.store_patch = mock.patch.object(dm_module, "store")
        self.fake_store = self.store_patch.start()
        self.tables: dict[str, list] = {
            dm_module.MODULE: [],
            dm_module.VERSION_MODULE: [],
            dm_module.DIMENSION_MODULE: [{
                "id": 1, "维度编号": "DIME-0001", "维度名称": "区域",
                "关联数据集": "销售明细数据集",
                "层级结构": "全国/华东/上海", "默认取值": "上海",
            }],
            dm_module.DATASET_MODULE: [
                {"id": 11, "数据集编号": "DATA-0001", "数据集名称": "销售明细数据集"},
            ],
        }
        self.fake_store.rows.side_effect = lambda key: self.tables.setdefault(key, [])
        self.fake_store.find.side_effect = (
            lambda key, entry_id: next((r for r in self.tables.get(key, []) if r.get("id") == entry_id), None)
        )
        self.service = dm_module.DimensionMemberService()

    def tearDown(self) -> None:
        self.store_patch.stop()

    def test_version_order_dedup_and_rollback(self) -> None:
        first = self.service.sync_from_dimensions()
        self.assertFalse(first["deduplicated"])
        self.assertEqual(first["version"], "映射V1")

        # 相同来源重复提交：去重，不新增版本
        again = self.service.sync_from_dimensions()
        self.assertTrue(again["deduplicated"])
        self.assertEqual(len(self.tables[dm_module.VERSION_MODULE]), 1)

        # 层级变化：确认新版本，旧版本归档
        self.tables[dm_module.DIMENSION_MODULE][0]["层级结构"] = "全国/华北/上海"
        second = self.service.sync_from_dimensions()
        self.assertEqual(second["version"], "映射V2")
        statuses = {v["version_label"]: v["status"] for v in self.service.list_versions()}
        self.assertEqual(statuses, {"映射V1": "已归档", "映射V2": "生效中"})

        # 回滚到 V1 的来源：复用历史版本并重新生效，不新建版本
        self.tables[dm_module.DIMENSION_MODULE][0]["层级结构"] = "全国/华东/上海"
        rolled = self.service.sync_from_dimensions()
        self.assertTrue(rolled["deduplicated"])
        self.assertEqual(rolled["version"], "映射V1")
        self.assertEqual(self.service.latest_version_label(), "映射V1")
        statuses = {v["version_label"]: v["status"] for v in self.service.list_versions()}
        self.assertEqual(statuses, {"映射V1": "生效中", "映射V2": "已归档"})
        # 映射行状态与版本表一致：V1 行恢复生效，V2 行全部归档
        row_status = {
            row["映射版本"]: row["status"] for row in self.tables[dm_module.MODULE]
        }
        self.assertEqual(set(row_status.values()), {"生效中", "已归档"})
        self.assertTrue(all(
            row["status"] == "生效中" for row in self.tables[dm_module.MODULE]
            if row["映射版本"] == "映射V1"
        ))
        self.assertTrue(all(
            row["status"] == "已归档" for row in self.tables[dm_module.MODULE]
            if row["映射版本"] == "映射V2"
        ))

    def test_concurrent_sync_confirms_single_version(self) -> None:
        results: list[dict] = []
        errors: list[Exception] = []

        def worker() -> None:
            try:
                results.append(self.service.sync_from_dimensions())
            except Exception as exc:  # pragma: no cover - 失败时需要看到原因
                errors.append(exc)

        threads = [threading.Thread(target=worker) for _ in range(10)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        self.assertEqual(errors, [])
        confirmed = {r["version"] for r in results if not r["deduplicated"]}
        self.assertEqual(confirmed, {"映射V1"})
        self.assertEqual(len(self.tables[dm_module.VERSION_MODULE]), 1)

    def test_chain_check_detects_dangling_dataset(self) -> None:
        self.service.sync_from_dimensions()
        self.tables[dm_module.DIMENSION_MODULE][0]["关联数据集"] = "缺失数据集"
        report = self.service.check_chain()
        self.assertFalse(report["ok"])
        self.assertEqual(report["missing_dataset_refs"][0]["关联数据集"], "缺失数据集")

        self.tables[dm_module.DIMENSION_MODULE][0]["关联数据集"] = "销售明细数据集"
        report = self.service.check_chain()
        self.assertTrue(report["ok"])


if __name__ == "__main__":
    unittest.main()
