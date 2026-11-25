---
name: migration-report
description: Use for migration status, ownership ledger, audit history or final reporting requests.
---
# Migration reporting
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Inspect local `ledger show` if approved. The complete report generator is absent.
Distinguish drafts, registrations and completed live work; there is no live work in this checkpoint.
## Inputs
Hook-owned local audit and ownership projection.
## Outputs
Local ledger view or blocked report status; no fabricated migration progress.
## Scripts used
`scripts\s2f.ps1 ledger show|export`; `report` is blocked.
## Approvals required
Native Read prompts; do not edit the registry or audit projections.
## Failure handling
State missing audit explicitly; do not infer that missing records mean success.
