import hashlib
import json
import os
import re
from datetime import timedelta
from uuid import uuid4

from . import approvals, configuration, host, provisioning, source
from .safety import (
    SafetyError, atomic_json, atomic_text, digest, identifier, load_json,
    parse_time, timestamp, utc_now,
)
from .state import read_state


KIND = "data-movement"
STAGES = ("extract", "upload", "load")
EXTRA_KEYS = {"kind", "spec", "artifact", "subscription", "reads"}
SPEC_KEYS = {
    "checkId", "previousPlanId", "extractPlanId", "maxRows", "queryTimeout",
    "table", "columns", "keys", "mode", "target",
}


def object_id(table):
    return source.qualified(*(table[key] for key in ("database", "schema", "table")))


def settings(context):
    selected = source.options(context)
    if (selected["D1"] != "file-drop" or selected["D2"] != "parquet" or selected["D3"] != "load-table-api"
            or selected["D4"] != {"mode": "full", "watermarks": {}} or selected["D7"] != "none"
            or selected["B5"] != "disabled"):
        raise SafetyError(
            "This bronze checkpoint requires explicit file-drop/Parquet/load-table-api, full mode, "
            "D7 none and B5 disabled. No notebook, schema-enabled, incremental or pipeline fallback is implemented."
        )
    if (selected["E2"] not in (3, 6) or selected["E3"] is not None
            or selected["E4"] is None or selected["E4"]["duplicates"] != "stop-relied-on"
            or selected["E5"] != "preserve"
            or selected["F1"] is None or selected["F2"] is None
            or selected["F3"] != {"mode": "exact", "value": None} or selected["F4"] != ["bronze"]
            or configuration.read(context).get("overrides")):
        raise SafetyError(
            "Choose explicit lossless E2, E4 stop-relied-on, E5 preserve, F1/F2, exact F3 and bronze-only F4. "
            "Custom type maps/overrides, numeric tolerance and silver/gold validation are not implemented."
        )
    return selected


def mode(context, table, max_rows):
    selected = settings(context)
    source.table_scope(selected, **table)
    if type(max_rows) is not int or not 1 <= max_rows <= 100000:
        raise SafetyError("Choose an explicit 1-100000 row safety ceiling; overflow fails, never silently samples.")
    value = (selected["C3"] or {}).get(object_id(table))
    if value is None or value["mode"] == "filtered" or value["predicate"] is not None:
        raise SafetyError("Choose explicit full/sample/schema-only C3 for this exact three-part table; predicates remain blocked.")
    if value["mode"] == "sample" and value["rows"] > max_rows:
        raise SafetyError("The chosen sample exceeds the explicit safety ceiling.")
    return value, 0 if value["mode"] == "schema-only" else value["rows"]


def snapshot(context, run_id, table, timeout, expected=None, max_rows=None):
    from . import data

    selected = settings(context)
    identity = source.confirmed(context)
    user = source.windows_user()
    if identity["sourceUser"].casefold() != user.casefold():
        raise SafetyError("The confirmed Windows/source identity changed before extraction.")
    source.table_scope(selected, **table)
    database = table["database"]
    with source.audit_run(context, run_id) as log, source.read_source(
        context, database, timeout, ("database",), table=(table["schema"], table["table"]),
    ) as (connection, texts):
        if source.probe(connection, context, texts, database, log, user) != identity:
            raise SafetyError("Source identity/engine changed after confirmation.")
        observed = source.query(connection, context, "database", texts["database"], database, log)
        if (len(observed) != 1 or observed[0].get("name") != database or observed[0].get("state") != "ONLINE"
                or observed[0].get("canViewDefinition") != 1):
            raise SafetyError("The exact source database is unavailable or VIEW DEFINITION is missing; no grant was attempted.")
        columns = source.table_metadata(context, connection, table, log)
        keys = data.validate_columns(columns, selected["E2"], selected["F2"])
        if expected is not None and columns != expected:
            raise SafetyError("Source table metadata changed; obtain a fresh table-check and plan.")
        rows = None
        if max_rows is not None:
            _, limit = mode(context, table, max_rows)
            rows = source.table_rows(
                context, connection, table, [column["name"] for column in columns], keys, limit, max_rows, log,
            )
            data.check_keys(rows, keys)
            if source.table_metadata(context, connection, table, log) != columns:
                raise SafetyError("Source metadata changed during the nontransactional extract.")
        if source.probe(connection, context, texts, database, log, user) != identity:
            raise SafetyError("Source identity/engine changed during extraction.")
    source.options(context)
    return columns, keys, rows


