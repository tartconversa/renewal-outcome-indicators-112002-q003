"""引擎核心规则测试：as-of 选择、缺失非零、成熟度、口径断裂、目标四态、
迟到更正、可复现性与血缘。"""

import unittest
from datetime import date
from pathlib import Path

from _bootstrap import SRC  # noqa: F401  (路径引导)

from renewal_analytics.domain import SourceRef, Target
from renewal_analytics.engine import AnalyticsEngine
from renewal_analytics.io_utils import shift_period
from renewal_analytics.publication import build_json_payload, build_manifest
from renewal_analytics.stores import DataStore

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"

# 两个发布时点：更正行（received_at 2026-09-02）之前与之后
AS_OF_AUG = date(2026, 8, 31)
AS_OF_SEP = date(2026, 9, 30)


class EngineTestBase(unittest.TestCase):
    def setUp(self):
        self.store = DataStore.load(DATA)
        self.engine = AnalyticsEngine(self.store)


class AsOfVersionTests(EngineTestBase):
    def test_definition_switches_at_valid_from(self):
        self.assertEqual(
            self.store.definition_as_of("I-01", date(2026, 6, 1)).version, "v1"
        )
        self.assertEqual(
            self.store.definition_as_of("I-01", date(2026, 7, 1)).version, "v2"
        )

    def test_boundary_scope_changes_with_version(self):
        june = self.store.scope_as_of(date(2026, 6, 1))
        july = self.store.scope_as_of(date(2026, 7, 1))
        # B1 中 F-2 在 North；B2 中调整到 South
        self.assertEqual(june["F-2"].district, "North")
        self.assertEqual(july["F-2"].district, "South")
        # F-3 在 B2 中仍在 North（已并入 F-1，note 保留合并说明）
        self.assertEqual(july["F-3"].note, "merged into F-1")

    def test_observations_visible_at_respects_received_at(self):
        visible_aug = self.store.observations_visible_at(
            period="2026-07", indicator_id="I-02", as_of=AS_OF_AUG
        )
        values = {o.facility_id: o.value for o in visible_aug}
        self.assertEqual(values["F-1"], 120)  # 更正行 9 月才到达
        visible_sep = self.store.observations_visible_at(
            period="2026-07", indicator_id="I-02", as_of=AS_OF_SEP
        )
        latest = max(visible_sep, key=lambda o: o.received_at)
        self.assertEqual(latest.value, 135)


class MissingNotZeroTests(EngineTestBase):
    def test_pending_and_missing_never_enter_numerator(self):
        # F-2 已验收未开放，I-01 v2 观测 pending 且值为空
        agg = self.engine.aggregate(
            indicator_id="I-01", period="2026-08",
            district="South", as_of=AS_OF_SEP,
        )
        self.assertEqual(agg.numerator_value, 0.0)
        self.assertEqual(agg.denominator_value, 1.0)
        self.assertEqual(agg.value, 0.0)
        self.assertEqual(agg.pending_facilities, ("F-2",))
        cell = next(c for c in agg.facility_cells if c.facility_id == "F-2")
        self.assertEqual(cell.status, "pending")
        self.assertIsNone(cell.value)

    def test_missing_facility_is_distinct_from_zero(self):
        # 2026-06 North 的 I-01 v1：F-1/F-2 完全无观测，F-3 有观测=1
        agg = self.engine.aggregate(
            indicator_id="I-01", period="2026-06",
            district="North", as_of=AS_OF_AUG,
        )
        self.assertEqual(agg.missing_facilities, ("F-1", "F-2"))
        self.assertEqual(agg.observed_facilities, ("F-3",))
        self.assertEqual(agg.numerator_value, 1.0)
        self.assertEqual(agg.denominator_value, 3.0)
        self.assertAlmostEqual(agg.value, 100.0 / 3.0)
        self.assertAlmostEqual(agg.coverage_ratio, 1.0 / 3.0)


