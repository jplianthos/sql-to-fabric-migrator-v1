---
name: wave-planner
description: Use for migration waves, dependency ordering or replay order planning.
---
# Wave planning
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Explain that wave generation is not implemented; the user owns all scope and order.
Do not choose databases, objects or replay parameters.
## Inputs
Future inventory and user-confirmed scope.
## Outputs
Blocked status, not a saved execution order.
## Scripts used
`scripts\s2f.ps1 plan-waves` is blocked.
## Approvals required
Future choices use `ask_user`; execution needs separate native approvals.
## Failure handling
Stop on missing dependencies or user decisions; no silent defaults.