def table_check(context, run_id, database, schema, table, timeout):
    identifier(run_id)
    target = {"database": database, "schema": schema, "table": table}
    columns, keys, _ = snapshot(context, run_id, target, timeout)
    receipt = {
        "contractVersion": "1.0", "kind": "movement-table", "checkId": str(uuid4()), "runId": run_id,
        "table": target, "columns": columns, "keys": keys, "estimatedRows": None,
        "consistency": "nontransactional", "dataRead": False,
    }
    atomic_json(provisioning.private_path(context, "movement-checks", receipt["checkId"]), {
        "receipt": receipt, "project": str(context.project), "session": context.session,
        "instance": host.instance(), "configHash": context.config_hash(),
        "expiresAt": timestamp(utc_now() + timedelta(minutes=60)),
    })
    return receipt


def checked_table(context, check_id, fresh=False):
    value = load_json(provisioning.private_path(context, "movement-checks", identifier(check_id)))
    if (not isinstance(value, dict) or value.get("project") != str(context.project)
            or value.get("configHash") != context.config_hash()
            or not isinstance(value.get("receipt"), dict) or value["receipt"].get("checkId") != check_id
            or value["receipt"].get("kind") != "movement-table"):
        raise SafetyError("The table check has no matching private project/configuration proof.")
    if fresh and ((value.get("session"), value.get("instance")) != (context.session, host.instance())
                  or parse_time(value.get("expiresAt")) <= utc_now()):
        raise SafetyError("Obtain a fresh table-check for this source session before extraction.")
    return value["receipt"]


def target_scope(context, target, connection):
    if not isinstance(target, dict) or set(target) != {"workspaceId", "lakehouseId", "tableName"}:
        raise SafetyError("Choose the exact project-created workspace/Lakehouse IDs and target table name.")
    workspace, item = provisioning.uuid(target["workspaceId"]), provisioning.uuid(target["lakehouseId"])
    if not isinstance(target["tableName"], str) or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", target["tableName"]) is None:
        raise SafetyError("The target table name must be an explicit, bounded ASCII SQL identifier.")
    selected = provisioning.options(context, connection)
    row = connection.execute(
        "SELECT w.name,i.name,i.schemas_enabled FROM sandbox_workspaces w JOIN owned_items i "
        "ON i.project=w.project AND i.workspace=w.id JOIN ownership o "
        "ON o.project=i.project AND o.workspace=i.workspace AND o.item=i.item "
        "WHERE w.project=? AND w.id=? AND w.capacity=? AND w.tenant=? AND i.item=? AND i.type='Lakehouse'",
        (str(context.project), workspace, selected["B1"]["id"], selected["A6"], item),
    ).fetchone()
    if (row is None or row[2] != 0 or not any(
        entry == {"id": workspace, "name": row[0]}
        for entry in configuration.approved_workspaces(configuration.read(context), connection, context.project)
    )):
        raise SafetyError("The target must be a verified, schema-disabled Lakehouse created by this project inside its current fence.")
    return {"id": workspace, "name": row[0]}, row[1]


def receipt_proof(context, receipt_id):
    proof = load_json(provisioning.private_path(context, "movement-receipts", identifier(receipt_id)))
    receipt = proof.get("receipt") if isinstance(proof, dict) else None
    if (not isinstance(proof, dict) or proof.get("project") != str(context.project) or proof.get("configHash") != context.config_hash()
            or not isinstance(receipt, dict) or receipt.get("receiptId") != receipt_id or receipt.get("kind") != KIND):
        raise SafetyError("Movement receipt has no matching private transport/extraction proof.")
    return proof