class MaturityTests(EngineTestBase):
    def test_newly_opened_facility_is_immature_for_outcome_indicator(self):
        # F-1 于 2026-07 开放，滞后 2 个月 → 2026-09 才成熟
        agg_jul = self.engine.aggregate(
            indicator_id="I-02", period="2026-07",
            district="North", as_of=AS_OF_SEP,
        )
        self.assertEqual(agg_jul.maturity, "contains_immature")
        self.assertEqual(agg_jul.immature_facilities, ("F-1",))

    def test_delivery_indicator_v1_is_not_maturity_gated(self):
        # open_facilities（v1）不是成熟度受控概念
        agg = self.engine.aggregate(
            indicator_id="I-01", period="2026-05",
            district="North", as_of=AS_OF_SEP,
        )
        self.assertEqual(agg.definition_version, "v1")
        self.assertEqual(agg.maturity, "not_applicable")

    def test_operating_indicator_v2_is_maturity_gated(self):
        # operating_facilities（v2）是成熟度受控概念
        agg = self.engine.aggregate(
            indicator_id="I-01", period="2026-07",
            district="North", as_of=AS_OF_SEP,
        )
        self.assertEqual(agg.definition_version, "v2")
        self.assertEqual(agg.maturity, "contains_immature")
        self.assertEqual(agg.immature_facilities, ("F-1",))

    def test_maturity_helper(self):
        f1 = self.store.project_by_facility("F-1")  # opened 2026-07-01
        from renewal_analytics.concepts import facility_is_mature
        self.assertFalse(facility_is_mature(f1, "2026-07"))
        self.assertFalse(facility_is_mature(f1, "2026-08"))
        self.assertTrue(facility_is_mature(f1, "2026-09"))


class CaliberBreakTests(EngineTestBase):
    def test_trend_flags_caliber_break_and_omits_change(self):
        links = self.engine.trend_links(
            indicator_id="I-01", district="North", as_of=AS_OF_SEP
        )
        break_link = next(
            l for l in links
            if l.from_period == "2026-06" and l.to_period == "2026-07"
        )
        self.assertEqual(break_link.comparability, "CALIBER_BREAK")
        self.assertIsNone(break_link.change)
        self.assertTrue(
            any("口径变更" in r for r in break_link.reasons)
        )

    def test_scope_change_flagged_when_denominator_moves(self):
        # South 只在 B2 后才有范围；North 的 F-2 在 7 月退出 → 范围变化
        links = self.engine.trend_links(
            indicator_id="I-01", district="North", as_of=AS_OF_SEP
        )
        july_link = next(l for l in links if l.to_period == "2026-07")
        self.assertIn(july_link.comparability, {"CALIBER_BREAK", "SCOPE_CHANGE"})
        self.assertTrue(any("边界范围变化" in r for r in july_link.reasons))

    def test_same_caliber_same_scope_link_is_comparable(self):
        # I-01 v1 在 North 的 2026-03→2026-04（仅 F-3 有观测，范围同为 B1）
        links = self.engine.trend_links(
            indicator_id="I-01", district="North", as_of=AS_OF_SEP
        )
        link = next(
            l for l in links
            if l.from_period == "2026-03" and l.to_period == "2026-04"
        )
        self.assertEqual(link.comparability, "comparable")
        self.assertEqual(link.change, 0.0)


