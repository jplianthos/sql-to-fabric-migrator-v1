---
name: tsql-assessment
description: Use for SQL compatibility assessment, migration findings or conversion-rule scans.
---
# Assessment
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
State that the SQL130 assessment engine is not implemented.
Do not infer compatibility from keywords or inspect the blind estate.
## Inputs
Future approved module inventory and verified rules.
## Outputs
Blocked status, not a compatibility verdict.
## Scripts used
`scripts\s2f.ps1 scan` is blocked.
## Approvals required
Future module reads require native Read approval.
## Failure handling
Keep unknown support status explicit; do not fabricate findings.
