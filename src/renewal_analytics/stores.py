"""输入数据的装载与 as-of 版本解析。

设计要点：
- 所有记录保留 SourceRef，血缘不依赖行顺序之外的隐式信息。
- 定义/边界的版本选择只看 valid_from 与查询时点，不回写历史。
- 观测按 (期间, 指标版本, 设施) 维护版本链，支持发布时点快照与迟到更正识别。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional

from .domain import (
    BoundaryAssignment,
    IndicatorDefinition,
    Investment,
    Observation,
    Project,
    SourceRef,
    Target,
)
from .io_utils import file_sha256, parse_iso_date, parse_optional_float, read_csv_rows


@dataclass(frozen=True)
class InputFingerprint:
    file: str
    sha256: str
    record_count: int


class DataStore:
    def __init__(self, data_dir: Path):
        self.data_dir = Path(data_dir)
        self.definitions: list[IndicatorDefinition] = []
        self.boundaries: list[BoundaryAssignment] = []
        self.projects: list[Project] = []
        self.observations: list[Observation] = []
        self.targets: list[Target] = []
        self.investments: list[Investment] = []
        self.fingerprints: list[InputFingerprint] = []

    # -- 装载 -------------------------------------------------------------

    @classmethod
    def load(cls, data_dir: Path | str) -> "DataStore":
        store = cls(Path(data_dir))
        store._load_definitions()
        store._load_boundaries()
        store._load_projects()
        store._load_observations()
        store._load_targets()
        store._load_investments()
        return store

    def _fingerprint(self, filename: str, record_count: int) -> None:
        path = self.data_dir / filename
        self.fingerprints.append(
            InputFingerprint(filename, file_sha256(path), record_count)
        )

    def _load_definitions(self) -> None:
        filename = "indicator-definitions.csv"
        count = 0
        for line, row in read_csv_rows(self.data_dir / filename):
            count += 1
            self.definitions.append(
                IndicatorDefinition(
                    indicator_id=row["indicator_id"].strip(),
                    version=row["version"].strip(),
                    unit=row["unit"].strip(),
                    numerator=row["numerator"].strip(),
                    denominator=row["denominator"].strip(),
                    valid_from=parse_iso_date(row["valid_from"]),
                    source=SourceRef(filename, line,
                                     f"{row['indicator_id'].strip()}@{row['version'].strip()}"),
                )
            )
        self._fingerprint(filename, count)

    def _load_boundaries(self) -> None:
        filename = "boundary.csv"
        count = 0
        for line, row in read_csv_rows(self.data_dir / filename):
            count += 1
            self.boundaries.append(
                BoundaryAssignment(
                    version=row["version"].strip(),
                    district=row["district"].strip(),
                    facility_id=row["facility_id"].strip(),
                    valid_from=parse_iso_date(row["valid_from"]),
                    note=row.get("note", "").strip(),
                    source=SourceRef(filename, line,
                                     f"{row['version'].strip()}:{row['facility_id'].strip()}"),
                )
            )
        self._fingerprint(filename, count)

    def _load_projects(self) -> None:
        filename = "projects.csv"
        count = 0
        for line, row in read_csv_rows(self.data_dir / filename):
            count += 1
            self.projects.append(
                Project(
                    project_id=row["project_id"].strip(),
                    district=row["district"].strip(),
                    facility_id=row["facility_id"].strip(),
                    project_type=row["project_type"].strip(),
                    accepted_on=parse_iso_date(row["accepted_on"]),
                    opened_on=parse_iso_date(row["opened_on"]),
                    source=SourceRef(filename, line, row["project_id"].strip()),
                )
            )
        self._fingerprint(filename, count)

    def _load_observations(self) -> None:
        filename = "observations.csv"
        count = 0
        for line, row in read_csv_rows(self.data_dir / filename):
            count += 1
            self.observations.append(
                Observation(
                    period=row["period"].strip(),
                    indicator_id=row["indicator_id"].strip(),
                    definition_version=row["definition_version"].strip(),
                    facility_id=row["facility_id"].strip(),
                    value=parse_optional_float(row["value"]),
                    received_at=parse_iso_date(row["received_at"]),
                    quality=row["quality"].strip(),
                    source=SourceRef(filename, line,
                                     f"{row['period'].strip()}:"
                                     f"{row['indicator_id'].strip()}@"
                                     f"{row['definition_version'].strip()}:"
                                     f"{row['facility_id'].strip()}"),
                )
            )
        self._fingerprint(filename, count)

    def _load_targets(self) -> None:
        filename = "targets.csv"
        count = 0
        for line, row in read_csv_rows(self.data_dir / filename):
            count += 1
            self.targets.append(
                Target(
                    target_id=row["target_id"].strip(),
                    indicator_id=row["indicator_id"].strip(),
                    definition_version=row["definition_version"].strip(),
                    district=row["district"].strip(),
                    period=row["period"].strip(),
                    target_value=parse_optional_float(row["target_value"]),
                    source=SourceRef(filename, line, row["target_id"].strip()),
                )
            )
        self._fingerprint(filename, count)

    def _load_investments(self) -> None:
        filename = "investments.csv"
        count = 0
        for line, row in read_csv_rows(self.data_dir / filename):
            count += 1
            self.investments.append(
                Investment(
                    project_id=row["project_id"].strip(),
                    period=row["period"].strip(),
                    amount_cny=parse_optional_float(row["amount_cny"]),
                    received_at=parse_iso_date(row["received_at"]),
                    source=SourceRef(filename, line,
                                     f"{row['project_id'].strip()}:{row['period'].strip()}"),
                )
            )
        self._fingerprint(filename, count)

    # -- as-of 查询 -------------------------------------------------------

    def definition_as_of(self, indicator_id: str, as_of: date) -> Optional[IndicatorDefinition]:
        """返回 as_of 时点生效的指标定义；无生效版本时返回 None。"""
        candidates = [
            d for d in self.definitions
            if d.indicator_id == indicator_id and d.valid_from <= as_of
        ]
        if not candidates:
            return None
        return max(candidates, key=lambda d: d.valid_from)

    def boundary_version_as_of(self, as_of: date) -> Optional[str]:
        versions = sorted({b.version for b in self.boundaries})
        effective = [
            v for v in versions
            if max(b.valid_from for b in self.boundaries if b.version == v) <= as_of
        ]
        return effective[-1] if effective else None

    def scope_as_of(self, as_of: date) -> dict[str, BoundaryAssignment]:
        """as_of 时点每个设施的片区归属（按当时生效边界版本）。"""
        version = self.boundary_version_as_of(as_of)
        if version is None:
            return {}
        return {b.facility_id: b for b in self.boundaries if b.version == version}

    def scope_for_version(self, boundary_version: str) -> dict[str, BoundaryAssignment]:
        return {
            b.facility_id: b
            for b in self.boundaries
            if b.version == boundary_version
        }

    def project_by_facility(self, facility_id: str) -> Optional[Project]:
        for project in self.projects:
            if project.facility_id == facility_id:
                return project
        return None

    def observations_visible_at(
        self,
        *,
        period: Optional[str] = None,
        indicator_id: Optional[str] = None,
        facility_id: Optional[str] = None,
        as_of: date,
    ) -> list[Observation]:
        """发布时点 as_of 可见的观测（received_at <= as_of）。"""
        result = []
        for obs in self.observations:
            if obs.received_at > as_of:
                continue
            if period is not None and obs.period != period:
                continue
            if indicator_id is not None and obs.indicator_id != indicator_id:
                continue
            if facility_id is not None and obs.facility_id != facility_id:
                continue
            result.append(obs)
        return result
