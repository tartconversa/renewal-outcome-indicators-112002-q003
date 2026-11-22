"""城市更新跨项目成效分析包。

模块划分：
- domain：领域记录与输出类型（全部带来源坐标）
- io_utils：CSV/日期/期间/指纹工具
- stores：输入装载与 as-of 版本解析
- concepts：指标分子分母概念注册表、成熟度与回溯重算规则
- engine：汇总、目标四态判定、趋势可比性、迟到更正、投入—交付—结果链
- publication：发布清单、血缘 JSON、中文 Markdown 报告
"""

from .engine import AnalyticsEngine
from .publication import build_manifest, lineage, render_report, write_publication
from .stores import DataStore

__all__ = [
    "AnalyticsEngine",
    "DataStore",
    "build_manifest",
    "lineage",
    "render_report",
    "write_publication",
]

__version__ = "0.2.0"
