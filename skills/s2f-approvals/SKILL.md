---
name: s2f-approvals
description: Use before every migration action or when reviewing plans, permissions, approvals, or audit records.
---
# Approval protocol
## Steps
Read [operating boundaries](../../references/operating-boundaries.md).
Classify Read, Write or Execute. Reads ask unless the user explicitly opts in
through native-approved `approvals reads allow` for this session and host process.
Use `approvals reads ask` to revoke; restart/resume also resets the allowance.
Draft a plan with the canonical wrapper and explicit request. Provisioning
requires --plan --request; movement also supports --plan --answers from explicit
interview choices. Have the operator save a provisioning request when needed;
this role must not bypass direct-file-write restrictions.
Inspect the JSON and Markdown, plan ID, hash, expiry and workspace fence.
Registration is not approval. Only native permission prompts authorize actions.
Never interpret a chat answer or unattended denial as approval; stop.
Legacy descriptive execution remains disabled even for a registered plan.
Exact fabric-provisioning creates and bounded data-movement extraction/upload/
load plans require the selected migration-lead's native scoped hook and one-use
grant. Each stage has its own native approval; a predecessor receipt is not a
grant for the next stage.
Configuration uses its own exact local-file plan: `config save --plan --answers`
or `config save --plan --input`,
followed by `config apply <id>`. Inspect `config-plans/<id>.json/.md` first.
The native pre-hook injects a private one-use grant for this local operation,
session read opt-in/source confirmation, or an exact create/movement stage.
Never provide `--native-grant` yourself or expose it. Missing/timed-out scoped
hooks cannot authorize execution.
An unattended denial must not be retried through another permission mode.
## Inputs
Host session context, explicit project and draft request.
## Outputs
Draft plan, hook-owned registry entry, audit projection and (only after native
approval and verified execution) the applicable creation/movement receipt.
## Scripts used
`scripts\s2f.ps1`; session, pre and post hooks.
## Approvals required
Native prompts for reads without opt-in, every configuration apply, every
allowance change and every execution request. Require manual mode.
## Failure handling
Stop on missing hooks, identity, configuration, expiry, hash or workspace mismatch.
Never retry through SQL clients, arbitrary scripts, HTTP tools or MCP writes.