def previous(context, plan_id, stage):
    from .plans import plan_path

    plan = load_json(plan_path(context, identifier(plan_id)))
    if (not isinstance(plan, dict) or plan.get("kind") != KIND or plan.get("operation") != stage
            or plan.get("planId") != plan_id or plan.get("project") != str(context.project)
            or plan.get("configHash") != context.config_hash()):
        raise SafetyError("The preceding plan is not the required movement stage.")
    with read_state(context) as connection:
        row = connection.execute(
            "SELECT hash,state,receipt_id FROM data_steps WHERE project=? AND plan_id=?",
            (str(context.project), plan_id),
        ).fetchone()
    expected = {"extract": "extracted", "upload": "uploaded"}[stage]
    if row is None or row[:2] != (digest(plan), expected):
        raise SafetyError("The preceding stage has no hook-recorded, verified completion; public files are not authority.")
    receipt = receipt_proof(context, row[2])["receipt"]
    if (receipt["planId"], receipt["planHash"], receipt["status"]) != (plan_id, digest(plan), expected):
        raise SafetyError("The preceding stage's private receipt differs from hook-owned state.")
    return plan, receipt


def artifact_path(context, plan):
    return context.run_path(plan["runId"], "movement", plan["spec"]["extractPlanId"] + ".parquet")


def artifact_bytes(context, plan):
    from .data import MAX_BYTES

    path = artifact_path(context, plan)
    artifact = plan["artifact"]
    if (not path.is_file() or path.stat().st_size != artifact["bytes"]
            or not 0 < artifact["bytes"] <= MAX_BYTES):
        raise SafetyError("The exact extracted Parquet file is missing or oversized.")
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != artifact["sha256"]:
        raise SafetyError("The extracted Parquet file changed; upload/load/reconciliation was blocked.")
    return content


def storage_path(context, plan):
    target = plan["spec"]["target"]
    return (
        f"/{target['workspaceId']}/{target['lakehouseId']}/Files/s2f/"
        f"{digest(str(context.project))[:16]}/{plan['runId']}/{plan['spec']['extractPlanId']}.parquet"
    )


