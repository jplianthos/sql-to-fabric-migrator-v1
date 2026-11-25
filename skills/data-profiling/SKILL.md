---
name: data-profiling
description: Use for table profiles, row counts, UTF-8 sizing or data sample requests.
---
# Profiling
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Explain that profiling is blocked until guarded reads and user-selected data scope exist.
Never choose sample sizes, predicates or tables.
## Inputs
Future approved scope and profiling choices.
## Outputs
Blocked status; no source values are read or persisted.
## Scripts used
`scripts\s2f.ps1 profile` is blocked.
## Approvals required
Future source reads require native approval.
## Failure handling
Stop; never substitute unguarded SQL or fabricated measurements.
