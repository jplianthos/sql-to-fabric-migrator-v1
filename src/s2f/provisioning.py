import getpass
import json
import re
from datetime import timedelta
from uuid import UUID, uuid4

from . import approvals, configuration, host
from .safety import (
    SafetyError, atomic_json, atomic_text, digest, identifier, load_json,
    parse_time, reject_links, timestamp, utc_now,
)


KIND = "fabric-provisioning"
REQUEST_KEYS = {"kind", "runId", "resourceType", "displayName", "workspaceId", "subscription", "identity"}
EXTRA_KEYS = {"kind", "resourceType", "subscription", "reads"}
COLLECTIONS = {"Lakehouse": "lakehouses", "Warehouse": "warehouses"}


def uuid(value):
    try:
        normalized = str(UUID(value))
    except (ValueError, TypeError, AttributeError):
        raise SafetyError("Provisioning requires explicit UUIDs, not names, URLs or inferred IDs.") from None
    if normalized != value or normalized == str(UUID(int=0)):
        raise SafetyError("Provisioning IDs must be canonical, nonzero UUIDs.")
    return normalized


def private_path(context, area, name):
    path = context.data / area / identifier(name)
    reject_links(path)
    return path


def binding(plan):
    return "fabric:" + plan["planId"] + ":" + digest(plan)


def scope_path(context, plan):
    return private_path(context, "fabric-scopes", digest({
        "project": str(context.project), "session": context.session, "planId": plan["planId"],
    }))


def attest_scope(context, plan, grant):
    atomic_json(scope_path(context, plan), {
        "planHash": digest(plan), "grantId": grant.split(".")[0], "instance": host.instance(),
    })


def check_scope(context, plan, grant_id):
    proof = load_json(scope_path(context, plan))
    if proof != {"planHash": digest(plan), "grantId": grant_id, "instance": host.instance()}:
        raise SafetyError("The native grant has no current selected migration-lead scoped-hook attestation.")


def options(context, connection):
    row = connection.execute(
        "SELECT hash FROM configuration WHERE project=?", (str(context.project),),
    ).fetchone()
    if row != (context.config_hash(),):
        raise SafetyError("Provisioning requires the exact native-approved configuration.")
    value = configuration.read(context)
    configuration.check_locks(context, value, connection)
    selected = value["options"]
    if selected["A5"] != "azure-cli" or selected["B7"] != "live-with-approval":
        raise SafetyError("Sandbox writes require explicit azure-cli sign-in and live-with-approval deployment mode.")
    if not selected["A6"] or not selected["B1"] or not selected["B2"]:
        raise SafetyError("Choose the tenant, capacity and new workspace names before provisioning.")
    uuid(selected["A6"])
    uuid(selected["B1"]["id"])
    if selected["B2"]["mode"] != "create":
        raise SafetyError("This executor provisions only new, project-created sandbox workspaces.")
    return selected


