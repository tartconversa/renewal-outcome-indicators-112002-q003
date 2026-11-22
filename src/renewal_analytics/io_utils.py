"""CSV 读取、日期/期间解析与输入指纹工具（仅标准库）。"""

from __future__ import annotations

import csv
import hashlib
from datetime import date
from pathlib import Path
from typing import Iterator, Optional


def parse_iso_date(text: Optional[str]) -> Optional[date]:
    """解析 ISO 日期；空字符串/None 返回 None（表示未知，而非零或纪元日）。"""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    return date.fromisoformat(text)


def parse_optional_float(text: Optional[str]) -> Optional[float]:
    """空单元格返回 None——缺失值绝不被当作 0。"""
    if text is None:
        return None
    text = text.strip()
    if not text:
        return None
    return float(text)


def parse_period(text: str) -> tuple[int, int]:
    """把 YYYY-MM 解析为 (年, 月)。"""
    year_str, month_str = text.strip().split("-")
    year, month = int(year_str), int(month_str)
    if not 1 <= month <= 12:
        raise ValueError(f"非法月份: {text!r}")
    return year, month


def period_key(period: str) -> tuple[int, int]:
    return parse_period(period)


def period_to_date(period: str) -> date:
    """期间对应的月初日期，用于 as-of 比较。"""
    year, month = parse_period(period)
    return date(year, month, 1)


def shift_period(period: str, months: int) -> str:
    year, month = parse_period(period)
    index = year * 12 + (month - 1) + months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def read_csv_rows(path: Path) -> Iterator[tuple[int, dict[str, str]]]:
    """逐行产出 (行号, 行字典)；行号从 2 起（1 为表头），与电子表格行号一致。"""
    with path.open(encoding="utf-8", newline="") as stream:
        reader = csv.DictReader(stream)
        for line_number, row in enumerate(reader, start=2):
            yield line_number, row


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()
