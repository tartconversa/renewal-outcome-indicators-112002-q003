"""指标分子/分母概念注册表。

定义文件里的 numerator/denominator 是概念名而不是写死的计算逻辑，
引擎通过本表解释每个概念：取值族、是否受成熟期约束、证据来自观测还是里程碑、
以及在口径切换时能否用底层证据回溯重算。
"""

from __future__ import annotations

from dataclasses import dataclass

from .domain import Project
from .io_utils import period_to_date, shift_period
from .stores import DataStore

# 设施开放后，居民结果类指标需要多少个月才视为成熟。
MATURITY_LAG_MONTHS = 2


@dataclass(frozen=True)
class ConceptSpec:
    name: str
    role: str  # numerator | denominator
    # binary_count：0/1 设施计数；sum：观测值求和；coverage_count：有完整观测的设施数；
    # scope_count：边界范围内的设施总数
    value_kind: str
    maturity_gated: bool
    evidence: str  # milestone | observation | scope


CONCEPTS: dict[str, ConceptSpec] = {
    "planned_facilities": ConceptSpec(
        "planned_facilities", "denominator", "scope_count", False, "scope"
    ),
    "open_facilities": ConceptSpec(
        "open_facilities", "numerator", "binary_count", False, "milestone"
    ),
    "operating_facilities": ConceptSpec(
        "operating_facilities", "numerator", "binary_count", True, "observation"
    ),
    "monthly_visits": ConceptSpec(
        "monthly_visits", "numerator", "sum", True, "observation"
    ),
    "facilities_with_complete_usage": ConceptSpec(
        "facilities_with_complete_usage", "denominator", "coverage_count", True,
        "observation"
    ),
}


def spec(name: str) -> ConceptSpec:
    if name not in CONCEPTS:
        raise KeyError(f"未知指标概念: {name!r}，请在概念注册表中登记后再使用")
    return CONCEPTS[name]


def is_rate_indicator(numerator_concept: str) -> bool:
    """分子是 0/1 设施计数、分母是范围计数的指标按百分比呈现。"""
    return spec(numerator_concept).value_kind == "binary_count"


def is_maturity_gated(numerator_concept: str) -> bool:
    return spec(numerator_concept).maturity_gated


def facility_is_mature(project: Project | None, period: str) -> bool:
    """居民结果指标的设施成熟判定：开放月 + 滞后期之后才成熟。

    没有开放记录的设施谈不上成熟或未成熟（调用方按 missing 处理）。
    """
    if project is None or project.opened_on is None:
        return False
    opened_period = f"{project.opened_on.year:04d}-{project.opened_on.month:02d}"
    return period >= shift_period(opened_period, MATURITY_LAG_MONTHS)


def period_end(period: str):
    from datetime import date, timedelta

    first_of_next = period_to_date(shift_period(period, 1))
    return first_of_next - timedelta(days=1)


# ---------------------------------------------------------------------------
# 回溯重算可行性
# ---------------------------------------------------------------------------

def backcast_feasible(
    store: DataStore,
    *,
    numerator_concept: str,
    denominator_concept: str,
    period: str,
    scope_facilities: list[str],
    as_of,
) -> tuple[bool, tuple[str, ...]]:
    """判断旧期间能否按新口径回溯重算。

    原则：只有当分子分母都能从可验证的底层证据（里程碑或完整原始观测）
    重建时才允许回算；任一环节依赖未知值或仅有汇总值，则只能保留旧口径。
    """
    reasons: list[str] = []
    end = period_end(period)

    if numerator_concept == "open_facilities":
        for facility_id in scope_facilities:
            project = store.project_by_facility(facility_id)
            if project is None or project.opened_on is None:
                reasons.append(
                    f"设施 {facility_id} 缺少开放里程碑，无法重建 open_facilities"
                )
    elif numerator_concept == "operating_facilities":
        for facility_id in scope_facilities:
            project = store.project_by_facility(facility_id)
            if project is None:
                reasons.append(f"设施 {facility_id} 缺少项目记录")
                continue
            if project.opened_on is None or project.opened_on > end:
                if project.opened_on is None:
                    reasons.append(
                        f"设施 {facility_id} 开放日期未知，无法判定 {period} 是否运营"
                    )
                continue  # 开放日晚于期间末日：可确定当时未运营
            usage = [
                o for o in store.observations_visible_at(
                    period=period, indicator_id="I-02",
                    facility_id=facility_id, as_of=as_of,
                )
                if o.quality == "complete" and o.value is not None
            ]
            if not usage:
                reasons.append(
                    f"设施 {facility_id} 在 {period} 缺少完整使用量证据，"
                    "无法重建 operating_facilities"
                )
    elif numerator_concept == "monthly_visits":
        reasons.append("monthly_visits 只有设施级汇总观测，无底层记录可按新口径重算")
    else:
        reasons.append(f"概念 {numerator_concept} 未登记回溯规则")

    if denominator_concept == "facilities_with_complete_usage":
        for facility_id in scope_facilities:
            complete = [
                o for o in store.observations_visible_at(
                    period=period, facility_id=facility_id, as_of=as_of,
                )
                if o.quality == "complete" and o.value is not None
            ]
            if not complete:
                reasons.append(
                    f"设施 {facility_id} 在 {period} 无完整观测，"
                    "无法重建 facilities_with_complete_usage 分母"
                )
    # scope_count 分母（planned_facilities）可由边界版本直接重建，无需额外证据。

    return (not reasons), tuple(reasons)
