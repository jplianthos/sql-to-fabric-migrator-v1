---
name: sql2016-source
description: Use for SQL Server 2016 source compatibility, connection guidance or read-only access readiness.
---
# SQL Server 2016 source
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Require explicit native-approved A1-A4: local `lpc:` server, Windows integrated
authentication, mandatory encryption with explicit certificate-trust choice,
and SQL2016/auto-detect engine profile. Do not infer these from test variables.
Ask the expected Windows user, run ID and 1-60 second query timeout.
Run `source-check --run-id <id> --query-timeout <seconds> --user '<expected-user>'`.
Show the observed user/original login, engine/edition and sysadmin status.
If exactSql2016Host is false, explain that the newer host is not SQL2016 evidence.
Request `source-confirm <check-id>` once through its native prompt. Neither chat
nor the check receipt confirms identity. Then hand off to estate-discovery.
Never enumerate databases, read modules or reuse development probes. Data reads
are available only through the separately planned/approved bounded bronze route;
hand off to bronze-ingestion after the user explicitly selects that route.
## Inputs
Explicit user connection/identity/timeout choices; no inferred database scope.
## Outputs
Verified identity observation and separately native-confirmed session lease.
## Scripts used
Session prefix plus `source-check` and `source-confirm`; sole SQL transport is
`src\s2f\source.py`, behind the pinned product SQL130 guard.
## Approvals required
Probe uses native Read approval unless explicitly allowed for this session.
Confirmation always asks natively, even with a read allowance.
## Failure handling
Stop on missing guard/driver, configuration, identity/encryption mismatch or
expired session. Produce DBA guidance rather than grants or source changes;
never dot-source an estate helper or fall back to another client/protocol.
