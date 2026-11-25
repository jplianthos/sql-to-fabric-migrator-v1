---
name: bronze-ingestion
description: Use for bronze extraction, uploads, Lakehouse landing or Delta load planning.
---
# Bronze ingestion
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Use [the bounded movement contract](../../references/bronze-movement.md).
Interview with ask_user; never require a manually authored JSON/config file.
Show unsupported choices rather than silently selecting the available path.
Require explicit C1/C2/C3 and the supported file-drop/Parquet/load-table-api,
schema-disabled, full-mode, manual-stage and bronze-validation choices.
After native source confirmation, ask the exact database/schema/table, run ID
and timeout. Run table-check for that table only. Explain type/PK blockers.
Ask the extraction row ceiling and expected user identities/subscription;
draft extract --plan --answers, inspect its exact preview, then request execute
through migration-lead's native scoped prompt. Never supply native-grant.
After the successful post hook, ask the exact project-created workspace,
Lakehouse and B6-consistent target name; draft/approve upload separately.
Explain that the preview Load Table API only provides Append/Overwrite, not
atomic create-only. Require explicit acknowledgement, exclusive use of the new
target during the canary, a separate load plan and native Execute approval.
Poll load-status with GETs, respecting nextPollAt; never repeat the load POST.
Hand off to reconcile after loaded. A pending/loaded receipt is not a pass.
Gateway/Mirroring source changes still need DBA checklists, not source writes.
## Inputs
Explicit interview answers, fresh table-check, approved configuration and
hook-recorded predecessor; no inferred tables, order, scope or target IDs.
## Outputs
Exact plans, immutable local Parquet, verified upload/load receipts and durable
private attempt/provenance state. Raw rows stay out of JSON receipts and logs.
## Scripts used
Session prefix plus table-check, extract|upload|load --plan --answers,
execute and load-status. Project-contained --request files remain optional.
## Approvals required
Data movement and code execution always require native approval.
## Failure handling
Stop on unsupported types/configuration, drift, overflow, target existence,
file hash/ETag mismatch or uncertain writes. Retain attempt markers; never
overwrite, adopt, auto-retry, clean up, enable CDC or use an ad-hoc client.