def effects(context, operation, request, plan_id, fresh=False):
    selected = settings(context)
    if operation == "extract":
        if set(request) != {"kind", "runId", "checkId", "identity", "subscription", "maxRows", "queryTimeout"}:
            raise SafetyError("Extraction requires an exact table check, identities, run and explicit row/time budgets.")
        check = checked_table(context, request["checkId"], fresh)
        if check["runId"] != request["runId"]:
            raise SafetyError("Extraction run must match the explicitly requested table check.")
        data_mode, limit = mode(context, check["table"], request["maxRows"])
        if type(request["queryTimeout"]) is not int or not 1 <= request["queryTimeout"] <= 60:
            raise SafetyError("Choose an explicit 1-60 second query timeout.")
        spec = {
            "checkId": request["checkId"], "previousPlanId": None, "extractPlanId": plan_id,
            "maxRows": request["maxRows"], "queryTimeout": request["queryTimeout"],
            "table": check["table"], "columns": check["columns"], "keys": check["keys"],
            "mode": data_mode, "target": None,
        }
        run_id, identity, subscription, artifact = request["runId"], request["identity"], request["subscription"], None
        workspace = {"id": "local", "name": "project-local"}
        call = {
            "workspaceId": "local", "itemId": "", "operation": "create",
            "description": "Read exact selected source rows; create one immutable local Parquet file. No Fabric calls.",
            "method": "LOCAL", "path": str(context.run_path(run_id, "movement", plan_id + ".parquet")),
            "body": {"sql": source.data_sql(
                context, spec["table"], [column["name"] for column in spec["columns"]], spec["keys"], limit,
            ), "maxRows": spec["maxRows"], "maxBytes": 64_000_000},
        }
        reads = ["source:identity-before-and-after", "source:database", source.metadata_sql(
            context, spec["table"]["schema"], spec["table"]["table"],
        )]
    elif operation in ("upload", "load"):
        required = {"kind", "previousPlanId", "target"} if operation == "upload" else {
            "kind", "previousPlanId", "acknowledgeNonAtomicCreate",
        }
        if set(request) != required or operation == "load" and request["acknowledgeNonAtomicCreate"] is not True:
            raise SafetyError("Choose the exact preceding stage/target; loading requires explicit non-atomic-create acknowledgement.")
        parent, receipt = previous(context, request["previousPlanId"], "extract" if operation == "upload" else "upload")
        spec = {**parent["spec"], "previousPlanId": parent["planId"]}
        if operation == "upload":
            spec["target"] = request["target"]
        run_id, identity, subscription, artifact = parent["runId"], parent["identity"], parent["subscription"], receipt["artifact"]
        with read_state(context) as connection:
            workspace, _ = target_scope(context, spec["target"], connection)
        naming = selected["B6"]
        table_id = object_id(spec["table"])
        expected_name = None if naming is None else (
            spec["table"]["table"] if naming["mode"] == "keep" else
            naming["prefix"] + spec["table"]["table"] if naming["mode"] == "prefix" else naming["mapping"].get(table_id)
        )
        if spec["target"]["tableName"] != expected_name:
            raise SafetyError("The explicit target table name differs from approved B6 naming.")
        base = f"/v1/workspaces/{workspace['id']}/lakehouses/{spec['target']['lakehouseId']}"
        shell = {"runId": run_id, "spec": spec}
        relative = storage_path(context, shell).split("/Files/", 1)[1]
        call = {
            "workspaceId": workspace["id"], "itemId": spec["target"]["lakehouseId"],
            "operation": "update" if operation == "upload" else "execute",
            "description": (
                "Conditional new-file upload and exact SHA256 readback; never overwrite/retry."
                if operation == "upload" else
                "Preview Load Table API Append after absence check; not atomic create-only. Concurrent creators may merge data; reconciliation is required."
            ),
            "method": "PUT" if operation == "upload" else "POST",
            "path": "https://onelake.blob.fabric.microsoft.com" + storage_path(context, shell) if operation == "upload"
            else base + "/tables/" + spec["target"]["tableName"] + "/load",
            "body": {"sha256": artifact["sha256"], "bytes": artifact["bytes"], "ifNoneMatch": "*"}
            if operation == "upload" else {
                "relativePath": "Files/" + relative, "pathType": "File", "mode": "Append", "recursive": False,
                "formatOptions": {"format": "Parquet"},
            },
        }
        reads = [
            "/v1/workspaces/" + workspace["id"], base, base + "/tables?maxResults=100",
            "https://onelake.blob.fabric.microsoft.com" + storage_path(context, shell),
        ]
        if operation == "load":
            reads.append("/v1/operations/{operation-id}")
    else:
        raise SafetyError("Unsupported movement stage.")
    if (not isinstance(identity, dict) or set(identity) != {"windows", "fabricUser", "tenant"}
            or any(not isinstance(value, str) or not value.strip() for value in identity.values())
            or identity["tenant"] != selected["A6"] or selected["A5"] != "azure-cli"
            or selected["B7"] != "live-with-approval"):
        raise SafetyError("Explicit user identities, configured tenant, azure-cli and live-with-approval are required.")
    provisioning.uuid(subscription)
    provisioning.uuid(identity["tenant"])
    identifier(run_id)
    return {
        "runId": run_id, "identity": identity, "subscription": subscription, "spec": spec, "artifact": artifact,
        "summary": f"{operation.capitalize()} one explicitly selected bronze table ({spec['mode']['mode']}); reconciliation required.",
        "workspaces": [workspace], "objects": [object_id(spec["table"])],
        "estimatedRows": artifact["rows"] if artifact else (spec["mode"]["rows"] if spec["mode"]["mode"] == "sample"
                                                         else 0 if spec["mode"]["mode"] == "schema-only" else None),
        "reversible": False, "calls": [call], "reads": reads,
    }


def request_from_plan(plan):
    spec = plan["spec"]
    if plan["operation"] == "extract":
        return {
            "kind": KIND, "runId": plan["runId"], "checkId": spec["checkId"],
            "identity": plan["identity"], "subscription": plan["subscription"],
            "maxRows": spec["maxRows"], "queryTimeout": spec["queryTimeout"],
        }
    if plan["operation"] == "upload":
        return {"kind": KIND, "previousPlanId": spec["previousPlanId"], "target": spec["target"]}
    return {"kind": KIND, "previousPlanId": spec["previousPlanId"], "acknowledgeNonAtomicCreate": True}


