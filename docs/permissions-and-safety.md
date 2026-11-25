# Permissions, supported scope and safety

## Administrator prerequisites

The customer or administrator supplies privileges. The plugin does not assign
roles, grant consent, create capacity, switch accounts or change subscriptions.

| Surface | Required access |
| --- | --- |
| Source SQL Server 2016 | Windows integrated login, encrypted connectivity, VIEW DEFINITION and SELECT for the explicitly selected scope |
| Fabric workspace creation | Workspace.ReadWrite.All, tenant permission to create workspaces and capacity contributor/admin access |
| Lakehouse creation/loading | Appropriate workspace/item write access and Lakehouse.ReadWrite.All, or the applicable documented broader item scope for creation |
| Optional Warehouse creation | Workspace access, Warehouse.ReadWrite.All or applicable item scope, and explicitly selected collation |
| OneLake | The signed-in user's data access and a Storage-audience token |
| SQL analytics endpoint | Entra access to the ready endpoint/database and a SQL-audience token |

Delegated API scopes, tenant settings and resource roles are separate
requirements. An Azure subscription role or successful capacity/workspace GET
does not prove all required Fabric permissions. Capacity lookup is bounded to
the first page; the plugin does not silently search another tenant or capacity.

Fabric uses the explicitly selected cached Azure CLI **user**. Service
principals, managed identities, browser/device-code flows within this product,
SQL username/password source login and unattended migration are not implemented.
Do not put passwords, tokens or connection helpers into configuration or prompts.

Prefer a properly trusted source server certificate. A self-signed development
certificate requires your explicit source trust choice and organizational
authorization; the plugin must not choose that for you. Target SQL readback
always uses encryption with certificate validation.

## Supported data movement

The supported route is file-drop/Parquet to a new, project-created,
schema-disabled Lakehouse using the preview Load Table API.
It requires one exact table per stage chain and a complete visible nonnullable
primary key.

| Limit | Current bound |
| --- | --- |
| Columns | 1-128; ASCII SQL-style column names |
| Primary-key columns | 1-16 |
| Explicit row safety ceiling | 1-100000; full mode fails on overflow |
| Source query timeout | Explicit 1-60 seconds |
| Artifact/raw read budget | 64 MB; oversized results fail |
| Target table name | Explicit ASCII SQL-style identifier following approved naming |

Supported values include integers, bit, decimal/numeric up to precision 38,
finite float/real, date, bounded SQL character/binary types and datetime2 whose
scale is no greater than the explicitly selected storage precision (3 or 6).
Character/binary metadata lengths must be within 1-8000 bytes.

Computed columns, alias/assembly types, LOB/MAX/XML, datetime/smalldatetime,
time/datetimeoffset, GUID and unsupported/lossy types are blocked. Decimal
normalization must preserve source scale. Custom maps, filtered predicates,
incremental extraction, notebooks, pipelines, schema-enabled loading,
silver/gold conversion and SSIS migration are not implemented.

Normalization settings are explicit: trailing-space trimming and millisecond
comparison can intentionally normalize differences. Row hashes preserve
multiplicity and distinguish NULL, blank and case under the chosen rules.
Counts-only is a lower-assurance check, not value-equivalence proof.
Source reads are nontransactional; final source rereading detects observed
drift but does not establish a consistent snapshot.

## Approval and execution model

Use manual permission mode. Interview answers select configuration; only the
native permission prompt authorizes the reviewed operation. Configuration
approval, catalog access, read opt-in, a plan ID or a receipt is not migration
write approval.

Each configuration apply, source confirmation, create and movement execution
requires the applicable fresh one-use grant. Fabric writes additionally require
the selected migration-lead's native scoped hook. Do not supply grant arguments
manually, replay them or accept a prompt for the user.

Reads normally prompt each time. The user can explicitly authorize
`approvals reads allow` for the current session/process, and restore prompts
with `approvals reads ask`. Restart/resume/end resets this allowance. It is
not saved in configuration and never authorizes writes.

Hooks and lexical checks are not an OS sandbox. Native command-hook timeouts
can fail open; the implemented write executors independently require their
grants. This does not make arbitrary shell tools safe. The guarded workflow
admits the supplied wrapper, not direct SQL clients, arbitrary scripts,
HTTP/MCP writes or permission changes.

## Target isolation and recovery

The preview load operation uses Append after a bounded absence check; it has no
atomic create-only condition. A concurrent creator or file writer can race
those checks. Obtain explicit acknowledgement and keep the new canary target
exclusive. Do not claim that verification eliminates the race.

Durable attempts prevent blind retries after timeouts, crashes or uncertain
results. A created resource can exist even when verification/local bookkeeping
fails. There is no automatic rollback, adoption, overwrite or deletion.
Failed reconciliation does not undo a load.

Preserve the project and private host-managed state for authorized inspection.
Never delete markers or edit registry/receipt files to unblock a migration.
Review [troubleshooting](troubleshooting.md) before recovery.
