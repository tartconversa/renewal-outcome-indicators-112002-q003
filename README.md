# Renewal Outcome Indicators

城市更新跨项目成效分析：把口径不一的项目上报数字，转化为可比较、可追溯、可复现的成效结论。
仅使用 Python 标准库，需要 Python 3.12+。

## 解决的问题

- 不同项目按开工楼栋 / 验收面积 / 建成设施等不同口径报进度，完成率不可直接比较；
- 楼栋合并、片区调整会改变分母；指标定义修订不能回写旧结论；
- 验收与实际运营存在时间差，"建成"被误读为"已服务"；
- 迟到更正会改变历史期数据，但已发布报告必须可复现；
- 缺失观测被默认填零、未成熟项目与成熟项目直接比较，会夸大或低估成效。

处理规则见 [`docs/methodology.md`](docs/methodology.md)，合成数据情景见
[`docs/input-contract.md`](docs/input-contract.md)。

## 运行测试

```bash
python -I -m unittest discover -s tests -v
```

## 命令行用法

通过仓库根的启动脚本运行（兼容 `python -I` 隔离模式，默认读取仓库内 `data/`）：

```bash
# 生成一期发布物（Markdown 报告 + 带血缘的 JSON）
python -I run_analytics.py publish \
  --as-of 2026-09-30 --label "2026-09月报" \
  --periods 2026-05,2026-06,2026-07,2026-08

# 查看单个汇总值的完整血缘（定义/边界版本、设施单元格、来源行号）
python -I run_analytics.py aggregate \
  --as-of 2026-09-30 --indicator I-01 --period 2026-08 --district South

# 目标四态判定：已达目标 / 未达目标 / 尚不可判断 / 数据口径不兼容
python -I run_analytics.py targets --as-of 2026-09-30

# 投入—交付—居民结果链
python -I run_analytics.py chain --as-of 2026-09-30
```

输出默认写入 `publications/`（该目录不纳入版本管理）。

## 代码结构

```
src/renewal_analytics/
  domain.py       领域记录与输出类型（全部带来源坐标 SourceRef）
  io_utils.py     CSV/日期/期间/SHA-256 工具
  stores.py       输入装载与 as-of 版本解析
  concepts.py     分子分母概念注册表、成熟度、回溯重算可行性
  engine.py       汇总、目标四态、趋势可比性、迟到更正、投入—交付—结果链
  publication.py  发布清单、血缘 JSON、中文 Markdown 报告
  cli.py          命令行入口
tests/            夹具契约 + 引擎规则 + 回溯重算测试
```

## 关键规则摘要

1. 空单元格与 pending 一律按缺失处理，**从不按零计入分子**；显式上报的 0 是已观测值。
2. 定义按 `valid_from` 生效、观测按 `received_at` 可见，新版本/迟到数据都不回写历史。
3. 分母取发布时点生效边界版本；合并与片区调整通过边界版本表达。
4. 验收与开放分别计数；居民结果指标有 2 个月成熟期，未成熟不参与红绿判定。
5. 目标判定优先级：口径不兼容 → 尚不可判断 → 已达/未达，每个结论附理由。
6. 回溯重算需要逐设施可验证证据，证据不足只能保留旧口径。
7. 每次发布冻结全部输入文件的 SHA-256，同 as_of 重跑结果字节级一致。