def effects(context, request, connection):
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS or request["kind"] != KIND:
        raise SafetyError("Invalid sandbox provisioning request contract.")
    selected = options(context, connection)
    kind, name = request["resourceType"], request["displayName"]
    if not isinstance(kind, str) or kind not in {"Workspace", *COLLECTIONS}:
        raise SafetyError("Only new Workspace, Lakehouse and Warehouse resources can be provisioned.")
    if (not isinstance(name, str) or not name.strip() or name != name.strip()
            or len(name) > 256 or any(ord(char) < 32 for char in name)):
        raise SafetyError("A bounded, explicit resource display name is required.")
    if kind != "Workspace" and not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name):
        raise SafetyError("Sandbox item names must use 1-128 ASCII letters, digits or underscores, starting with a letter/underscore.")
    identity = request["identity"]
    if (not isinstance(identity, dict) or set(identity) != {"windows", "fabricUser", "tenant"}
            or any(not isinstance(value, str) or not value.strip() for value in identity.values())
            or identity["tenant"] != selected["A6"]):
        raise SafetyError("Explicit Windows/Fabric user identities must match the configured tenant.")
    uuid(request["subscription"])
    body = {"displayName": name}
    targets = selected["B2"]["targets"]
    if kind == "Workspace":
        if request["workspaceId"] is not None or not any(target["name"] == name for target in targets):
            raise SafetyError("The new workspace name must be an explicit B2 target; existing IDs cannot be adopted.")
        workspace = {"id": "new", "name": name}
        path = "/v1/workspaces"
        body["capacityId"] = selected["B1"]["id"]
        reads = ["/v1/capacities", "/v1/workspaces/{created-id}"]
    else:
        workspace_id = uuid(request["workspaceId"])
        row = connection.execute(
            "SELECT name,capacity,tenant FROM sandbox_workspaces WHERE project=? AND id=?",
            (str(context.project), workspace_id),
        ).fetchone()
        if row is None or row[1:] != (selected["B1"]["id"], selected["A6"]) or not any(
            target["name"] == row[0] for target in targets
        ):
            raise SafetyError("Item creation requires a verified workspace created by this project inside the configured fence.")
        workspace = {"id": workspace_id, "name": row[0]}
        path = f"/v1/workspaces/{workspace_id}/{COLLECTIONS[kind]}"
        reads = [f"/v1/workspaces/{workspace_id}", path + "/{created-id}"]
        if kind == "Lakehouse":
            if selected["B5"] is None:
                raise SafetyError("Choose B5 explicitly before creating a Lakehouse.")
            body["creationPayload"] = {"enableSchemas": selected["B5"] == "enabled"}
        else:
            if selected["B4"] is None:
                raise SafetyError("Choose immutable Warehouse collation B4 before creating a Warehouse.")
            body["creationPayload"] = {"collationType": selected["B4"]}
    reads.extend(("/v1/operations/{operation-id}", "/v1/operations/{operation-id}/result"))
    return {
        "summary": f"Create one new {kind}: {name}",
        "workspaces": [workspace], "objects": [name], "estimatedRows": 0, "reversible": False,
        "calls": [{
            "workspaceId": workspace["id"], "itemId": "", "operation": "create",
            "description": f"Create new {kind}; no SQL, updates, deletes, adoption or automatic undo.",
            "method": "POST", "path": path, "body": body,
        }],
        "reads": reads,
    }


def draft(context, request):
    from .plans import plan_path
    from .state import read_state

    with read_state(context) as connection:
        exact = effects(context, request, connection)
    now = utc_now()
    plan = {
        "contractVersion": "1.0", "kind": KIND, "planId": str(uuid4()), "runId": request["runId"],
        "project": str(context.project), "createdAt": timestamp(now),
        "expiresAt": timestamp(now + timedelta(minutes=context.runtime_options().get("G3") or 60)),
        "configHash": context.config_hash(), "operation": "provision", "actionClass": "Write",
        "identity": request["identity"], "resourceType": request["resourceType"],
        "subscription": request["subscription"], **exact,
    }
    validate_plan(plan, context)
    path = plan_path(context, plan["planId"])
    atomic_json(path, plan)
    atomic_text(
        path.with_suffix(".md"),
        f"# Fabric sandbox Write plan\n\n{plan['summary']}\n\n"
        f"Plan `{plan['planId']}`; SHA256 `{digest(plan)}`; expires {plan['expiresAt']}.\n\n"
        "NOT approved. Exactly one POST, plus the listed identity/preflight/verification/LRO reads. "
        "Creation may consume Fabric capacity. No permission grants, source queries or automatic cleanup. "
        "Only the selected migration-lead can obtain a one-use native-hook grant. "
        "Do not repeat creation after an uncertain result; inspect Fabric and retained private attempt evidence.\n\n"
        "```json\n" + json.dumps(plan, indent=2) + "\n```\n",
    )
    return plan


