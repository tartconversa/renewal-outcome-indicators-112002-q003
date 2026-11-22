"""命令行入口：发布报告、查询单个汇总血缘、评估目标。"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

from dataclasses import asdict

from .engine import AnalyticsEngine
from .io_utils import parse_iso_date
from .publication import build_json_payload, lineage, write_publication
from .stores import DataStore


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="renewal-analytics",
        description="城市更新跨项目成效分析（版本化口径、成熟度、可复现发布）",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="输入 CSV 目录（默认：本包上两级仓库根下的 data/）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    publish = sub.add_parser("publish", help="生成一期 Markdown + JSON 发布物")
    publish.add_argument("--as-of", required=True, help="发布时点 YYYY-MM-DD")
    publish.add_argument("--label", required=True, help="发布名称（如 2026-08 月报）")
    publish.add_argument("--periods", required=True,
                         help="纳入发布的期间，逗号分隔，如 2026-06,2026-07,2026-08")
    publish.add_argument(
        "--out-dir", default=None,
        help="输出目录（默认：仓库根下 publications/）",
    )

    aggregate = sub.add_parser("aggregate", help="查询单个汇总并打印血缘")
    aggregate.add_argument("--as-of", required=True)
    aggregate.add_argument("--indicator", required=True)
    aggregate.add_argument("--period", required=True)
    aggregate.add_argument("--district", required=True)

    targets = sub.add_parser("targets", help="按发布时点评估全部目标（四态）")
    targets.add_argument("--as-of", required=True)

    chain = sub.add_parser("chain", help="投入—交付—居民结果链（按期间/片区/工程类型）")
    chain.add_argument("--as-of", required=True)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    if args.data_dir is None:
        # src/renewal_analytics/cli.py 的上两级即仓库根
        repo_root = Path(__file__).resolve().parents[2]
        data_dir = repo_root / "data"
    else:
        data_dir = Path(args.data_dir)
    store = DataStore.load(data_dir)
    engine = AnalyticsEngine(store)
    as_of = parse_iso_date(args.as_of)

    if args.command == "publish":
        periods = [p.strip() for p in args.periods.split(",") if p.strip()]
        if args.out_dir is None:
            out_dir = Path(__file__).resolve().parents[2] / "publications"
        else:
            out_dir = Path(args.out_dir)
        md_path, json_path = write_publication(
            store, engine,
            out_dir=out_dir,
            as_of=as_of, label=args.label, periods=periods,
        )
        print(f"已写出 {md_path}")
        print(f"已写出 {json_path}")
        return 0

    if args.command == "aggregate":
        aggregate = engine.aggregate(
            indicator_id=args.indicator,
            period=args.period,
            district=args.district,
            as_of=as_of,
        )
        if aggregate is None:
            print("无匹配汇总（可能是期间内无生效定义或边界范围为空）", file=sys.stderr)
            return 2
        print(json.dumps(lineage(aggregate), ensure_ascii=False, indent=2))
        return 0

    if args.command == "targets":
        payload = build_json_payload(
            store, engine, as_of=as_of, label=args.as_of, periods=[]
        )
        print(json.dumps(payload["target_evaluations"], ensure_ascii=False, indent=2))
        return 0

    if args.command == "chain":
        rows = engine.chain_view(as_of=as_of)
        print(json.dumps([asdict(r) for r in rows], ensure_ascii=False, indent=2,
                         default=str))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
