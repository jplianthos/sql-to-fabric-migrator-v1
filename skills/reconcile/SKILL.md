---
name: reconcile
description: Use for source-target validation, row counts, normalized profiles or row hash comparisons.
---
# Reconcile
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Use [the bronze movement contract](../../references/bronze-movement.md).
Require the exact hook-recorded loaded plan and explicit F1/F2/exact F3/bronze
F4 choices. Run reconcile --plan-id <loaded-plan-id>. This verifies the immutable
Parquet manifest, rereads the selected source slice and queries the actual
Lakehouse SQL analytics endpoint using the planned user's SQL-audience token.
Report mismatch, source drift, unavailable endpoint and permission failures
plainly. Do not refresh metadata, change permissions or rerun a load to fix a read.
Counts-only is not value equivalence. Hashes compare normalized row multisets,
not target collation/order; NULL/blank and case remain distinct.
Never infer success from plans, load status, matching counts alone or fake rows.
Silver/gold validation remains unavailable.
## Inputs
Exact loaded plan, immutable verified artifact, approved comparison choices,
confirmed source session and actual source/target read permission.
## Outputs
Private-backed reconciled/mismatch receipt with explicit checks, source drift,
target differences, value-hash assurance and nontransactional consistency.
## Scripts used
Session prefix plus reconcile --plan-id <id>.
## Approvals required
Native Read prompt unless the user explicitly enabled this session's Read allowance.
## Failure handling
Stop on missing configuration, drift, access, unsupported types or SQL-endpoint
lag. Never fabricate a pass, execute procedures or write to refresh metadata.