def validate_plan(plan, context, now=None, allow_expired=False):
    from .plans import PLAN_KEYS, validate_plan as validate_legacy
    from .state import read_state

    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS | EXTRA_KEYS or plan.get("kind") != KIND:
        raise SafetyError("Invalid executable sandbox plan contract.")
    if plan["operation"] != "provision":
        raise SafetyError("Only provisioning has an executable Fabric contract.")
    legacy = {key: value for key, value in plan.items() if key in PLAN_KEYS}
    if not isinstance(plan["calls"], list) or len(plan["calls"]) != 1 or not isinstance(plan["calls"][0], dict):
        raise SafetyError("Each provisioning approval authorizes exactly one create call.")
    call = plan["calls"][0]
    legacy["calls"] = [{key: value for key, value in call.items() if key not in {"method", "path", "body"}}]
    validate_legacy(legacy, context, now, allow_expired)
    if not isinstance(call.get("body"), dict):
        raise SafetyError("An exact, structured POST body is required.")
    request = {
        "kind": KIND, "runId": plan["runId"], "resourceType": plan["resourceType"],
        "displayName": call["body"].get("displayName"),
        "workspaceId": None if plan["resourceType"] == "Workspace" else call["workspaceId"],
        "subscription": plan["subscription"], "identity": plan["identity"],
    }
    with read_state(context) as connection:
        expected = effects(context, request, connection)
    if any(plan[key] != value for key, value in expected.items()):
        raise SafetyError("Executable calls, bodies, reads or workspace fence differ from the approved configuration.")
    return plan


def attempt_key(context, plan):
    call = plan["calls"][0]
    return digest({
        "project": str(context.project), "tenant": plan["identity"]["tenant"],
        "capacity": context.runtime_options()["B1"]["id"], "path": call["path"],
        "type": plan["resourceType"], "name": call["body"]["displayName"],
    })


def ensure_unattempted(context, plan):
    from .state import read_state

    if plan["resourceType"] == "Workspace":
        with read_state(context) as connection:
            if connection.execute(
                "SELECT 1 FROM sandbox_workspaces WHERE project=? AND name=?",
                (str(context.project), plan["objects"][0]),
            ).fetchone():
                raise SafetyError("This project already created that workspace; do not recreate or adopt it.")
    if private_path(context, "fabric-attempts", attempt_key(context, plan)).exists():
        raise SafetyError("Creation of this target was already attempted. Do not retry or adopt it; inspect Fabric/private attempt evidence.")


def claim_target(context, plan, grant_id):
    path = private_path(context, "fabric-attempts", attempt_key(context, plan))
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as output:
            json.dump({"planId": plan["planId"], "planHash": digest(plan), "grantId": grant_id}, output)
    except FileExistsError:
        raise SafetyError("A concurrent or previous creation attempt already claimed this target; no POST was repeated.") from None


