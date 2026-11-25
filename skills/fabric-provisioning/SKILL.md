---
name: fabric-provisioning
description: Use for capacity, workspace, Lakehouse or Warehouse provisioning plans.
---
# Provisioning
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Require explicit native-approved A5=azure-cli, A6 tenant, B1 capacity, B2=create
with user-chosen new workspace names, and B7=live-with-approval.
Draft `provision --plan --request <file>` with `kind: fabric-provisioning`;
see the bundled provisioning plan schema for exact effects. Show JSON/Markdown,
plan hash, identity, capacity, method/path/body and verification GETs first.
Have the operator save the exact request in the project when needed:
inline --answers supports movement, not provisioning. Never bypass file/role
boundaries to create it.
Only selected migration-lead may request `execute <plan-id>` and native approval.
Never supply an internal grant. No capacity, name or configuration is inferred.
Create the workspace first; use its verified returned ID for separate item plans.
Lakehouse creation requires explicit B5; Warehouse creation requires explicit B4.
Existing items cannot be adopted. General updates/deletes and SQL are not
provisioning operations; use bronze-ingestion for the separate supported
upload/load route after verified creation.
## Inputs
User-authored approved configuration and explicit request identity/subscription.
## Outputs
Exact plan, then native-approved creation receipt. 202 is pending, not ownership.
Use bounded GET-only `provision-status <id>` after nextPollAt until terminal;
do not repeat execute. Created IDs enter private authority only after post verification.
## Scripts used
`scripts\s2f.ps1 provision --plan --request` with session prefix.
## Approvals required
Reads ask unless opted in for this session; every create needs native approval.
Configuration approval and read opt-in are not Fabric Write approval.
## Failure handling
Stop on missing configuration; never fall back to MCP or HTTP writes.
Stop on failed/unknown creation or failed post verification. Retained exclusive
attempt evidence prohibits reissuing the same create under a new plan/session.
No automatic delete, rollback, adoption or permission/account change.