def validate_plan(plan, context, now=None, allow_expired=False):
    from .plans import PLAN_KEYS, validate_plan as validate_legacy

    if (not isinstance(plan, dict) or set(plan) != PLAN_KEYS | EXTRA_KEYS or plan.get("kind") != KIND
            or plan.get("operation") not in STAGES or not isinstance(plan.get("spec"), dict)
            or set(plan["spec"]) != SPEC_KEYS):
        raise SafetyError("Invalid executable movement plan.")
    legacy = {key: value for key, value in plan.items() if key in PLAN_KEYS}
    if not isinstance(plan["calls"], list) or len(plan["calls"]) != 1 or not isinstance(plan["calls"][0], dict):
        raise SafetyError("Each movement approval authorizes exactly one bounded stage.")
    legacy["calls"] = [
        {key: value for key, value in plan["calls"][0].items() if key not in {"method", "path", "body"}}
    ]
    validate_legacy(legacy, context, now, allow_expired)
    expected = effects(context, plan["operation"], request_from_plan(plan), plan["planId"])
    if any(plan.get(key) != value for key, value in expected.items()):
        raise SafetyError("Movement calls, artifact, source scope or target fence differ from the exact selected stage.")
    return plan


def draft(context, operation, request):
    from .plans import plan_path

    if not isinstance(request, dict) or request.get("kind") != KIND:
        raise SafetyError("Movement plans require the exact data-movement request contract.")
    plan_id, now = str(uuid4()), utc_now()
    exact = effects(context, operation, request, plan_id, fresh=True)
    plan = {
        "contractVersion": "1.0", "kind": KIND, "planId": plan_id, "project": str(context.project),
        "createdAt": timestamp(now), "expiresAt": timestamp(now + timedelta(minutes=context.runtime_options()["G3"] or 60)),
        "configHash": context.config_hash(), "operation": operation,
        "actionClass": "Execute" if operation == "load" else "Write", **exact,
    }
    validate_plan(plan, context)
    path = plan_path(context, plan_id)
    atomic_json(path, plan)
    atomic_text(
        path.with_suffix(".md"),
        "# Bounded bronze movement plan\n\n"
        f"Plan `{plan_id}`; SHA256 `{digest(plan)}`; expires {plan['expiresAt']}.\n\n"
        "NOT approved. Exactly this stage, no retries, overwrite, adoption, source mutation or cleanup. "
        "Source consistency is nontransactional; the final comparison rereads the source and actual target. "
        "The Load Table API is preview and has no atomic create-only mode. Append is sent only after an absence "
        "check; a concurrent writer can race that check. Do not share the new target during this canary. "
        "A load receipt is not value-equivalence proof. Only bronze is in scope.\n\n"
        "```json\n" + json.dumps(plan, indent=2) + "\n```\n",
    )
    return plan


def attempt_key(context, plan):
    target = plan["spec"]["target"]
    scope = ["extract", plan["runId"]] if target is None else [
        plan["operation"], target["workspaceId"], target["lakehouseId"],
        storage_path(context, plan) if plan["operation"] == "upload" else target["tableName"].casefold(),
    ]
    return digest([str(context.project), scope])


def ensure_unattempted(context, plan):
    if provisioning.private_path(context, "movement-attempts", attempt_key(context, plan)).exists():
        raise SafetyError("This movement target/run was already attempted; inspect retained evidence. No automatic retry is allowed.")


def claim_target(context, plan, grant_id):
    from .state import validate_registered

    with read_state(context) as connection:
        validate_registered(connection, context, plan["planId"])
    path = provisioning.private_path(context, "movement-attempts", attempt_key(context, plan))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump({"planId": plan["planId"], "planHash": digest(plan), "grantId": grant_id}, output)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        raise SafetyError("A concurrent/previous movement attempt already claimed this target; no write was repeated.") from None


