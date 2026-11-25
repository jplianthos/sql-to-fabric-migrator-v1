# Bounded bronze contract

The runnable route is **file-drop, Parquet, preview Load Table API, new
schema-disabled project-created Lakehouse**. It is not silver/gold migration.
Use ask_user for choices and the native prompt for each Write/Execute.
Never infer values from environment variables, examples or available inventory.

Persistent prerequisites: explicit source A1-A4, Azure CLI user A5/tenant A6,
capacity B1, new-workspace B2 fence, B5 disabled, B6 naming, B7 live-with-approval,
C1/C2/C3, D1 file-drop, D2 parquet, D3 load-table-api, D4 full with no watermarks,
D7 none, E2 3/6, E4 stop-relied-on, E5 preserve, F1/F2, exact F3 and
F4 ["bronze"]. Custom E3 maps and object overrides are blocked. G2/G3 control
output/expiry as usual. Other recorded options do not imply those phases are
implemented.

After source-check/native source-confirm:

1. `table-check --run-id <id> --database <db> --schema <schema> --table <table> --query-timeout <1-60>`
   is a Read, metadata only, for one explicit user table. It returns checkId,
   columns and a complete visible nonnullable PK. No database/table is inferred.
2. `extract --plan --answers '<JSON>'` request keys are exactly kind
   ("data-movement"), runId (same as check), checkId, identity
   ({windows: local login hint, fabricUser: explicit UPN, tenant: UUID}),
   subscription (UUID), maxRows (1-100000 ceiling), queryTimeout (1-60).
   Review then native `execute <plan-id>`. Full mode never silently samples;
   sample is TOP N ordered by the PK; schema-only is TOP 0.
3. `upload --plan --answers '<JSON>'` keys: kind, previousPlanId (completed
   extraction), target ({workspaceId, lakehouseId, tableName}). IDs must belong
   to this project's verified creation ledger; tableName must follow B6.
   Review then native execute. PUT uses If-None-Match: *, SHA256 readback and
   the created ETag. No overwrite/retry.
4. `load --plan --answers '<JSON>'` keys: kind, previousPlanId (completed upload),
   acknowledgeNonAtomicCreate: true. **Ask before setting that acknowledgement.**
   Review then native execute. API mode is Append after an absence check:
   it is not atomic create-only, and concurrent writers can race it. Do not share
   the new target during this canary. The plan/native prompt states this risk.
5. `load-status <load-plan-id>` is GET-only; respect nextPollAt. Only the matching
   successful operation plus exact visible managed Delta table yields loaded.
6. `reconcile --plan-id <load-plan-id>` is a Read of the exact source slice and
   actual SQL endpoint rows, not generated expected rows. Report the explicit
   F1 checks, any source drift and target differences. Only hash-enabled success
   says valueHashesMatched. Even that is normalized, nontransactional bronze
   comparison, not silver/gold completion or a transactional snapshot.

Plans/requests must not contain data values or credentials. CLI receipts never
contain rows/tokens. Parquet and movement JSON are CLI-owned under the project's
run/movement folder; public copies cannot establish authority. Successful post
hooks register provenance; missing hooks, forged receipts, tampering, replay,
expiry, wrong configuration/session/scope and unowned targets fail closed.
Pending-status/reconciliation Reads may resume under a new session for the same
project/configuration/planned user; fresh source confirmation is still required.
This does not renew or transfer a Write/Execute grant.

Supported values: bounded SQL character/binary types, integers, bit,
decimal/numeric <=38, finite float/real, date and datetime2 with scale <= E2
(3 or 6). No computed/alias/CLR/LOB/time/offset/GUID/datetime types, predicates,
incremental mode or custom maps. Column/target names use ASCII SQL identifiers.
At most 128 columns, 16 PK columns, 100000 rows and 64 MB per artifact/raw read.
Overflow fails rather than producing a partial success.

Normalization explicitly trims ASCII trailing spaces only when selected;
millisecond comparison truncates fractional microseconds when selected.
Decimal comparison never silently rounds: F2 scale must preserve source scale.
Storage itself must roundtrip losslessly. Row hashes are sorted multisets,
preserving duplicate multiplicity and avoiding collation/order dependence.
The relied-on PK must remain nonnull/unique. Profiles report NULL counts,
distinct nonnull values and maximum normalized UTF-8 bytes.

Stop on any unsupported choice, error, schema drift, target collision,
uncertain write or endpoint lag. Only an authorized status/reconciliation Read
may be attempted later; never resubmit a load to remedy endpoint lag.
No write retries, permission grants, refresh writes, resource adoption, deletion
or private marker clearing. A normal failed extract also retains its attempt:
ask the user to select a fresh run after inspection.
