---
name: gold-procedures
description: Use for silver-to-gold stored procedures, activities or replay planning.
---
# Gold procedures
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Only draft explicit `deploy` or `run` previews with `--plan --request`.
Do not select procedure calls, parameter values or replay order.
## Inputs
Explicit draft request and future user-confirmed replay plan.
## Outputs
Preview plan; no deployed or executed procedures.
## Scripts used
`scripts\s2f.ps1 deploy|run --plan --request` with session prefix.
## Approvals required
Every future deployment or replay requires native plan approval.
## Failure handling
Stop before execution; never infer replay intent from chat.