def publish(context, plan, grant_id, status, artifact=None, operation_id=None, next_poll=None, previous_id=None, report=None):
    receipt = {
        "contractVersion": "1.0", "kind": KIND, "stage": plan["operation"], "status": status,
        "planId": plan["planId"], "planHash": digest(plan), "runId": plan["runId"], "grantId": grant_id,
        "receiptId": str(uuid4()), "operationId": operation_id, "nextPollAt": next_poll,
        "previousReceiptId": previous_id, "artifact": artifact if artifact is not None else plan["artifact"], "report": report,
    }
    atomic_json(provisioning.private_path(context, "movement-receipts", receipt["receiptId"]), {
        "receipt": receipt, "project": str(context.project), "session": context.session,
        "instance": host.instance(), "configHash": context.config_hash(),
    })
    atomic_json(context.run_path(plan["runId"], "movement", receipt["receiptId"] + ".json"), receipt)
    return receipt


def execute(context, plan, grant):
    from . import data, fabric
    from .state import validate_registered

    with read_state(context) as connection:
        registered = validate_registered(connection, context, plan["planId"])
    if digest(registered) != digest(plan):
        raise SafetyError("The movement executor did not receive the exact registered plan.")
    source.confirmed(context)
    ensure_unattempted(context, plan)
    grant_id = approvals.claim(context, grant, provisioning.binding(plan))
    provisioning.check_scope(context, plan, grant_id)
    provisioning.check_windows(plan)
    if plan["operation"] == "extract":
        checked_table(context, plan["spec"]["checkId"], fresh=True)
        claim_target(context, plan, grant_id)
        spec, selected = plan["spec"], settings(context)
        columns, _, rows = snapshot(
            context, plan["runId"], spec["table"], spec["queryTimeout"], spec["columns"], spec["maxRows"],
        )
        expected = data.signature(rows, columns, selected["F2"])
        content = data.encode(rows, columns, selected["E2"])
        decoded = data.decode(content, columns, selected["E2"], spec["maxRows"])
        if data.signature(decoded, columns, selected["F2"]) != expected:
            raise SafetyError("Parquet roundtrip changed values; no successful extract was accepted.")
        path = artifact_path(context, plan)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with path.open("xb") as output:
                output.write(content)
                output.flush()
                os.fsync(output.fileno())
        except FileExistsError:
            raise SafetyError("The immutable extraction file already exists; it was not overwritten.") from None
        artifact = {
            "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
            "rows": len(rows), "signature": expected, "etag": None,
        }
        return publish(context, plan, grant_id, "extracted", artifact)
    content = artifact_bytes(context, plan)
    before_write = lambda: claim_target(context, plan, grant_id)
    if plan["operation"] == "upload":
        etag = fabric.upload_parquet(context, plan, content, before_write)
        return publish(context, plan, grant_id, "uploaded", {**plan["artifact"], "etag": etag})
    operation_id, next_poll = fabric.load_parquet(context, plan, content, before_write)
    return publish(context, plan, grant_id, "pending", operation_id=operation_id, next_poll=next_poll)


def job(context, plan_id, states):
    from .plans import read_plan

    plan = read_plan(context, identifier(plan_id), allow_expired=True)
    with read_state(context) as connection:
        row = connection.execute(
            "SELECT hash,session,grant_id,operation_id,next_poll,state,receipt_id FROM data_steps "
            "WHERE project=? AND plan_id=?", (str(context.project), plan_id),
        ).fetchone()
    if (plan.get("kind") != KIND or plan["operation"] != "load" or row is None
            or row[0] != digest(plan) or row[5] not in states):
        raise SafetyError("No matching load stage in the required state exists for this project/configuration.")
    return plan, row


def load_status(context, plan_id):
    from . import fabric

    plan, row = job(context, plan_id, {"pending"})
    grant, operation, next_poll, _, previous_id = row[2:]
    if parse_time(next_poll) > utc_now():
        return publish(context, plan, grant, "pending", operation_id=operation, next_poll=next_poll, previous_id=previous_id)
    status, next_poll = fabric.load_parquet_status(context, plan, operation)
    return publish(context, plan, grant, status, operation_id=operation, next_poll=next_poll, previous_id=previous_id)


