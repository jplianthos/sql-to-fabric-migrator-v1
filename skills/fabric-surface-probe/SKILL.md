---
name: fabric-surface-probe
description: Use for requests to verify Fabric SQL features with scratch-schema probes.
---
# Fabric surface probes
## Steps
Follow `s2f-approvals` and [boundaries](../../references/operating-boundaries.md).
Explain that no live probe executor exists. Do not create a scratch schema.
Unknown feature support stays unknown until a separately approved future probe.
## Inputs
Future verified rule and approved scratch workspace.
## Outputs
Blocked status; no feature verdict or cleanup claims.
## Scripts used
None; the live Fabric boundary is disabled.
## Approvals required
Future probe creation/execution and each cleanup delete need separate native plans.
## Failure handling
Never issue ad-hoc SQL, HTTP or MCP writes as a substitute.
