from datetime import timedelta
import json
from pathlib import Path
from uuid import uuid4

from .safety import (
    SafetyError, atomic_json, atomic_text, digest, identifier,
    load_json, parse_time, reject_secrets, timestamp, utc_now,
)


WRITE_COMMANDS = ("provision", "extract", "upload", "load", "deploy", "run", "translate", "convert", "generate")
PLAN_KEYS = {
    "contractVersion", "planId", "runId", "project", "createdAt", "expiresAt",
    "configHash", "operation", "actionClass", "summary", "identity",
    "workspaces", "objects", "estimatedRows", "reversible", "calls",
}
REQUEST_KEYS = {
    "runId", "summary", "identity", "workspaces", "objects", "estimatedRows",
    "reversible", "calls",
}


def validate_plan(plan, context, now=None, allow_expired=False):
    if isinstance(plan, dict) and plan.get("kind") == "data-movement":
        from .movement import validate_plan as validate_movement
        return validate_movement(plan, context, now, allow_expired)
    if isinstance(plan, dict) and plan.get("kind") == "fabric-provisioning":
        from .provisioning import validate_plan as validate_provisioning
        return validate_provisioning(plan, context, now, allow_expired)
    reject_secrets(plan)
    if not isinstance(plan, dict) or set(plan) != PLAN_KEYS or plan["contractVersion"] != "1.0":
        raise SafetyError("Invalid or unsupported plan contract.")
    identifier(plan["planId"])
    identifier(plan["runId"])
    if plan["project"] != str(context.project) or plan["configHash"] != context.config_hash():
        raise SafetyError("Plan project or configuration has changed.")
    now = now or utc_now()
    created, expires = parse_time(plan["createdAt"]), parse_time(plan["expiresAt"])
    if created > now or (expires <= now and not allow_expired) or not timedelta(0) < expires - created <= timedelta(minutes=60):
        raise SafetyError("Plan is expired, future-dated or exceeds the skeleton 60-minute maximum.")
    if plan["operation"] not in WRITE_COMMANDS:
        raise SafetyError("Unknown plan operation.")
    expected_class = "Execute" if plan["operation"] in ("load", "run") else "Write"
    if plan["actionClass"] != expected_class:
        raise SafetyError("Plan action class does not match the operation.")
    if not isinstance(plan["summary"], str) or not 1 <= len(plan["summary"]) <= 500:
        raise SafetyError("A bounded, nonempty plan summary is required.")
    identity = plan["identity"]
    if not isinstance(identity, dict) or set(identity) != {"windows", "fabricUser", "tenant"}:
        raise SafetyError("Plan must state the explicitly selected Windows and Fabric identities.")
    if any(not isinstance(value, str) or not value.strip() for value in identity.values()):
        raise SafetyError("Plan identity fields cannot be unset.")
    if not isinstance(plan["workspaces"], list) or not plan["workspaces"]:
        raise SafetyError("Plan must name its workspace fence.")
    workspace_ids = []
    for workspace in plan["workspaces"]:
        if not isinstance(workspace, dict) or set(workspace) != {"id", "name"}:
            raise SafetyError("Every workspace needs its ID and name.")
        if not all(isinstance(value, str) and value.strip() for value in workspace.values()):
            raise SafetyError("Workspace identities cannot be empty.")
        workspace_ids.append(workspace["id"])
    if len(set(workspace_ids)) != len(workspace_ids):
        raise SafetyError("Duplicate workspace IDs are prohibited.")
    if not isinstance(plan["objects"], list) or not all(isinstance(item, str) for item in plan["objects"]):
        raise SafetyError("Plan objects must be an explicit list.")
    if plan["estimatedRows"] is not None and (type(plan["estimatedRows"]) is not int or plan["estimatedRows"] < 0):
        raise SafetyError("Estimated rows must be nonnegative or explicitly unknown.")
    if type(plan["reversible"]) is not bool or not isinstance(plan["calls"], list) or not plan["calls"]:
        raise SafetyError("A reversibility choice and exact calls are required.")
    for call in plan["calls"]:
        if not isinstance(call, dict) or set(call) != {"workspaceId", "itemId", "operation", "description"}:
            raise SafetyError("Each call must state its workspace, item, operation and exact description.")
        if call["workspaceId"] not in workspace_ids or call["operation"] not in ("create", "update", "delete", "execute"):
            raise SafetyError("Call leaves the plan's workspace fence or uses an unknown operation.")
        if not isinstance(call["itemId"], str) or not isinstance(call["description"], str) or not call["description"].strip():
            raise SafetyError("Invalid call target or description.")
        if call["operation"] != "create" and not call["itemId"]:
            raise SafetyError("Existing-item calls must name the exact item ID.")
    if any(call["operation"] == "delete" for call in plan["calls"]) and len(plan["calls"]) != 1:
        raise SafetyError("Each delete requires its own separate plan and approval.")
    if any(call["operation"] == "delete" for call in plan["calls"]) and context.runtime_options().get("G4") == "never":
        raise SafetyError("G4 prohibits deletes, including delete previews.")
    if any(call["operation"] == "execute" for call in plan["calls"]) and plan["actionClass"] != "Execute":
        raise SafetyError("Executable calls cannot be mislabeled as a Write-only plan.")
    context.check_blind(str(plan))
    return plan