def reconcile(context, plan_id):
    from . import data, fabric

    plan, row = job(context, plan_id, {"loaded", "reconciled", "mismatch"})
    selected, spec = settings(context), plan["spec"]
    content = artifact_bytes(context, plan)
    expected = plan["artifact"]["signature"]
    decoded = data.decode(content, spec["columns"], selected["E2"], spec["maxRows"])
    if data.signature(decoded, spec["columns"], selected["F2"]) != expected:
        raise SafetyError("Parquet values differ from the private verified extraction manifest.")
    columns, _, fresh = snapshot(
        context, plan["runId"], spec["table"], spec["queryTimeout"], spec["columns"], spec["maxRows"],
    )
    observed_source = data.signature(fresh, columns, selected["F2"])
    target = fabric.read_loaded_table(context, plan)
    target_key_error = False
    try:
        data.check_keys(target, spec["keys"])
    except SafetyError:
        target_key_error = True
    observed_target = data.signature(target, columns, selected["F2"])
    source_diff = data.differences(expected, observed_source, "counts-profiles-hashes")
    target_diff = data.differences(expected, observed_target, selected["F1"])
    if target_key_error:
        target_diff.append("primary-key")
    passed = not source_diff and not target_diff
    report = {
        "observedAt": timestamp(), "comparisonPoint": "bronze", "checks": selected["F1"],
        "sourceConsistency": "nontransactional", "sourceDifferences": source_diff, "targetDifferences": target_diff,
        "expected": expected, "source": observed_source, "target": observed_target,
        "passed": passed, "valueHashesMatched": passed and selected["F1"] == "counts-profiles-hashes",
        "silverGoldValidated": False,
    }
    return publish(
        context, plan, row[2], "reconciled" if passed else "mismatch",
        operation_id=row[3], previous_id=row[6], report=report,
    )


def record(context, connection, plan, receipt, mode_name):
    from .state import consume_plan

    proof = receipt_proof(context, receipt.get("receiptId"))
    if (proof["receipt"] != receipt or (proof.get("session"), proof.get("instance")) != (context.session, host.instance())
            or (receipt.get("planId"), receipt.get("planHash"), receipt.get("runId"), receipt.get("stage"))
            != (plan["planId"], digest(plan), plan["runId"], plan["operation"])):
        raise SafetyError("Movement receipt differs from the private verified result/current execution instance.")
    attempt = load_json(provisioning.private_path(context, "movement-attempts", attempt_key(context, plan)))
    if attempt != {"planId": plan["planId"], "planHash": digest(plan), "grantId": receipt.get("grantId")}:
        raise SafetyError("Movement receipt has no matching exclusive target/run attempt.")
    if mode_name == "initial":
        expected = {"extract": "extracted", "upload": "uploaded", "load": "pending"}[plan["operation"]]
        if receipt["status"] != expected or receipt["previousReceiptId"] is not None:
            raise SafetyError("Invalid initial movement result.")
        provisioning.check_scope(context, plan, receipt["grantId"])
        approvals.finish(connection, context, receipt["grantId"], provisioning.binding(plan))
        consume_plan(connection, context, plan["planId"])
    else:
        current = connection.execute(
            "SELECT hash,session,grant_id,operation_id,state,receipt_id FROM data_steps WHERE project=? AND plan_id=?",
            (str(context.project), plan["planId"]),
        ).fetchone()
        allowed = {"pending"} if mode_name == "status" else {"loaded", "reconciled", "mismatch"}
        outcomes = {"pending", "loaded", "failed"} if mode_name == "status" else {"reconciled", "mismatch"}
        if (current is None or (current[0], current[2], current[3]) != (digest(plan), receipt["grantId"], receipt["operationId"])
                or current[4] not in allowed or current[5] != receipt["previousReceiptId"] or receipt["status"] not in outcomes):
            raise SafetyError("Polling/reconciliation cannot replay a stale receipt or rebind the approved operation.")
    if receipt["status"] == "pending":
        provisioning.uuid(receipt["operationId"])
        parse_time(receipt["nextPollAt"])
    connection.execute(
        "INSERT INTO data_steps VALUES(?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(project,plan_id) DO UPDATE SET "
        "session=excluded.session,next_poll=excluded.next_poll,state=excluded.state,receipt_id=excluded.receipt_id",
        (str(context.project), plan["planId"], digest(plan), context.session, plan["operation"], plan["runId"],
         receipt["grantId"], receipt["operationId"], receipt["nextPollAt"], receipt["status"], receipt["receiptId"]),
    )
