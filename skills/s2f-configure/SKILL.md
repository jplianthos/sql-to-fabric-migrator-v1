---
name: s2f-configure
description: Use for migration configuration choices, scope, connection settings or change control.
---
# Configure
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Use `config catalog --group A` through G in order. Show choices, recommendation,
consequence and B4's creation-time lock before asking. Recommendations are not answers.
Collect choices with `ask_user`, one group at a time; users need not edit JSON.
Serialize only explicit answers into `config save --plan --answers '<JSON>'`.
Use an option-ID object, encode apostrophes as JSON `\u0027` for the canonical
single-quoted shell argument, and never include credentials. Unmentioned choices
stay unchanged/unset; explicit null clears an answer. The command validates and
creates a preview only. Never infer answers from environment variables or
treat interview answers as Write approval.
A user-authored project-contained YAML/JSON proposal remains supported via
`config init`, `config validate`, `config diff` and `config save --plan --input`.
Where needed, request bounded `config lookup` Reads with the user's explicit
expected Azure CLI user, tenant UUID and subscription UUID. Never change accounts,
pick a capacity/workspace, create resources or infer source scope.
Show both generated plan files, the exact answers/diff and hash, then
request `config apply <id>` once. Only native approval may apply the exact payload.
A7 uses `approvals reads allow|ask|status` and never belongs in the proposal.
## Inputs
Explicit interview answers or user-authored proposal, catalog and explicit identity context for optional live lookups.
## Outputs
Schema-validated configuration/diff; registered local-file plan; native-bound save.
Null answers remain blockers. Complete answers are not source/Fabric readiness.
## Scripts used
Session prefix plus `config init|catalog|validate|show|diff|lookup|save|apply`.
## Approvals required
Choices use `ask_user`; every save needs a separate native Write prompt.
Read lookups prompt unless a native-approved current-session allowance exists.
## Failure handling
Stop on schema, semantic, lock, base-hash, expiry, session, native-host or grant
failure. Do not supply internal grants or bypass with file tools.
Only cached Azure CLI user lookup is implemented; browser/device-code login,
source predicates, type maps and rule IDs still need later implementation/verification.