def publish(context, plan, grant_id, status, resource=None, operation_id=None, next_poll=None, previous=None):
    receipt = {
        "contractVersion": "1.0", "kind": KIND, "status": status, "planId": plan["planId"],
        "planHash": digest(plan), "runId": plan["runId"], "grantId": grant_id,
        "receiptId": str(uuid4()), "resource": resource, "operationId": operation_id, "nextPollAt": next_poll,
        "previousReceiptId": previous,
    }
    path = private_path(context, "fabric-receipts", receipt["receiptId"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as output:
        json.dump(receipt, output, allow_nan=False)
    return receipt


def job(context, connection, plan_id):
    from .plans import read_plan

    plan = read_plan(context, identifier(plan_id), allow_expired=True)
    row = connection.execute(
        "SELECT hash,session,grant_id,operation_id,next_poll,state,receipt_id FROM fabric_jobs WHERE project=? AND plan_id=?",
        (str(context.project), plan_id),
    ).fetchone()
    if plan.get("kind") != KIND or row is None or row[:2] != (digest(plan), context.session) or row[5] != "pending":
        raise SafetyError("No matching pending provisioning operation exists for this project/session/configuration.")
    return plan, row


def record(context, connection, plan, receipt, initial):
    from .state import consume_plan, record_created_item

    proof = load_json(private_path(context, "fabric-receipts", identifier(receipt.get("receiptId"))))
    if proof != receipt or receipt.get("kind") != KIND or (
        receipt.get("planId"), receipt.get("planHash"), receipt.get("runId")
    ) != (plan["planId"], digest(plan), plan["runId"]):
        raise SafetyError("Fabric receipt does not match the private verified transport result.")
    attempt = load_json(private_path(context, "fabric-attempts", attempt_key(context, plan)))
    if attempt != {"planId": plan["planId"], "planHash": digest(plan), "grantId": receipt.get("grantId")}:
        raise SafetyError("Fabric receipt has no matching exclusive creation attempt.")
    if initial:
        if receipt.get("previousReceiptId") is not None:
            raise SafetyError("An initial create cannot claim a previous polling result.")
        check_scope(context, plan, receipt.get("grantId"))
        approvals.finish(connection, context, receipt.get("grantId"), binding(plan))
        consume_plan(connection, context, plan["planId"])
    else:
        _, row = job(context, connection, plan["planId"])
        if (row[2], row[3], row[6]) != (
            receipt.get("grantId"), receipt.get("operationId"), receipt.get("previousReceiptId"),
        ):
            raise SafetyError("Polling cannot rebind the approved operation/grant or replay a stale polling result.")
    status = receipt.get("status")
    if status not in {"pending", "created", "failed"}:
        raise SafetyError("Unsupported provisioning outcome; ownership was not registered.")
    if status == "pending":
        uuid(receipt["operationId"])
        parse_time(receipt["nextPollAt"])
        if receipt["resource"] is not None:
            raise SafetyError("A pending operation cannot confer ownership.")
    elif status == "created":
        resource = receipt["resource"]
        from .fabric import validate_created
        validate_created(plan, resource, context.runtime_options()["B1"]["id"])
        if plan["resourceType"] == "Workspace":
            connection.execute(
                "INSERT INTO sandbox_workspaces VALUES(?,?,?,?,?,?)",
                (str(context.project), resource["id"], resource["displayName"], resource["capacityId"],
                 plan["identity"]["tenant"], plan["planId"]),
            )
            fence = configuration.approved_workspaces(configuration.read(context), connection, context.project)
            connection.execute(
                "UPDATE configuration SET workspaces=? WHERE project=? AND hash=?",
                (json.dumps(fence), str(context.project), plan["configHash"]),
            )
        else:
            record_created_item(
                connection, context, plan["planId"], resource["workspaceId"], resource["id"], allow_expired=not initial,
            )
            connection.execute("INSERT INTO owned_items VALUES(?,?,?,?,?,?)", (
                str(context.project), resource["workspaceId"], resource["id"], plan["resourceType"],
                resource["displayName"], int(context.runtime_options()["B5"] == "enabled")
                if plan["resourceType"] == "Lakehouse" else None,
            ))
            if plan["resourceType"] == "Warehouse":
                connection.execute("INSERT OR REPLACE INTO configuration_locks VALUES(?,?,?,?)", (
                    str(context.project), "B4", digest(context.runtime_options()["B4"]),
                    "Verified Warehouse creation fixed its immutable collation.",
                ))
    connection.execute(
        "INSERT INTO fabric_jobs VALUES(?,?,?,?,?,?,?,?,?) ON CONFLICT(project,plan_id) DO UPDATE SET "
        "next_poll=excluded.next_poll,state=excluded.state,receipt_id=excluded.receipt_id",
        (str(context.project), plan["planId"], digest(plan), context.session, receipt["grantId"],
         receipt["operationId"], receipt["nextPollAt"], status, receipt["receiptId"]),
    )


def check_windows(plan):
    if plan["identity"]["windows"].casefold() != getpass.getuser().casefold():
        raise SafetyError("The planned Windows login does not match this process; source authentication remains unconfirmed/disabled.")
