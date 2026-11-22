"""核心领域记录与基础类型。

所有数据行都带来源标识（SourceRef），使每个汇总值都能回溯到
具体的来源文件、行号与记录主键。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Optional


@dataclass(frozen=True)
class SourceRef:
    """一条输入记录的来源坐标。"""

    file: str
    line_number: int
    key: str


@dataclass(frozen=True)
class IndicatorDefinition:
    indicator_id: str
    version: str
    unit: str
    numerator: str
    denominator: str
    valid_from: date
    source: SourceRef

    @property
    def id_version(self) -> str:
        return f"{self.indicator_id}@{self.version}"


@dataclass(frozen=True)
class BoundaryAssignment:
    version: str
    district: str
    facility_id: str
    valid_from: date
    note: str
    source: SourceRef


@dataclass(frozen=True)
class Project:
    project_id: str
    district: str
    facility_id: str
    project_type: str
    accepted_on: Optional[date]
    opened_on: Optional[date]
    source: SourceRef


@dataclass(frozen=True)
class Observation:
    period: str  # YYYY-MM
    indicator_id: str
    definition_version: str
    facility_id: str
    value: Optional[float]
    received_at: date
    quality: str
    source: SourceRef

    @property
    def id_version(self) -> str:
        return f"{self.indicator_id}@{self.definition_version}"


@dataclass(frozen=True)
class Target:
    target_id: str
    indicator_id: str
    definition_version: str
    district: str
    period: str
    target_value: float
    source: SourceRef

    @property
    def id_version(self) -> str:
        return f"{self.indicator_id}@{self.definition_version}"


@dataclass(frozen=True)
class Investment:
    project_id: str
    period: str
    amount_cny: float
    received_at: date
    source: SourceRef


# ---------------------------------------------------------------------------
# 输出类型
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class FacilityCell:
    """汇总中一个设施的取值状态——缺失与零必须可区分。"""

    facility_id: str
    status: str  # observed | pending | missing
    value: Optional[float]
    quality: Optional[str]
    source: Optional[SourceRef] = None
    reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class Aggregate:
    period: str
    indicator_id: str
    definition_version: str
    district: str  # 或 "ALL"
    unit: str
    numerator_concept: str
    denominator_concept: str
    value: Optional[float]
    numerator_value: Optional[float]
    denominator_value: Optional[float]
    scope_facilities: tuple[str, ...]
    boundary_version: str
    observed_facilities: tuple[str, ...]
    pending_facilities: tuple[str, ...]
    missing_facilities: tuple[str, ...]
    immature_facilities: tuple[str, ...]
    coverage_ratio: Optional[float]
    maturity: str  # mature | contains_immature | not_applicable
    evidence_flags: tuple[str, ...]
    sources: tuple[SourceRef, ...]
    facility_cells: tuple[FacilityCell, ...]


@dataclass(frozen=True)
class TargetEvaluation:
    target: Target
    status: str  # MET | BELOW_TARGET | NOT_JUDGEABLE | INCOMPATIBLE_CALIBER
    actual: Optional[float]
    reasons: tuple[str, ...]
    aggregate: Optional[Aggregate]


@dataclass(frozen=True)
class TrendLink:
    indicator_id: str
    district: str
    from_period: str
    to_period: str
    comparability: str  # comparable | CALIBER_BREAK | SCOPE_CHANGE | NO_BASELINE
    change: Optional[float]
    reasons: tuple[str, ...]


@dataclass(frozen=True)
class Correction:
    observation: Observation
    superseded_value: Optional[float]
    superseded_source: SourceRef
    changed_period: str


@dataclass(frozen=True)
class ChainRow:
    """投入—交付—居民结果的链式视图中的一行。"""

    period: str
    district: str
    project_type: str
    investment_cny: Optional[float]
    accepted_facilities: int
    open_facilities: int
    operating_facilities: int
    mature_facilities: int
    facility_count: int
    sources: tuple[SourceRef, ...] = field(default_factory=tuple)
