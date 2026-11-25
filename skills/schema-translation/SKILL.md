---
name: schema-translation
description: Use for translating source DDL, type mappings, keys or target schemas.
---
# Schema translation
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Only draft explicit preview plans with `translate --plan --request`.
The translation engine and live executors are absent; never invent converted DDL.
## Inputs
Explicit draft request; future approved type and collation policies.
## Outputs
Preview JSON/Markdown, not translated or deployed schemas.
## Scripts used
`scripts\s2f.ps1 translate --plan --request` with session prefix.
## Approvals required
Drafting is not approval; future execution always asks natively.
## Failure handling
Stop on missing configuration, choices or verified rules.
