# Input material

All records are synthetic and refer to no real household, project, or city. A file is a source extract, not a precomputed answer. Preserve source identifiers and revisions when interpreting it.

- `indicator-definitions.csv`: CSV; columns `indicator_id, version, unit, numerator, denominator, valid_from`. Dates use ISO 8601 where present; blank cells mean unknown or not supplied, not numeric zero.
- `projects.csv`: CSV; columns `project_id, district, facility_id, accepted_on, opened_on`. Dates use ISO 8601 where present; blank cells mean unknown or not supplied, not numeric zero.
- `observations.csv`: CSV; columns `period, indicator_id, definition_version, facility_id, value, received_at, quality`. Dates use ISO 8601 where present; blank cells mean unknown or not supplied, not numeric zero.
- `boundary.csv`: CSV; columns `version, district, facility_id, valid_from`. Dates use ISO 8601 where present; blank cells mean unknown or not supplied, not numeric zero.
