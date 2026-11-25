---
name: s2f-start
description: Use to start or resume an approval-first SQL Server to Fabric migration project.
---
# Start
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Use `ask_user` to select an existing separate project folder; select no defaults.
Use the session-provided wrapper for `doctor` and local `whoami`.
Explain that sql2016-source provides guarded identity confirmation; interactive
Entra sign-in remains user-owned and is not implemented here.
Use `s2f-configure` for user-authored configuration; do not infer choices.
Optional bounded Fabric lookups verify an explicitly named cached Azure CLI user,
tenant and subscription, but never authorize writes or confirm source identity.
User-approved new sandbox provisioning is available through fabric-provisioning
after configuration; it does not approve any subsequent migration write.
Use sql2016-source and estate-discovery only after explicit configuration/scope.
Catalog discovery does not extract rows. For an explicitly chosen supported
bronze migration, follow bronze-ingestion and references/bronze-movement.md.
Require new owned schema-disabled targets, exact plans, explicit budgets and
separate native extraction/upload/load approvals, then actual reconciliation.
Explain the preview load race and obtain its explicit acknowledgement.
Profiling and silver/gold remain unavailable. Never run connection helpers.
## Inputs
Host session prefix and user-selected separate project.
## Outputs
Local diagnostics, explicit configuration interview and guarded readiness hand-offs.
## Scripts used
`scripts\s2f.ps1 doctor` and `whoami` with the full session prefix.
## Approvals required
Native Read prompt per command unless explicitly allowed for this session;
source identity confirmation always requires its own native prompt.
## Failure handling
Stop on missing tools/context; do not sign in or infer identities automatically.