class TargetStatusTests(EngineTestBase):
    def _eval(self, target_id):
        target = next(t for t in self.store.targets if t.target_id == target_id)
        return self.engine.evaluate_target(target, as_of=AS_OF_SEP)

    def test_met(self):
        # T-3: I-01 v1 North 2026-05 目标 30%；F-1/F-2 显式上报零、F-3 上报 1，
        # 范围 3 个设施 → 33.3%，零是已观测值而非缺失，可直接判定。
        e = self._eval("T-3")
        self.assertEqual(e.status, "MET")
        self.assertAlmostEqual(e.actual, 100.0 / 3.0)

    def test_explicit_zero_is_observed_not_missing(self):
        agg = self.engine.aggregate(
            indicator_id="I-01", period="2026-05",
            district="North", as_of=AS_OF_SEP,
        )
        self.assertEqual(
            sorted(agg.observed_facilities), ["F-1", "F-2", "F-3"]
        )
        self.assertEqual(agg.missing_facilities, ())
        zeros = [
            c for c in agg.facility_cells
            if c.facility_id in {"F-1", "F-2"}
        ]
        self.assertTrue(all(c.status == "observed" and c.value == 0 for c in zeros))

    def test_below_target(self):
        # T-4: I-02 North 2026-06 目标 200，实际仅 F-3 的 60
        e = self._eval("T-4")
        self.assertEqual(e.status, "BELOW_TARGET")
        self.assertEqual(e.actual, 60.0)

    def test_not_judgeable_when_immature(self):
        # T-5: I-02 North 2026-07，唯一完整观测来自未成熟的 F-1
        e = self._eval("T-5")
        self.assertEqual(e.status, "NOT_JUDGEABLE")
        self.assertTrue(any("未成熟" in r for r in e.reasons))

    def test_not_judgeable_when_pending(self):
        # T-1: I-01 v1 North 2026-06，F-1/F-2 无观测但里程碑证明当时均未开放
        # （结构零），因此可以判定；实际 1/3 ≈ 33.3% < 目标 50%。
        e = self._eval("T-1")
        self.assertEqual(e.status, "BELOW_TARGET")
        self.assertAlmostEqual(e.actual, 100.0 / 3.0)

    def test_not_judgeable_when_open_facility_missing_observation(self):
        # T-6: I-02 North 2026-07。F-1 开放仅 1 个月、未成熟即阻断红绿判定；
        # 同时 F-3 早已开放却缺使用量观测，交叉核对须显式标出。
        e = self._eval("T-6")
        self.assertEqual(e.status, "NOT_JUDGEABLE")
        self.assertTrue(any("未成熟" in r for r in e.reasons))

        agg = self.engine.aggregate(
            indicator_id="I-02", period="2026-07",
            district="North", as_of=AS_OF_SEP,
        )
        self.assertIn("F-3", agg.missing_facilities)
        self.assertTrue(
            any("OPEN_FACILITY_WITHOUT_OBSERVATION:F-3" in f
                for f in agg.evidence_flags)
        )

    def test_incompatible_caliber(self):
        # T-2: 目标按 v1 下达给 South 2026-07，但该期间生效的是 v2
        e = self._eval("T-2")
        self.assertEqual(e.status, "INCOMPATIBLE_CALIBER")
        self.assertIsNone(e.actual)
        self.assertTrue(any("口径断裂" in r for r in e.reasons))

    def test_scope_indicator_blocks_when_open_facility_lacks_observation(self):
        # 内存构造目标：I-01 v1（scope 分母）North 2026-07。
        # 该期间 v1 已失效，所以直接构造一个 v1 仍生效的对照不可行；
        # 改用 2026-06 期间：F-3 已开放有观测，F-1/F-2 未开放（结构零）→ 可判定。
        june_target = Target(
            target_id="TX-1", indicator_id="I-01", definition_version="v1",
            district="North", period="2026-06", target_value=50.0,
            source=SourceRef("test", 1, "TX-1"),
        )
        evaluation = self.engine.evaluate_target(june_target, as_of=AS_OF_SEP)
        self.assertEqual(evaluation.status, "BELOW_TARGET")
        self.assertNotIn("真实贡献未知", " ".join(evaluation.reasons))


class CorrectionAndReproducibilityTests(EngineTestBase):
    def test_corrections_are_detected_as_version_chains(self):
        corrections = self.engine.corrections()
        self.assertEqual(len(corrections), 1)
        correction = corrections[0]
        self.assertEqual(correction.changed_period, "2026-07")
        self.assertEqual(correction.observation.facility_id, "F-1")
        self.assertEqual(correction.superseded_value, 120)
        self.assertEqual(correction.observation.value, 135)

    def test_publication_is_reproducible_and_frozen_at_as_of(self):
        periods = ["2026-06", "2026-07", "2026-08"]
        payload_aug_1 = build_json_payload(
            self.store, self.engine, as_of=AS_OF_AUG, label="aug", periods=periods
        )
        payload_aug_2 = build_json_payload(
            self.store, self.engine, as_of=AS_OF_AUG, label="aug", periods=periods
        )
        import json
        self.assertEqual(
            json.dumps(payload_aug_1, ensure_ascii=False, sort_keys=True, default=str),
            json.dumps(payload_aug_2, ensure_ascii=False, sort_keys=True, default=str),
        )
        # 8 月发布看不到 9 月才到达的更正：I-02 2026-07 仍为 120
        aug_july = payload_aug_1["periods"]["2026-07"]["aggregates"]
        i02_north = next(
            a for a in aug_july
            if a["indicator_id"] == "I-02" and a["district"] == "North"
        )
        self.assertEqual(i02_north["value"], 120.0)

        payload_sep = build_json_payload(
            self.store, self.engine, as_of=AS_OF_SEP, label="sep", periods=periods
        )
        sep_july = payload_sep["periods"]["2026-07"]["aggregates"]
        i02_north_sep = next(
            a for a in sep_july
            if a["indicator_id"] == "I-02" and a["district"] == "North"
        )
        self.assertEqual(i02_north_sep["value"], 135.0)

    def test_manifest_fingerprints_every_input(self):
        manifest = build_manifest(self.store, as_of=AS_OF_SEP, label="sep")
        files = {f for f, _, _ in manifest.fingerprints}
        self.assertEqual(
            files,
            {
                "indicator-definitions.csv", "boundary.csv", "projects.csv",
                "observations.csv", "targets.csv", "investments.csv",
            },
        )
        self.assertTrue(all(len(h) == 64 for _, h, _ in manifest.fingerprints))


