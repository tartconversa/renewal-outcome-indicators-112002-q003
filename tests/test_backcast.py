"""回溯重算（back-cast）可行性规则测试。

口径切换后，旧期间只有在分子分母都能由可验证底层证据重建时，
才允许按新口径回算；否则只能保留旧口径并注明原因。
"""

import unittest
from datetime import date
from pathlib import Path

from _bootstrap import SRC  # noqa: F401

from renewal_analytics.concepts import backcast_feasible
from renewal_analytics.stores import DataStore

ROOT = Path(__file__).resolve().parents[1]
AS_OF = date(2026, 9, 30)


class BackcastTests(unittest.TestCase):
    def setUp(self):
        self.store = DataStore.load(ROOT / "data")

    def test_open_facilities_backcast_feasible_with_milestones(self):
        # 2026-06 North 范围（B1: F-1/F-2/F-3）重建 open_facilities：
        # F-3 已开放；F-1 开放日晚于期间末日（可判定未开放）；
        # F-2 开放日未知 → 不可重建。
        feasible, reasons = backcast_feasible(
            self.store,
            numerator_concept="open_facilities",
            denominator_concept="planned_facilities",
            period="2026-06",
            scope_facilities=["F-1", "F-2", "F-3"],
            as_of=AS_OF,
        )
        self.assertFalse(feasible)
        self.assertTrue(any("F-2" in r and "开放里程碑" in r for r in reasons))

    def test_operating_backcast_feasible_when_usage_evidence_complete(self):
        # 2026-08 North（B2: F-1/F-3）：F-1 开放且有 8 月完整使用量；
        # F-3 已并入 F-1 且 8 月无使用量证据 → 不可重建。
        feasible, reasons = backcast_feasible(
            self.store,
            numerator_concept="operating_facilities",
            denominator_concept="planned_facilities",
            period="2026-08",
            scope_facilities=["F-1", "F-3"],
            as_of=AS_OF,
        )
        self.assertFalse(feasible)
        self.assertTrue(any("F-3" in r for r in reasons))

    def test_operating_backcast_feasible_for_single_operating_facility(self):
        # 只对 F-1 重建 2026-08 operating 状态：开放 + 完整使用量 → 可行
        feasible, reasons = backcast_feasible(
            self.store,
            numerator_concept="operating_facilities",
            denominator_concept="planned_facilities",
            period="2026-08",
            scope_facilities=["F-1"],
            as_of=AS_OF,
        )
        self.assertTrue(feasible, reasons)
        self.assertEqual(reasons, ())

    def test_coverage_denominator_requires_complete_observations(self):
        # facilities_with_complete_usage 分母：F-3 在 2026-07 无完整观测
        feasible, reasons = backcast_feasible(
            self.store,
            numerator_concept="monthly_visits",
            denominator_concept="facilities_with_complete_usage",
            period="2026-07",
            scope_facilities=["F-1", "F-3"],
            as_of=AS_OF,
        )
        self.assertFalse(feasible)
        self.assertTrue(
            any("facilities_with_complete_usage" in r and "F-3" in r
                for r in reasons)
        )


if __name__ == "__main__":
    unittest.main()
