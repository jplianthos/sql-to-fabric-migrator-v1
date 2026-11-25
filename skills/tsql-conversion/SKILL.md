---
name: tsql-conversion
description: Use for T-SQL rewrites, stored procedure conversion or conversion diffs.
---
# Conversion
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Only draft explicit `convert --plan --request` previews.
No conversion engine exists yet. Future edits belong only in the run's converted folder.
## Inputs
Explicit draft request and future approved conversion policies.
## Outputs
Preview plan; no generated or executed T-SQL.
## Scripts used
`scripts\s2f.ps1 convert --plan --request` with session prefix.
## Approvals required
Native execution approval cannot be replaced by reviewing a diff.
## Failure handling
Stop on unsupported conversions; never run rewritten code anywhere.
