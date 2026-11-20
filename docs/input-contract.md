# 合成输入材料

这里的记录均为合成数据，不对应真实住户、项目或城市。每个文件表示一个来源数据表，不包含分析结论；解释记录时应保留来源标识和修订关系。

- `indicator-definitions.csv`：CSV 表格，字段为 `indicator_id, version, unit, numerator, denominator, valid_from`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。
- `projects.csv`：CSV 表格，字段为 `project_id, district, facility_id, accepted_on, opened_on`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。
- `observations.csv`：CSV 表格，字段为 `period, indicator_id, definition_version, facility_id, value, received_at, quality`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。
- `boundary.csv`：CSV 表格，字段为 `version, district, facility_id, valid_from`。日期字段使用 ISO 8601 格式；空单元格表示未知或未提供，不代表数值零。