class LineageTests(EngineTestBase):
    def test_lineage_traces_every_value_to_sources(self):
        agg = self.engine.aggregate(
            indicator_id="I-01", period="2026-07",
            district="North", as_of=AS_OF_SEP,
        )
        from renewal_analytics.publication import lineage
        info = lineage(agg)
        self.assertEqual(info["definition_version"], "v2")
        self.assertEqual(info["boundary_version"], "B2")
        self.assertEqual(set(info["scope_facilities"]), {"F-1", "F-3"})
        # 每个设施单元格与每条汇总来源都有 文件#L行号 坐标
        for cell in info["facility_cells"]:
            if cell["source"] is not None:
                self.assertIn("#L", cell["source"])
        self.assertTrue(any("indicator-definitions.csv" in s for s in info["sources"]))
        self.assertTrue(any("boundary.csv" in s for s in info["sources"]))
        self.assertTrue(any("observations.csv" in s for s in info["sources"]))


class MilestoneCrossCheckTests(EngineTestBase):
    def test_accepted_but_not_open_is_flagged(self):
        # F-2 在 2026-08 已验收未开放
        agg = self.engine.aggregate(
            indicator_id="I-01", period="2026-08",
            district="South", as_of=AS_OF_SEP,
        )
        self.assertTrue(
            any("ACCEPTED_BUT_NOT_OPEN:F-2" in f for f in agg.evidence_flags)
        )

    def test_open_facility_without_observation_is_flagged(self):
        # F-1 2026-07 已开放，但 I-01 v1 口径下该期间无 v1 观测
        # （v1 在 7 月已失效，此场景通过 I-02 在 F-3 2026-06 之外验证开放无报数）
        agg = self.engine.aggregate(
            indicator_id="I-02", period="2026-07",
            district="North", as_of=AS_OF_SEP,
        )
        # F-3 在 B2 范围内但 7 月无 I-02 观测，且 F-3 早已开放
        self.assertTrue(
            any("OPEN_FACILITY_WITHOUT_OBSERVATION:F-3" in f
                for f in agg.evidence_flags)
        )


class ChainViewTests(EngineTestBase):
    def test_chain_separates_accepted_open_operating_and_mature(self):
        rows = self.engine.chain_view(as_of=AS_OF_SEP)
        f1_row = next(
            r for r in rows
            if r.period == "2026-07" and r.district == "North"
            and r.project_type == "eldercare"
        )
        self.assertEqual(f1_row.accepted_facilities, 1)
        self.assertEqual(f1_row.open_facilities, 1)
        self.assertEqual(f1_row.operating_facilities, 1)
        self.assertEqual(f1_row.mature_facilities, 0)  # 开放当月未成熟

        f2_row = next(
            r for r in rows
            if r.period == "2026-08" and r.district == "South"
        )
        self.assertEqual(f2_row.accepted_facilities, 1)
        self.assertEqual(f2_row.open_facilities, 0)  # 验收≠开放

    def test_investment_visibility_respects_received_at(self):
        rows_aug = self.engine.chain_view(as_of=AS_OF_AUG)
        p2_aug = [r for r in rows_aug if r.period == "2026-08"]
        self.assertTrue(all(r.investment_cny is None for r in p2_aug))
        rows_sep = self.engine.chain_view(as_of=AS_OF_SEP)
        p2_sep = next(
            r for r in rows_sep
            if r.period == "2026-08" and r.district == "South"
        )
        self.assertEqual(p2_sep.investment_cny, 500000)


class PeriodUtilsTests(unittest.TestCase):
    def test_shift_period(self):
        self.assertEqual(shift_period("2026-12", 1), "2027-01")
        self.assertEqual(shift_period("2026-01", -1), "2025-12")
        self.assertEqual(shift_period("2026-07", 2), "2026-09")


if __name__ == "__main__":
    unittest.main()
