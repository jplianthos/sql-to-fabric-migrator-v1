---
name: silver-load
description: Use for bronze-to-silver notebook, pipeline or Warehouse load planning.
---
# Silver load
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Only draft explicit `generate` or `load` previews with `--plan --request`.
No notebook, pipeline or data load implementation exists yet.
## Inputs
Explicit draft request and future approved D5 choices.
## Outputs
Preview plan; no generated or executed workload.
## Scripts used
`scripts\s2f.ps1 generate|load --plan --request` with session prefix.
## Approvals required
Future code execution always asks natively.
## Failure handling
Stop on unset load strategy or workspace fence.
