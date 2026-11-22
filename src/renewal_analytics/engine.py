"""成效分析引擎。

所有查询都显式接收 as_of 发布时点：
- 定义、边界按 as_of 做 as-of 选择；
- 观测只取 received_at <= as_of 的版本，迟到数据不影响已发布快照；
- 每个汇总值附带定义版本、边界版本、设施级单元格与全部来源，支持血缘追溯。
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from typing import Optional

from . import concepts
from .domain import (
    Aggregate,
    ChainRow,
    Correction,
    FacilityCell,
    Observation,
    TargetEvaluation,
    TrendLink,
)
from .io_utils import period_key, period_to_date
from .stores import DataStore

# 里程碑/观测交叉核对产生的证据标记
EVIDENCE_OPEN_NOT_REPORTED = "OPEN_FACILITY_WITHOUT_OBSERVATION"
EVIDENCE_ACCEPTED_NOT_OPEN = "ACCEPTED_BUT_NOT_OPEN"
EVIDENCE_PENDING_UNCONFIRMED = "PENDING_OBSERVATION_EXCLUDED"
EVIDENCE_REPORTED_NOT_MATURE = "REPORTED_BUT_FACILITY_IMMATURE"


@dataclass(frozen=True)
class PublicationContext:
    """一次发布的冻结上下文。"""

    as_of: date
    label: str


class AnalyticsEngine:
    def __init__(self, store: DataStore):
        self.store = store

    # ------------------------------------------------------------------
    # 汇总
    # ------------------------------------------------------------------

    def aggregate(
        self,
        *,
        indicator_id: str,
        period: str,
        district: str,
        as_of: date,
        boundary_version: Optional[str] = None,
    ) -> Optional[Aggregate]:
        definition = self.store.definition_as_of(indicator_id, period_to_date(period))
        if definition is None:
            return None

        if boundary_version is None:
            boundary_version = self.store.boundary_version_as_of(period_to_date(period))
        if boundary_version is None:
            return None
        scope = {
            f: b for f, b in self.store.scope_for_version(boundary_version).items()
            if district == "ALL" or b.district == district
        }
        if not scope:
            return None

        period_obs = [
            o for o in self.store.observations_visible_at(
                period=period, indicator_id=indicator_id, as_of=as_of
            )
            if o.definition_version == definition.version and o.facility_id in scope
        ]
        latest = self._latest_per_facility(period_obs)

        cells: list[FacilityCell] = []
        evidence_flags: list[str] = []
        observed: list[str] = []
        pending: list[str] = []
        missing: list[str] = []
        immature: list[str] = []

        gated = concepts.is_maturity_gated(definition.numerator)
        for facility_id in sorted(scope):
            project = self.store.project_by_facility(facility_id)
            obs = latest.get(facility_id)
            cell = self._classify_cell(facility_id, obs)
            cells.append(cell)
            if cell.status == "observed":
                observed.append(facility_id)
            elif cell.status == "pending":
                pending.append(facility_id)
                evidence_flags.append(f"{EVIDENCE_PENDING_UNCONFIRMED}:{facility_id}")
            else:
                missing.append(facility_id)

            if gated and project is not None and project.opened_on is not None:
                # 仅对期间内已经开放的设施谈成熟度；期间末仍未开放者不进名单。
                if project.opened_on <= concepts.period_end(period) \
                        and not concepts.facility_is_mature(project, period):
                    immature.append(facility_id)
                    if cell.status == "observed":
                        evidence_flags.append(
                            f"{EVIDENCE_REPORTED_NOT_MATURE}:{facility_id}"
                        )

            self._milestone_cross_check(
                facility_id, project, obs, period, evidence_flags
            )

        numerator_value, denominator_value, value = self._compute_values(
            definition.numerator, definition.denominator, cells, sorted(scope)
        )

        coverage = (
            len(observed) / len(scope) if definition.numerator != "monthly_visits"
            else (len(observed) / len(scope) if scope else None)
        )
        maturity = "not_applicable"
        if gated:
            maturity = "contains_immature" if immature else "mature"

        sources = tuple(
            sorted(
                {o.source for o in period_obs}
                | {scope[f].source for f in scope}
                | {definition.source},
                key=lambda s: (s.file, s.line_number),
            )
        )

        return Aggregate(
            period=period,
            indicator_id=indicator_id,
            definition_version=definition.version,
            district=district,
            unit=definition.unit,
            numerator_concept=definition.numerator,
            denominator_concept=definition.denominator,
            value=value,
            numerator_value=numerator_value,
            denominator_value=denominator_value,
            scope_facilities=tuple(sorted(scope)),
            boundary_version=boundary_version,
            observed_facilities=tuple(observed),
            pending_facilities=tuple(pending),
            missing_facilities=tuple(missing),
            immature_facilities=tuple(sorted(immature)),
            coverage_ratio=coverage,
            maturity=maturity,
            evidence_flags=tuple(evidence_flags),
            sources=sources,
            facility_cells=tuple(cells),
        )

    def all_aggregates(self, *, period: str, as_of: date) -> list[Aggregate]:
        """发布一期所需的全部（指标 × 片区，含 ALL）汇总。"""
        results: list[Aggregate] = []
        boundary_version = self.store.boundary_version_as_of(period_to_date(period))
        if boundary_version is None:
            return results
        districts = sorted({
            b.district
            for b in self.store.boundaries
            if b.version == boundary_version
        }) + ["ALL"]
        indicator_ids = sorted({d.indicator_id for d in self.store.definitions})
        for indicator_id in indicator_ids:
            for district in districts:
                aggregate = self.aggregate(
                    indicator_id=indicator_id,
                    period=period,
                    district=district,
                    as_of=as_of,
                    boundary_version=boundary_version,
                )
                if aggregate is not None:
                    results.append(aggregate)
        return results

    # ------------------------------------------------------------------
    # 目标判定（四态）
    # ------------------------------------------------------------------

    def evaluate_target(self, target, *, as_of: date) -> TargetEvaluation:
        definition = self.store.definition_as_of(
            target.indicator_id, period_to_date(target.period)
        )
        # 1) 口径不兼容优先：目标绑定的定义版本与期间生效版本不一致，
        #    且无法按目标口径回溯重算。
        if definition is None or definition.version != target.definition_version:
            return TargetEvaluation(
                target=target,
                status="INCOMPATIBLE_CALIBER",
                actual=None,
                reasons=(
                    f"目标按 {target.indicator_id}@{target.definition_version} 下达，"
                    f"期间 {target.period} 生效口径为 "
                    f"{definition.id_version if definition else '无生效定义'}，"
                    "口径断裂且未提供同口径回溯重算",
                ),
                aggregate=None,
            )

        aggregate = self.aggregate(
            indicator_id=target.indicator_id,
            period=target.period,
            district=target.district,
            as_of=as_of,
        )
        if aggregate is None:
            return TargetEvaluation(
                target=target,
                status="NOT_JUDGEABLE",
                actual=None,
                reasons=("该片区在目标期间无边界范围，无法构造分母",),
                aggregate=None,
            )

        # 2) 尚不可判断：
        #    - 待确认观测（pending）一律阻断——其值未知，不能当零；
        #    - scope 口径（分母为范围内全部设施）下，已开放却缺观测的设施属于
        #      "无法解释的缺失"：它必然进入分母、分子却未知，阻断判定；
        #      尚未开放设施的零贡献是里程碑可证实的结构零，不阻断；
        #    - coverage 口径（分母为有完整观测的设施数）自我归一化，缺失设施
        #      既不进分子也不进分母，不阻断，但在汇总的覆盖率中披露；
        #    - 结果类指标含未成熟设施时阻断——项目未到成熟期，不能与成熟项目比较。
        reasons: list[str] = []
        if aggregate.pending_facilities:
            reasons.append(
                "待确认观测（不计零）: " + ", ".join(aggregate.pending_facilities)
            )
        denominator_is_scope = (
            concepts.spec(aggregate.denominator_concept).value_kind == "scope_count"
        )
        unexplained_missing = []
        if denominator_is_scope:
            end = concepts.period_end(target.period)
            for facility_id in aggregate.missing_facilities:
                project = self.store.project_by_facility(facility_id)
                if (
                    project is not None
                    and project.opened_on is not None
                    and project.opened_on <= end
                ):
                    unexplained_missing.append(facility_id)
        if unexplained_missing:
            reasons.append(
                "已开放设施缺失观测，真实贡献未知（不得按零处理）: "
                + ", ".join(unexplained_missing)
            )
        if aggregate.maturity == "contains_immature":
            reasons.append(
                "含未成熟设施，结果尚不稳定: "
                + ", ".join(aggregate.immature_facilities)
            )
        if reasons:
            return TargetEvaluation(
                target=target,
                status="NOT_JUDGEABLE",
                actual=aggregate.value,
                reasons=tuple(reasons),
                aggregate=aggregate,
            )

        # 3) 口径一致、证据完整且成熟后，才允许红绿判定。
        if aggregate.value is None:
            return TargetEvaluation(
                target=target,
                status="NOT_JUDGEABLE",
                actual=None,
                reasons=("没有任何完整观测可用于比较",),
                aggregate=aggregate,
            )
        if aggregate.value >= target.target_value:
            status = "MET"
            reasons.append(
                f"实际 {self._fmt(aggregate)} >= 目标 {target.target_value}"
            )
        else:
            status = "BELOW_TARGET"
            reasons.append(
                f"实际 {self._fmt(aggregate)} < 目标 {target.target_value}"
            )
        return TargetEvaluation(
            target=target,
            status=status,
            actual=aggregate.value,
            reasons=tuple(reasons),
            aggregate=aggregate,
        )

    def evaluate_all_targets(self, *, as_of: date) -> list[TargetEvaluation]:
        return [
            self.evaluate_target(target, as_of=as_of)
            for target in sorted(self.store.targets, key=lambda t: t.target_id)
        ]

    # ------------------------------------------------------------------
    # 趋势（带可比性标记）
    # ------------------------------------------------------------------

    def trend_links(
        self, *, indicator_id: str, district: str, as_of: date
    ) -> list[TrendLink]:
        periods = sorted(
            {o.period for o in self.store.observations if o.indicator_id == indicator_id},
            key=period_key,
        )
        links: list[TrendLink] = []
        previous = None
        for period in periods:
            current = self.aggregate(
                indicator_id=indicator_id,
                period=period,
                district=district,
                as_of=as_of,
            )
            if current is None:
                continue
            if previous is None:
                links.append(
                    TrendLink(indicator_id, district, "", period,
                              "NO_BASELINE", None, ("首期，无前期可比基线",))
                )
            else:
                links.append(self._compare(previous, current))
            previous = current
        return links

    def _compare(self, previous: Aggregate, current: Aggregate) -> TrendLink:
        reasons: list[str] = []
        caliber_break = previous.definition_version != current.definition_version
        scope_change = set(previous.scope_facilities) != set(current.scope_facilities)
        if caliber_break:
            reasons.append(
                f"口径变更: {previous.indicator_id}@{previous.definition_version} → "
                f"{current.indicator_id}@{current.definition_version}，"
                "分子概念不同，变化量不可直接解释"
            )
        if scope_change:
            removed = sorted(set(previous.scope_facilities) - set(current.scope_facilities))
            added = sorted(set(current.scope_facilities) - set(previous.scope_facilities))
            reasons.append(
                "边界范围变化（分母不可比）"
                + (f"；退出: {','.join(removed)}" if removed else "")
                + (f"；新增: {','.join(added)}" if added else "")
                + f"；边界版本 {previous.boundary_version} → {current.boundary_version}"
            )
        if caliber_break:
            comparability = "CALIBER_BREAK"
        elif scope_change:
            comparability = "SCOPE_CHANGE"
        else:
            comparability = "comparable"
        change = None
        if comparability == "comparable" and previous.value is not None and current.value is not None:
            change = current.value - previous.value
        return TrendLink(
            current.indicator_id, current.district,
            previous.period, current.period,
            comparability, change, tuple(reasons),
        )

    # ------------------------------------------------------------------
    # 迟到更正
    # ------------------------------------------------------------------

    def corrections(self) -> list[Correction]:
        """识别对同一 (期间, 指标版本, 设施) 的后续版本。"""
        chains: dict[tuple, list[Observation]] = defaultdict(list)
        for obs in self.store.observations:
            key = (obs.period, obs.indicator_id, obs.definition_version, obs.facility_id)
            chains[key].append(obs)

        corrections: list[Correction] = []
        for key, versions in chains.items():
            versions = sorted(versions, key=lambda o: o.received_at)
            for newer, older in zip(versions[1:], versions[:-1]):
                corrections.append(
                    Correction(
                        observation=newer,
                        superseded_value=older.value,
                        superseded_source=older.source,
                        changed_period=key[0],
                    )
                )
        return sorted(corrections, key=lambda c: (c.observation.received_at, c.observation.source.key))

    # ------------------------------------------------------------------
    # 投入—交付—结果链
    # ------------------------------------------------------------------

    def chain_view(self, *, as_of: date) -> list[ChainRow]:
        periods = sorted(
            {o.period for o in self.store.observations}
            | {i.period for i in self.store.investments if i.received_at <= as_of},
            key=period_key,
        )
        rows: list[ChainRow] = []
        for period in periods:
            boundary_version = self.store.boundary_version_as_of(period_to_date(period))
            if boundary_version is None:
                continue
            scope = self.store.scope_for_version(boundary_version)
            groups: dict[tuple[str, str], list[str]] = defaultdict(list)
            for facility_id, assignment in scope.items():
                project = self.store.project_by_facility(facility_id)
                project_type = project.project_type if project else "unknown"
                groups[(assignment.district, project_type)].append(facility_id)

            for (district, project_type), facilities in sorted(groups.items()):
                investment = sum(
                    inv.amount_cny
                    for inv in self.store.investments
                    if inv.period == period
                    and inv.received_at <= as_of
                    and inv.project_id in {
                        p.project_id
                        for f in facilities
                        if (p := self.store.project_by_facility(f)) is not None
                    }
                )
                accepted = open_ = operating = mature = 0
                sources = []
                for facility_id in facilities:
                    project = self.store.project_by_facility(facility_id)
                    if project is None:
                        continue
                    sources.append(project.source)
                    end = concepts.period_end(period)
                    if project.accepted_on is not None and project.accepted_on <= end:
                        accepted += 1
                    if project.opened_on is not None and project.opened_on <= end:
                        open_ += 1
                        if concepts.facility_is_mature(project, period):
                            mature += 1
                    usage = [
                        o for o in self.store.observations_visible_at(
                            period=period, facility_id=facility_id, as_of=as_of
                        )
                        if o.indicator_id == "I-02"
                        and o.quality == "complete" and o.value is not None
                    ]
                    if usage:
                        operating += 1
                rows.append(
                    ChainRow(
                        period=period,
                        district=district,
                        project_type=project_type,
                        investment_cny=investment or None,
                        accepted_facilities=accepted,
                        open_facilities=open_,
                        operating_facilities=operating,
                        mature_facilities=mature,
                        facility_count=len(facilities),
                        sources=tuple(sources),
                    )
                )
        return rows

    # ------------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------------

    @staticmethod
    def _latest_per_facility(observations: list[Observation]) -> dict[str, Observation]:
        latest: dict[str, Observation] = {}
        for obs in observations:
            current = latest.get(obs.facility_id)
            if current is None or obs.received_at > current.received_at:
                latest[obs.facility_id] = obs
        return latest

    @staticmethod
    def _classify_cell(facility_id: str, obs: Optional[Observation]) -> FacilityCell:
        if obs is None:
            return FacilityCell(facility_id, "missing", None, None, None,
                                ("期间内无任何观测记录",))
        # complete 与带值的 corrected 都视为已观测（更正属性保留在 quality 中，
        # 由更正登记与发布快照另行说明）；零值是显式上报的零，与缺失严格区分。
        if obs.value is not None and obs.quality in {"complete", "corrected"}:
            return FacilityCell(facility_id, "observed", obs.value, obs.quality, obs.source)
        reason = "观测质量为 pending，未确认"
        if obs.quality == "complete" and obs.value is None:
            reason = "complete 观测但值为空，按缺失处理而非零"
        if obs.quality == "corrected":
            reason = "更正行缺少可用值"
        return FacilityCell(facility_id, "pending", obs.value, obs.quality,
                            obs.source, (reason,))

    def _milestone_cross_check(
        self,
        facility_id: str,
        project,
        obs: Optional[Observation],
        period: str,
        evidence_flags: list[str],
    ) -> None:
        if project is None:
            return
        end = concepts.period_end(period)
        if project.accepted_on is not None and project.accepted_on <= end:
            if project.opened_on is None or project.opened_on > end:
                evidence_flags.append(f"{EVIDENCE_ACCEPTED_NOT_OPEN}:{facility_id}")
        if project.opened_on is not None and project.opened_on <= end and obs is None:
            evidence_flags.append(f"{EVIDENCE_OPEN_NOT_REPORTED}:{facility_id}")

    def _compute_values(self, numerator_concept, denominator_concept, cells, scope):
        num_kind = concepts.spec(numerator_concept).value_kind
        den_kind = concepts.spec(denominator_concept).value_kind
        observed_cells = [c for c in cells if c.status == "observed"]

        if num_kind == "binary_count":
            numerator = sum(1 for c in observed_cells if c.value and c.value > 0)
        elif num_kind == "sum":
            numerator = sum(c.value for c in observed_cells)
        else:
            numerator = None

        if den_kind == "scope_count":
            denominator = float(len(scope))
        elif den_kind == "coverage_count":
            denominator = float(len(observed_cells))
        else:
            denominator = None

        if numerator is None or denominator is None or denominator == 0:
            value = None
        elif num_kind == "binary_count":
            value = numerator / denominator * 100.0
        else:
            value = numerator
        return (
            float(numerator) if numerator is not None else None,
            denominator,
            value,
        )

    @staticmethod
    def _fmt(aggregate: Aggregate) -> str:
        if aggregate.unit == "percent":
            return f"{aggregate.value:.1f}%"
        return f"{aggregate.value:g}"
