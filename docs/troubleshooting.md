# Troubleshooting

Stop on an unexpected result. Do not switch permission modes, bypass the
wrapper, grant privileges or rerun a write to see whether it works.

| Symptom | Safe next action |
| --- | --- |
| Python/module or Node dependency missing | Exit the guarded session; restore the pinned dependencies in the actual loaded plugin folder using the setup guide. Start a fresh session. |
| SQL130 guard unavailable | Build `tools\S2F.ScriptDom` in Release as described in setup. Do not substitute an unguarded SQL client. |
| ODBC Driver 18 missing | Have the driver installed through your organization's approved process, then restart. |
| Unexpected command unavailable after update | Confirm the fresh session loads the updated plugin folder rather than an older cached installation. |
| Project/data/root validation fails | Use separate ordinary folders for plugin, project and host-managed state; avoid links, junctions and single quotes in roots. |
| Configuration unset or not approved | Complete the interview, review a fresh preview and use native configuration apply. Saving a proposal alone is not approval. |
| User/tenant/subscription mismatch | Stop. Correct interactive Azure CLI sign-in/context yourself outside the guarded session; confirm your choices before resuming. |
| Source login, certificate or SELECT/VIEW DEFINITION error | Ask the SQL administrator to verify the selected Windows principal, certificate policy and exact table/database permissions. |
| Fabric 401/403 or selected capacity unavailable | Have the administrator check delegated scopes, tenant settings and capacity/workspace/item roles. A REST read is not proof of write or data access. |
| Unsupported source type, missing PK or safety ceiling exceeded | Review the reported limitation. Explicitly select a supported table/mode/budget; do not silently drop columns, round values or sample. |
| Missing, expired or changed plan/grant | Inspect current state, then request the appropriate fresh plan and native approval. Never provide a grant value yourself. |
| Target not project-owned or already exists | Do not adopt, append to or overwrite it. Review a new isolated target with the operator; existing selection does not confer ownership. |
| Upload/create/load timeout, crash or uncertain result | Preserve evidence and inspect Fabric with the operator. The write may have happened. Do not retry or clear the retained attempt. |
| Operation pending | Respect `nextPollAt`; use only the corresponding GET-only status command. Do not repeat execution. |
| Load complete but SQL readback not ready | Wait for endpoint availability/synchronization and request another authorized reconciliation Read. Do not reload or issue a refresh write. |
| Reconciliation mismatch or source drift | Review the actual differences and chosen assurance with the data owner. Do not edit receipts, weaken checks or claim a pass. |
| Configuration change blocked while an operation is pending | Resolve the existing operation through its supported status flow first. Do not modify its binding underneath it. |
| Local write succeeded but post-hook registration failed | Treat it as unapproved. Inspect actual state and request a fresh approved local-file plan; do not fabricate authority or reuse a grant. |

## What to retain

Keep the plan/run/operation identifiers, sanitized error, observed time, actual
stage state and relevant project receipts. Private host-managed authority is
not replaceable with copied public JSON. Handle Parquet, source object names
and audit SQL as customer data under your organization's policy.

Do not include tokens, passwords, private grant material, raw HTTP error bodies
or source rows in support messages. Share only the minimum approved, sanitized
evidence. The plugin never requires a connection helper or an internal test
environment to operate.

An authorized human can inspect resources through normal administrative
channels. This does not authorize the agent to adopt/delete them or remove
private markers. Automatic cleanup is not implemented.