def plan_path(context, plan_id):
    return context.project_path(Path("plans") / f"{identifier(plan_id)}.json")


def read_plan(context, plan_id, allow_expired=False):
    return validate_plan(load_json(plan_path(context, plan_id)), context, allow_expired=allow_expired)


def create_plan(context, operation, request_path=None, answers=None):
    if answers is None:
        request = load_json(context.project_path(request_path))
    else:
        from .safety import unique_keys
        if not isinstance(answers, str) or len(answers) > 32768:
            raise SafetyError("Stage interview answers must be a bounded JSON object.")
        try:
            request = json.loads(answers, object_pairs_hook=unique_keys)
        except ValueError:
            raise SafetyError("Stage interview answers must be valid JSON.") from None
        reject_secrets(request)
        if not isinstance(request, dict) or request.get("kind") != "data-movement":
            raise SafetyError("Inline stage answers require the exact data-movement contract.")
    if isinstance(request, dict) and request.get("kind") == "data-movement":
        from .movement import draft
        return draft(context, operation, request)
    if operation == "provision" and isinstance(request, dict) and request.get("kind") == "fabric-provisioning":
        from .provisioning import draft
        return draft(context, request)
    if not isinstance(request, dict) or set(request) != REQUEST_KEYS:
        raise SafetyError("Invalid draft-plan request; all explicit choices must be present.")
    now = utc_now()
    plan = {
        "contractVersion": "1.0", "planId": str(uuid4()), "project": str(context.project),
        "createdAt": timestamp(now), "expiresAt": timestamp(now + timedelta(minutes=context.runtime_options().get("G3") or 60)),
        "configHash": context.config_hash(), "operation": operation,
        "actionClass": "Execute" if operation in ("load", "run") else "Write", **request,
    }
    validate_plan(plan, context, now=now + timedelta(milliseconds=1))
    path = plan_path(context, plan["planId"])
    if path.exists():
        raise SafetyError("Plan already exists.")
    atomic_json(path, plan)
    text = (
        f"# Draft {plan['actionClass']} plan\n\n{plan['summary']}\n\n"
        f"Plan: `{plan['planId']}`\n\nHash: `{digest(plan)}`\n\n"
        f"Expires: {plan['expiresAt']}\n\n"
        "Not approved. Live executors are disabled in this skeleton.\n\n"
        "```json\n" + json.dumps(plan, indent=2) + "\n```\n"
    )
    atomic_text(path.with_suffix(".md"), text)
    return plan
