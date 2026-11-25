---
name: estate-discovery
description: Use when the user requests source inventory, dependencies or discovery.
---
# Discovery
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Ask the user for 1-16 exact user database names (C1); do not enumerate or choose
them. Save those explicit answers through the configuration preview/native apply
path. Then use sql2016-source to obtain a fresh native-confirmed source identity.
Changed configuration requires a fresh source check/confirmation.
This checkpoint cannot apply C2 schema/list/exclusion filters; stop rather than
broaden them or change the user's choice. C2 may remain unset for catalog-only
discovery before object selection; this does not select migration objects.
Ask an explicit run ID and 1-60 second timeout; run
`discover --run-id <id> --query-timeout <seconds>` using the session prefix.
Review inventory.json and source-queries.jsonl in that run's output.
Report catalog-only objects/columns, visibility/readiness and any limitations.
Do not call this full discovery or migration: no module definitions, dependencies,
keys, row data, profiling, extraction, SSIS package/job inspection or Fabric calls.
Missing metadata visibility is a blocker; never grant permissions automatically.
## Inputs
Native-approved A1-A4/C1, confirmed source identity, explicit run/timeout.
## Outputs
Bounded schema-validated inventory and flushed query audit, or explicit failure.
Metadata is nontransactional and permission-filtered, not a completeness proof.
## Scripts used
Session prefix plus `discover`; fixed catalog query library through source.py.
## Approvals required
Catalog reads ask natively unless explicitly allowed for this session;
source confirmation is always separate and mandatory.
## Failure handling
Stop on missing scope/confirmation, permission/row limits, drift or malformed
results. Do not fall back, retry automatically or clear private run-lock markers.
