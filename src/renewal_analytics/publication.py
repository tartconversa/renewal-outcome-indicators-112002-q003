"""发布清单、血缘追溯与中文报告渲染。

发布物由两部分构成：
- 清单（manifest）：冻结发布时点、输入文件指纹、所用定义/边界版本，
  保证同一时点重复运行得到字节级一致的结果；
- 报告（Markdown）：每个数字都注明测量对象、覆盖范围、数据成熟度、
  与前期比较的限制，以及来源坐标。
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any

from .engine import AnalyticsEngine
from .stores import DataStore


@dataclass(frozen=True)
class PublicationManifest:
    label: str
    as_of: str
    indicator_versions: tuple[tuple[str, str, str], ...]  # (indicator, version, valid_from)
    boundary_versions: tuple[tuple[str, str], ...]        # (version, first_valid_from)
    fingerprints: tuple[tuple[str, str, int], ...]       # (file, sha256, records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "as_of": self.as_of,
            "indicator_versions": [
                {"indicator_id": i, "version": v, "valid_from": f}
                for i, v, f in self.indicator_versions
            ],
            "boundary_versions": [
                {"version": v, "valid_from": f} for v, f in self.boundary_versions
            ],
            "input_fingerprints": [
                {"file": f, "sha256": h, "record_count": n}
                for f, h, n in self.fingerprints
            ],
        }


def build_manifest(store: DataStore, *, as_of: date, label: str) -> PublicationManifest:
    indicator_versions = tuple(sorted(
        (d.indicator_id, d.version, d.valid_from.isoformat())
        for d in store.definitions
    ))
    boundary_versions = tuple(sorted(
        (v, min(b.valid_from for b in store.boundaries if b.version == v).isoformat())
        for v in {b.version for b in store.boundaries}
    ))
    fingerprints = tuple(sorted(
        (f.file, f.sha256, f.record_count) for f in store.fingerprints
    ))
    return PublicationManifest(label, as_of.isoformat(),
                               indicator_versions, boundary_versions, fingerprints)


def lineage(aggregate) -> dict[str, Any]:
    """返回单个汇总值的完整血缘，供逐值追查。"""
    return {
        "period": aggregate.period,
        "indicator_id": aggregate.indicator_id,
        "definition_version": aggregate.definition_version,
        "district": aggregate.district,
        "numerator_concept": aggregate.numerator_concept,
        "denominator_concept": aggregate.denominator_concept,
        "boundary_version": aggregate.boundary_version,
        "scope_facilities": list(aggregate.scope_facilities),
        "observed_facilities": list(aggregate.observed_facilities),
        "pending_facilities": list(aggregate.pending_facilities),
        "missing_facilities": list(aggregate.missing_facilities),
        "immature_facilities": list(aggregate.immature_facilities),
        "coverage_ratio": aggregate.coverage_ratio,
        "maturity": aggregate.maturity,
        "evidence_flags": list(aggregate.evidence_flags),
        "facility_cells": [
            {
                "facility_id": c.facility_id,
                "status": c.status,
                "value": c.value,
                "quality": c.quality,
                "source": (
                    f"{c.source.file}#L{c.source.line_number}({c.source.key})"
                    if c.source else None
                ),
                "reasons": list(c.reasons),
            }
            for c in aggregate.facility_cells
        ],
        "sources": [
            f"{s.file}#L{s.line_number}({s.key})" for s in aggregate.sources
        ],
    }


# ---------------------------------------------------------------------------
# 中文报告
# ---------------------------------------------------------------------------

_STATUS_LABEL = {
    "MET": "已达目标",
    "BELOW_TARGET": "未达目标",
    "NOT_JUDGEABLE": "尚不可判断",
    "INCOMPATIBLE_CALIBER": "数据口径不兼容",
}

_COMPARABILITY_LABEL = {
    "comparable": "可比",
    "CALIBER_BREAK": "口径断裂，变化量不解释",
    "SCOPE_CHANGE": "范围变化，变化量谨慎使用",
    "NO_BASELINE": "无前期基线",
}


def _fmt_value(aggregate) -> str:
    if aggregate.value is None:
        return "不可计算"
    if aggregate.unit == "percent":
        return f"{aggregate.value:.1f}%"
    return f"{aggregate.value:g} {aggregate.unit}"


def render_report(
    store: DataStore,
    engine: AnalyticsEngine,
    *,
    as_of: date,
    label: str,
    periods: list[str],
) -> str:
    manifest = build_manifest(store, as_of=as_of, label=label)
    lines: list[str] = []
    lines.append(f"# 城市更新成效发布报告 · {label}")
    lines.append("")
    lines.append(f"- 发布时点（as_of）：{as_of.isoformat()}")
    lines.append("- 本报告只采用 received_at 不晚于发布时点的数据；"
                 "迟到数据不改变本报告，只进入后续发布并在更正栏说明。")
    lines.append("")

    lines.append("## 一、口径与边界版本（测量对象与覆盖范围）")
    lines.append("")
    lines.append("| 指标 | 定义版本 | 分子概念 | 分母概念 | 生效日期 |")
    lines.append("|---|---|---|---|---|")
    for indicator_id, version, valid_from in manifest.indicator_versions:
        definition = next(
            d for d in store.definitions
            if d.indicator_id == indicator_id and d.version == version
        )
        lines.append(
            f"| {indicator_id} | {version} | {definition.numerator} | "
            f"{definition.denominator} | {valid_from} |"
        )
    lines.append("")
    lines.append("| 边界版本 | 生效日期 |")
    lines.append("|---|---|")
    for version, valid_from in manifest.boundary_versions:
        lines.append(f"| {version} | {valid_from} |")
    lines.append("")

    lines.append("## 二、输入指纹（可复现性）")
    lines.append("")
    lines.append("| 文件 | SHA-256 | 记录数 |")
    lines.append("|---|---|---|")
    for file_name, digest, count in manifest.fingerprints:
        lines.append(f"| {file_name} | `{digest[:16]}…` | {count} |")
    lines.append("")

    lines.append("## 三、指标汇总（含覆盖范围与成熟度）")
    lines.append("")
    for period in periods:
        aggregates = engine.all_aggregates(period=period, as_of=as_of)
        if not aggregates:
            continue
        lines.append(f"### 期间 {period}")
        lines.append("")
        lines.append("| 指标/口径 | 片区 | 结果 | 分子 | 分母(范围) | 完整/待确认/缺失 | 覆盖率 | 成熟度 | 边界 | 提示 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|")
        for agg in aggregates:
            maturity_label = {
                "mature": "成熟",
                "contains_immature": "含未成熟设施",
                "not_applicable": "不适用",
            }[agg.maturity]
            coverage = (
                f"{agg.coverage_ratio * 100:.0f}%"
                if agg.coverage_ratio is not None else "—"
            )
            flags = "；".join(sorted(set(
                flag.split(":", 1)[0] for flag in agg.evidence_flags
            ))) or "—"
            lines.append(
                f"| {agg.indicator_id}@{agg.definition_version} | {agg.district} | "
                f"{_fmt_value(agg)} | {agg.numerator_value if agg.numerator_value is not None else '—'} | "
                f"{agg.denominator_value if agg.denominator_value is not None else '—'} "
                f"({'/'.join(agg.scope_facilities)}) | "
                f"{len(agg.observed_facilities)}/{len(agg.pending_facilities)}/"
                f"{len(agg.missing_facilities)} | {coverage} | {maturity_label} | "
                f"{agg.boundary_version} | {flags} |"
            )
        lines.append("")

    lines.append("## 四、目标达成判定（四态，不以单一红绿掩盖原因）")
    lines.append("")
    lines.append("| 目标 | 期间 | 片区 | 目标口径 | 目标值 | 判定 | 实际 | 依据/限制 |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for evaluation in engine.evaluate_all_targets(as_of=as_of):
        target = evaluation.target
        actual = (
            "—" if evaluation.actual is None
            else (f"{evaluation.actual:.1f}%" if target.indicator_id == "I-01"
                  else f"{evaluation.actual:g}")
        )
        target_value = (
            f"{target.target_value:g}%" if target.indicator_id == "I-01"
            else f"{target.target_value:g}"
        )
        lines.append(
            f"| {target.target_id} | {target.period} | {target.district} | "
            f"{target.indicator_id}@{target.definition_version} | {target_value} | "
            f"{_STATUS_LABEL[evaluation.status]} | {actual} | "
            f"{'；'.join(evaluation.reasons)} |"
        )
    lines.append("")

    lines.append("## 五、跨期趋势与可比性限制")
    lines.append("")
    indicator_ids = sorted({d.indicator_id for d in store.definitions})
    districts = sorted({b.district for b in store.boundaries})
    for indicator_id in indicator_ids:
        for district in districts:
            links = engine.trend_links(
                indicator_id=indicator_id, district=district, as_of=as_of
            )
            real_links = [l for l in links if l.comparability != "NO_BASELINE"]
            if not real_links:
                continue
            lines.append(f"### {indicator_id} · {district}")
            lines.append("")
            lines.append("| 前期 | 本期 | 可比性 | 变化量 | 说明 |")
            lines.append("|---|---|---|---|---|")
            for link in links:
                change = "—" if link.change is None else f"{link.change:+.2f}"
                lines.append(
                    f"| {link.from_period or '—'} | {link.to_period} | "
                    f"{_COMPARABILITY_LABEL[link.comparability]} | {change} | "
                    f"{'；'.join(link.reasons) or '同口径同范围，可直接比较'} |"
                )
            lines.append("")

    lines.append("## 六、迟到更正（历史期被改变，但已发布报告仍按旧快照复现）")
    lines.append("")
    corrections = engine.corrections()
    if corrections:
        lines.append("| 更正接收日 | 期间 | 指标口径 | 设施 | 原值 | 新值 | 原来源 | 新来源 |")
        lines.append("|---|---|---|---|---|---|---|---|")
        for correction in corrections:
            obs = correction.observation
            lines.append(
                f"| {obs.received_at.isoformat()} | {correction.changed_period} | "
                f"{obs.indicator_id}@{obs.definition_version} | {obs.facility_id} | "
                f"{correction.superseded_value if correction.superseded_value is not None else '空'} | "
                f"{obs.value if obs.value is not None else '空'} | "
                f"{correction.superseded_source.file}#L{correction.superseded_source.line_number} | "
                f"{obs.source.file}#L{obs.source.line_number} |"
            )
    else:
        lines.append("本期点之前无更正记录。")
    lines.append("")

    lines.append("## 七、投入—交付—居民结果链")
    lines.append("")
    lines.append("| 期间 | 片区 | 工程类型 | 投入(元) | 已验收 | 已开放 | 有使用证据(运营) | 已成熟 | 范围设施数 |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for row in engine.chain_view(as_of=as_of):
        investment = (
            "—" if row.investment_cny is None
            else f"{row.investment_cny:,.0f}"
        )
        lines.append(
            f"| {row.period} | {row.district} | {row.project_type} | "
            f"{investment} | "
            f"{row.accepted_facilities} | {row.open_facilities} | "
            f"{row.operating_facilities} | {row.mature_facilities} | {row.facility_count} |"
        )
    lines.append("")

    lines.append("## 八、血缘追溯说明")
    lines.append("")
    lines.append("每个汇总值均可通过 JSON 发布物中的 `lineage` 节点追查：")
    lines.append("指标定义版本、边界版本、范围内设施、逐设施观测状态（完整/待确认/缺失）、")
    lines.append("成熟度标记、证据交叉核对结果，以及每条来源的 `文件名#L行号(主键)` 坐标。")
    lines.append("")
    lines.append("## 九、口径处理规则摘要")
    lines.append("")
    lines.append("1. 空单元格与 pending 观测一律按缺失处理，**从不按零计入分子**。")
    lines.append("2. 指标定义按 valid_from 生效，新版本不回写旧期间；跨版本期间标记口径断裂。")
    lines.append("3. 分母取发布时点生效边界版本内的设施；楼栋合并/片区调整通过边界版本体现。")
    lines.append("4. 验收（accepted_on）与开放服务（opened_on）分别计数，建成不等于运营。")
    lines.append("5. 居民结果类指标在设施开放后有 2 个月成熟期，未成熟设施不参与红绿判定。")
    lines.append("6. 目标判定四态优先顺序：口径不兼容 → 尚不可判断 → 已达/未达目标。")
    lines.append("7. 回溯重算仅在分子分母都有可验证底层证据时允许，否则保留旧口径并注明。")
    lines.append("")
    return "\n".join(lines)


def build_json_payload(
    store: DataStore,
    engine: AnalyticsEngine,
    *,
    as_of: date,
    label: str,
    periods: list[str],
) -> dict[str, Any]:
    manifest = build_manifest(store, as_of=as_of, label=label)
    payload: dict[str, Any] = {"manifest": manifest.to_dict(), "periods": {}}
    for period in periods:
        aggregates = engine.all_aggregates(period=period, as_of=as_of)
        payload["periods"][period] = {
            "aggregates": [asdict(a) for a in aggregates],
            "lineage": {
                f"{a.indicator_id}|{a.district}": lineage(a) for a in aggregates
            },
        }
    payload["target_evaluations"] = [
        {
            "target_id": e.target.target_id,
            "status": e.status,
            "status_label": _STATUS_LABEL[e.status],
            "actual": e.actual,
            "reasons": list(e.reasons),
        }
        for e in engine.evaluate_all_targets(as_of=as_of)
    ]
    payload["corrections"] = [
        {
            "changed_period": c.changed_period,
            "indicator": f"{c.observation.indicator_id}@{c.observation.definition_version}",
            "facility_id": c.observation.facility_id,
            "superseded_value": c.superseded_value,
            "new_value": c.observation.value,
            "received_at": c.observation.received_at.isoformat(),
        }
        for c in engine.corrections()
    ]
    payload["chain_view"] = [asdict(r) for r in engine.chain_view(as_of=as_of)]
    return payload


def write_publication(
    store: DataStore,
    engine: AnalyticsEngine,
    *,
    out_dir: Path,
    as_of: date,
    label: str,
    periods: list[str],
) -> tuple[Path, Path]:
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    markdown = render_report(store, engine, as_of=as_of, label=label, periods=periods)
    payload = build_json_payload(store, engine, as_of=as_of, label=label, periods=periods)
    md_path = out_dir / f"report-{as_of.isoformat()}.md"
    json_path = out_dir / f"publication-{as_of.isoformat()}.json"
    md_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    return md_path, json_path
