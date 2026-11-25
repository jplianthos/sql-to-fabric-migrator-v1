# First migration walkthrough

Complete [setup](getting-started.md) first. Use an isolated, authorized canary
target and a source table whose contents can remain stable during the test.
This walkthrough is interactive: answering a question is never approval to
write, and an agent must not accept a permission prompt on your behalf.

## 1. Answer the configuration interview

The agent should explain supported choices before asking you to select them.
You supply the SQL Server instance, Windows-authenticated source access and
certificate policy; exact databases/tables and extraction mode; Fabric user,
tenant, subscription and capacity; new workspace/item names; naming rules;
comparison settings; run identifier and row/time budgets.

For the currently implemented movement route, explicitly choose:

| Configuration | Supported selection |
| --- | --- |
| A5 / A6 | Azure CLI user authentication and your tenant UUID |
| B1 / B2 | Your capacity and explicitly named new workspace(s) in create mode |
| B5 / B7 | Schemas disabled; live with approval |
| B6 | Your table naming choice |
| C1 / C2 / C3 | Exact database/table scope and full, sample or schema-only mode |
| D1 / D2 / D3 | File drop, Parquet, Load Table API |
| D4 / D7 | Full extraction with no watermarks; no pipeline orchestration |
| E2 / E4 / E5 | Lossless supported timestamp precision, stop on relied-on key duplicates, preserve identity values |
| F1 / F2 / F3 / F4 | Your comparison checks/normalization, exact comparison, bronze only |

Custom type maps/overrides and unsupported alternatives must be reported as
blocked, not silently replaced. The agent can explain all available fields
through `config catalog --group A` through `G`. Some catalog options describe
future routes; appearing in the catalog does not mean they are executable.

The agent serializes your answers into a configuration preview. You do not need
to write JSON or YAML. Review the changed choices, generated configuration,
plan hash, expiry and project path. Accept the **native configuration-apply
prompt** only if they match your intent. Partial configuration can be saved but
does not authorize dependent stages. Unanswered choices remain unset.

## 2. Confirm identity and source scope

Expect local diagnostics, the explicitly scoped source check, and a separate
native source-identity confirmation. A local login-name hint or saved tenant
is not identity confirmation. No source connection helper is required.

Catalog discovery must stay inside your selected databases/tables. A
`table-check` reads metadata for the exact table and checks supported columns
and a complete nonnullable primary key. It does not extract row data.

Missing tools, permissions, unsupported types or mismatched identities stop the
walkthrough. A successful REST lookup does not prove OneLake, SQL endpoint or
write permissions.

## 3. Provision a new target

Provisioning currently requires a project-contained JSON request file; its
inline-answer path is not implemented. Ask the agent to show the exact
schema-valid content using only your explicit names and identity. Review and
save that content in your migration project yourself, outside the guarded
agent's file-editing role. This is separate from the interview-based
configuration and movement requests.

The request follows `schemas\provisioning-plan.schema.json`. For example,
replace every placeholder below with your own explicit value; this is not
selected configuration:

```json
{
  "kind": "fabric-provisioning",
  "runId": "<your-run-id>",
  "resourceType": "Workspace",
  "displayName": "<your-approved-new-workspace-name>",
  "workspaceId": null,
  "subscription": "<your-subscription-UUID>",
  "identity": {
    "windows": "<your-local-login-name-hint>",
    "fabricUser": "<your-Azure-CLI-user-UPN>",
    "tenant": "<your-tenant-UUID>"
  }
}
```

The agent then uses `provision --plan --request <project-contained-file>`.
For the separate Lakehouse request, use resourceType `Lakehouse`, your explicit
item name and the verified workspace UUID rather than null.

Review and approve the new workspace creation plan, then the separate
schema-disabled Lakehouse creation plan. Each has its own native prompt.
Review the capacity, names, API effects and potential capacity consumption.
The capacity must already exist and be usable by your identity.

The plugin must verify creation and register ownership before dependent stages
can use returned IDs. Existing workspace selection does not confer ownership
and cannot adopt an existing Lakehouse.

`202` means pending, not created. The agent may use GET-only
`provision-status <plan-id>` at the indicated polling time. It must not repeat
the create request.

## 4. Extract to local Parquet

Choose the run ID, explicit row ceiling and query timeout yourself. Review the
extract plan's table projection, primary-key ordering, mode and local file
effect, then approve that stage's native prompt.

Full mode fails on overflow; it does not silently become a sample. Sampling
uses the explicitly selected count and primary-key order. Schema-only writes
the supported empty typed schema. A successful extraction has an immutable
Parquet artifact and a verified hash/row signature. Receipts do not print rows.

## 5. Upload the new OneLake file

Review the exact owned target, file path, byte count and hash. Separately approve
the upload stage. The plugin conditionally creates a new file, reads it back
under its ETag and verifies SHA256. A collision, uncertain response or failed
verification must not trigger an overwrite or automatic retry.

## 6. Load the new bronze table

The preview Load Table API does not provide an atomic create-only operation.
The plugin checks absence and submits Append, but another writer can race that
check. **Keep the canary target exclusive and explicitly acknowledge this
limitation before the agent sets the load acknowledgement.**

Review the exact target table, API body and file hash, then approve the separate
native load prompt. A pending response is not a completed migration.
`load-status <load-plan-id>` performs GET-only polling and respects the indicated
next polling time; it never resubmits the load. Completion also requires the
exact visible managed Delta table.

## 7. Reconcile actual data

After loading, request reconciliation of that load plan. The plugin verifies the
local artifact, rereads the source and queries the actual Lakehouse SQL analytics
endpoint over encrypted ODBC with your Entra identity.

Review source drift, target differences, primary-key checks and the selected
comparison assurance. Counts-only does not prove value equivalence. Hash-enabled
success compares normalized row multisets; it is not a transactional snapshot
guarantee and does not validate silver/gold.

Endpoint synchronization can lag a completed load. If a read is not ready, stop
and request only another authorized status/reconciliation Read later. Never
reload or refresh metadata automatically to make reconciliation pass.

## Results and continuing a session

Your selected output folder contains per-run `movement` artifacts and receipts.
It defaults to `s2f-runs` until you explicitly configure G2. Plans and ownership
can be inspected through `plans list`, `plans show <id>` and `ledger show`.
Keep Parquet, configuration and audit output under your organization's data
handling policy; do not add them to the plugin distribution.

The private registry/receipts, not edited public JSON, establish authority.
Keep that host-managed state intact. Restart/resume resets read allowances and
source identity confirmation. Pending movement status and reconciliation can
resume for the same project/configuration/planned user; fresh source
confirmation is still required. This does not renew write approvals.

On failure, follow [troubleshooting](troubleshooting.md). Do not clear attempt
markers, adopt resources, fabricate receipts or approve a different operation
just to obtain a successful-looking result.
