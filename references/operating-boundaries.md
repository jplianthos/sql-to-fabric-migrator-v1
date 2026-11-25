# Operating and safety boundaries

Follow `s2f-approvals` before every action. Use manual permission mode.
Collect choices with `ask_user`; chat is never native approval. Never accept
an approval prompt for the user or change permission modes to bypass denial.

Use the exact session-provided wrapper prefix, project, private data path and
host session ID. Do not invent these values or set global permission or
authentication defaults. Plugin, project and host-managed state must be
separate ordinary directories.

## Available operations

Local diagnostics, configuration previews/native apply, plan inspection,
ownership inspection and bounded first-page Fabric capacity/workspace lookups
are available. Lookups require the explicitly selected cached Azure CLI user,
tenant and subscription; they do not prove write permissions.

Source identity/catalog reads require approved configuration and the SQL130
guard. User-database reads additionally require explicit scope and a fresh
native source confirmation. Exact table metadata/data access follows
[the movement contract](bronze-movement.md). Never use a connection helper,
general SQL client, arbitrary shell/HTTP command or MCP write as a fallback.

New sandbox Workspace/Lakehouse/Warehouse creation is available through exact
fabric-provisioning plans. A workspace must be created/verified by this project
on the selected capacity before it can authorize item creation. Existing
workspace selection does not confer ownership. Warehouse collation locks only
after verified creation. Capacity creation/resizing is not implemented.

The bounded bronze route uses separate exact data-movement plans for local
Parquet extraction, conditional OneLake upload and preview Lakehouse loading.
Uploads/loads require a verified project-created, schema-disabled Lakehouse.
Never silently change B5/D3/F4 or choose names, scope, budgets or order.
The preview Append load is not atomic create-only: explain the race, require
explicit acknowledgement and exclusive canary-target use.

Modules, general predicates, profiling, assessment, wave planning, silver/gold
conversion, notebooks, pipelines and SSIS migration remain unavailable.
Legacy descriptive plans are not executable contracts. Do not delegate an
unsupported phase or present empty/generated output as its successful result.

## Authority and approvals

Configuration plans bind the exact payload, base-file hash, project, session
and expiry. Stage plans bind their exact scope, effects and predecessors.
Registration, configuration approval, read permission and completed receipts
never replace approval for a new write.

Each applicable native pre-hook injects a one-use grant; never expose or
provide it yourself. Fabric write execution additionally requires the selected
migration-lead's native scoped-hook attestation. Missing/timed-out scoped hooks
cannot authorize execution inside the CLI. Role names/front matter alone are
not authority. Other agents must not request execution.

Successful post hooks validate private proof and register configuration,
ownership and stage completion. Public Parquet/manifests/JSON cannot establish
authority. Do not edit plans, policies, audits, private registry or receipts.

Reads ask unless an explicit native-approved `approvals reads allow` lease is
active for this session and native Windows process. `approvals reads ask`,
restart/resume/end reset it. Source confirmation is separately required.
Pending movement status/reconciliation reads can resume for the same project,
configuration and planned user, but do not renew write grants.

## Files, failures and results

File tools require explicit non-linked project paths; static packaged
references, schemas and skills may also be read. Searches name explicit files.
Honor configured restricted paths. Never read credentials or write outside
the admitted project paths. Unknown tools/argument forms fail closed.

Native command-hook timeouts can fail open. The write executors independently
require grants; hooks/lexical checks still are not an OS sandbox or protection
against arbitrary local processes.

Durable exclusive attempts survive uncertain writes. A timeout/crash/failed
post hook may leave a resource without registered ownership. Stop for human
inspection; do not retry writes, clear markers, adopt, overwrite, grant access,
delete resources or invent rollback.

202 is pending, not created/loaded/owned. Poll only the supported status Read
after its next polling time. A loaded table is not reconciliation success.
Compare actual target rows and a fresh source read under explicit F1/F2 rules.
Counts-only is not value equivalence; hash success is normalized,
nontransactional bronze comparison, not silver/gold completion.

On endpoint lag, stop and request only another authorized Read later. Never
reload or perform a metadata refresh write to force a successful result.
