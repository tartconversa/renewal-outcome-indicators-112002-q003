# 合成输入材料

这里的记录均为合成数据，不对应真实住户、项目或城市。每个文件表示一个来源数据表，不包含分析结论；解释记录时应保留来源标识和修订关系。

通用约定：

- 日期字段使用 ISO 8601（`YYYY-MM-DD`），月度期间使用 `YYYY-MM`。
- **空单元格表示未知或未提供，不代表数值零。** 分析流程不得把缺失观测按零值计入分子或分母。
- 所有带时间的数据都必须保留接收时间（`received_at`）或生效时间（`valid_from`），分析按"发布时点"做 as-of 选择，而不是按数据当前的最新状态。

## 文件清单

- `indicator-definitions.csv`：指标定义（带生效期，可多版本）。字段为
  `indicator_id, version, unit, numerator, denominator, valid_from`。
  同一 `indicator_id` 的新版本只在 `valid_from` 之后生效，**不得回写旧版期间的结论**。
  `numerator` / `denominator` 是概念名（如 `open_facilities`、`operating_facilities`、
  `planned_facilities`、`monthly_visits`、`facilities_with_complete_usage`），
  由分析引擎中的概念注册表解释；不同版本若概念不同，即构成口径断裂。
- `boundary.csv`：片区边界版本。字段为
  `version, district, facility_id, valid_from, note`。
  一个设施在不同边界版本中可属于不同片区或因合并退出（如 `note=merged into F-1`）。
  汇总分母随边界版本变化，跨期比较必须注明边界版本是否一致。
- `projects.csv`：项目与里程碑。字段为
  `project_id, district, facility_id, project_type, accepted_on, opened_on`。
  `accepted_on` 是工程验收日期，`opened_on` 是设施实际开始服务日期；两者之间存在时间差，
  **"已建成/已验收"不等于"已运营"**。`opened_on` 为空表示尚未开始服务。
- `observations.csv`：设施级指标观测。字段为
  `period, indicator_id, definition_version, facility_id, value, received_at, quality`。
  `definition_version` 记录该观测按哪一版定义填报；`quality` 取值包括
  `complete`（完整）、`pending`（已报但未成熟/未确认，值通常为空）、
  `corrected`（对历史期的迟到更正）。迟到更正以新行追加，不删除旧行；
  已发布报告按其发布时点冻结的快照复现。
- `targets.csv`：目标值（按指标版本、片区、期间下达）。字段为
  `target_id, indicator_id, definition_version, district, period, target_value`。
  百分比类指标的目标值与观测值同单位（如 50 表示 50%）。
- `investments.csv`：项目投入台账。字段为
  `project_id, period, amount_cny, received_at`。金额单位为人民币元。

## 合成数据中的典型情景

- I-01 从 v1（`open_facilities` 口径，2026-01 起）切换到 v2（`operating_facilities` 口径，2026-07 起）。
- F-2 所属片区在 B1→B2 间由 North 调整到 South；F-3 在 B2 中并入 F-1，分母发生变化。
- P-2/F-2 已验收但未开放（`opened_on` 为空），其观测为 `pending` 且值为空。
- F-1 的 2026-07 I-02 观测存在迟到更正（120 → 135）。
- F-1 于 2026-07 才开放，2026-07/08 的居民结果指标处于未成熟期。
