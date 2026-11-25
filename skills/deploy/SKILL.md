---
name: deploy
description: Use when reviewing deployment plans or requesting an approved Fabric execution.
---
# Deploy
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Draft and inspect explicit `deploy --plan --request` previews.
Only migration-lead may request `execute`; live execution remains disabled.
## Inputs
Plan ID, hash, future approved configuration and ownership ledger.
## Outputs
Preview and audit records only; no Fabric side effects.
## Scripts used
`scripts\s2f.ps1 deploy --plan --request`; blocked `execute`.
## Approvals required
Native prompt bound to plan ID/hash; registration is never authorization.
## Failure handling
Reject tampered, expired, reused, wrong-session or out-of-fence plans.
Never approve on behalf of the user or use an alternate write path.
