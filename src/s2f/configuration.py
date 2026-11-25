import getpass
import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path
from uuid import uuid4

from .safety import (
    SafetyError, atomic_json, atomic_text, digest, file_hash, identifier,
    load_json, load_yaml, parse_time, reject_secrets, timestamp, unique_keys, utc_now,
)


def schema(context):
    return load_json(context.plugin / "schemas" / "config.schema.json")


def catalog(context, group=None):
    properties = schema(context)["properties"]["options"]["properties"]
    options = [
        {"id": key, "title": item["title"], "recommendation": item["x-recommendation"],
         "consequence": item["description"], "valueSchema": item}
        for key, item in properties.items() if not group or key.startswith(group)
    ]
    if group in (None, "A"):
        options.insert(6, {
            "id": "A7", "title": "Read prompts", "recommendation": "ask-every-time",
            "consequence": "Session-only; never saved in migration.config.yaml.",
            "choices": ["ask-every-time", "allow-this-session"], "sessionOnly": True,
        })
    return options


def template(context):
    keys = schema(context)["properties"]["options"]["properties"]
    return {"contractVersion": "1.0", "options": dict.fromkeys(keys)}


def validate(value, context, complete=False):
    from jsonschema import Draft202012Validator

    reject_secrets(value)
    error = next(Draft202012Validator(schema(context)).iter_errors(value), None)
    if error is not None:
        location = ".".join(map(str, error.absolute_path)) or "root"
        raise SafetyError(f"Configuration schema validation failed at {location}; no values were saved.")
    options = value["options"]
    missing = [key for key, answer in options.items() if answer is None]
    if complete and missing:
        raise SafetyError("Unanswered configuration options: " + ", ".join(missing))
    if options["A1"] and any(char in options["A1"] for char in ";\r\n"):
        raise SafetyError("A1 is a server name, not a connection string or statement.")
    encryption = options["A3"]
    if encryption and encryption["encrypt"] == "strict" and encryption["trustServerCertificate"]:
        raise SafetyError("Strict encryption ignores certificate trust; choose trust false explicitly.")
    workspaces = options["B2"]
    if workspaces:
        targets = workspaces["targets"]
        if any((target["id"] is None) != (workspaces["mode"] == "create") for target in targets):
            raise SafetyError("B2 existing workspaces need IDs; new workspaces cannot claim existing IDs.")
        if workspaces["layout"] == "single" and (len(targets) != 1 or targets[0]["layer"] != "all"):
            raise SafetyError("A single workspace requires exactly one all-layer target.")
        if workspaces["layout"] == "per-layer" and (
            {target["layer"] for target in targets} != {"bronze", "silver", "gold"} or len(targets) != 3
        ):
            raise SafetyError("Per-layer workspaces require one explicit target for each layer.")
        for field in ("name", "id"):
            values = [target[field] for target in targets if target[field] is not None]
            if len(set(values)) != len(values):
                raise SafetyError("B2 workspace identities must be unique.")
    layout, naming = options["B3"], options["B6"]
    if layout and bool(layout["mapping"]) != (layout["layout"] == "C"):
        raise SafetyError("Only custom layout C requires an explicit mapping.")
    if naming and (
        bool(naming["prefix"]) != (naming["mode"] == "prefix")
        or bool(naming["mapping"]) != (naming["mode"] == "custom")
    ):
        raise SafetyError("B6 must supply only the prefix or mapping selected by its mode.")
    scope = options["C2"]
    if scope and bool(scope["include"]) != (scope["mode"] != "all"):
        raise SafetyError("C2 schemas/list require inclusions; all does not.")
    for data in (options["C3"] or {}).values():
        if (bool(data["predicate"]) != (data["mode"] == "filtered")
                or (data["rows"] is not None) != (data["mode"] == "sample")):
            raise SafetyError("C3 filtered data needs only a predicate; samples need only a row limit.")
    waves = options["C4"]
    if waves:
        names = [wave["name"] for wave in waves]
        objects = [item for wave in waves for item in wave["objects"]]
        if len(set(names)) != len(names) or len(set(objects)) != len(objects):
            raise SafetyError("C4 wave names and assigned objects must be unique.")
    mode = options["D4"]
    if mode and bool(mode["watermarks"]) != (mode["mode"] == "incremental"):
        raise SafetyError("D4 incremental mode requires explicit watermark columns.")
    for conversion in [options, *value.get("overrides", {}).values()]:
        sizing = conversion.get("E1")
        if sizing and (sizing["headroom"] is not None) != (sizing["mode"] == "measured"):
            raise SafetyError("E1 measured sizing requires explicit headroom; other modes do not.")
        if conversion.get("E3"):
            context.project_path(conversion["E3"])
    tolerance = options["F3"]
    if tolerance and (tolerance["value"] is not None) != (tolerance["mode"] == "numeric"):
        raise SafetyError("F3 numeric tolerance requires a value; exact does not.")
    if options["G2"]:
        target = context.project_path(options["G2"])
        if target == context.project or target.relative_to(context.project).parts[0].casefold() in {
            ".git", ".github", "plans", "config-plans", "migration.config.yaml", ".blind-paths",
        } or target.relative_to(context.project).parts[0].casefold().startswith(".env"):
            raise SafetyError("G2 must be a dedicated project-contained run folder.")
    for entry in options["G1"] or []:
        name = entry.strip().rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1]
        if not name or name in (".", "..") or re.fullmatch(r"[A-Za-z]:", name) or entry.lstrip().startswith("#"):
            raise SafetyError("G1 entries must identify actual paths or folder names, not roots/comments.")
    return missing


def read(context, path=None):
    value = load_yaml(context.project_path(path or "migration.config.yaml"))
    validate(value, context)
    return value


def check_locks(context, value, connection=None):
    from .state import read_state

    if connection is None:
        if not context.registry.exists():
            return
        with read_state(context) as state:
            return check_locks(context, value, state)
    # Older skeleton registries predate this optional authority table.
    if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='configuration_locks'").fetchone():
        return
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='fabric_jobs'").fetchone() and connection.execute(
        "SELECT 1 FROM fabric_jobs WHERE project=? AND state='pending'", (str(context.project),),
    ).fetchone() and value != read(context):
        raise SafetyError("Complete pending Fabric provisioning before changing configuration.")
    if connection.execute("SELECT 1 FROM sqlite_master WHERE name='data_steps'").fetchone() and connection.execute(
        "SELECT 1 FROM data_steps WHERE project=? AND state='pending'", (str(context.project),),
    ).fetchone() and value != read(context):
        raise SafetyError("Complete the pending Fabric load before changing configuration.")
    for key, expected, reason in connection.execute(
        "SELECT option_id,value_hash,reason FROM configuration_locks WHERE project=?", (str(context.project),),
    ):
        if digest(value["options"][key]) != expected:
            raise SafetyError(f"{key} is locked after application: {reason}")


def changes(context, value):
    current = context.project_path("migration.config.yaml")
    old = read(context) if current.exists() else template(context)
    result = [key for key in value["options"] if value["options"][key] != old["options"][key]]
    if value.get("overrides", {}) != old.get("overrides", {}):
        result.append("overrides")
    return result


def render(value):
    import yaml
    return yaml.safe_dump(value, sort_keys=False, allow_unicode=False)


def payload_hash(value):
    return hashlib.sha256(render(value).encode("utf-8")).hexdigest()


def plan_path(context, plan_id):
    return context.project_path(Path("config-plans") / f"{identifier(plan_id)}.json")


def answers(context, text):
    if not isinstance(text, str) or len(text) > 32768:
        raise SafetyError("Interview answers must be a bounded JSON object of explicit option IDs.")
    try:
        selected = json.loads(
            text, object_pairs_hook=unique_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except ValueError:
        raise SafetyError("Interview answers must be valid JSON with unique option IDs.") from None
    if not isinstance(selected, dict) or not selected:
        raise SafetyError("Interview answers must name at least one explicit option ID.")
    value = read(context) if (context.project / "migration.config.yaml").exists() else template(context)
    if any(key not in value["options"] for key in selected):
        raise SafetyError("Unknown or session-only option in interview answers.")
    value["options"].update(selected)
    validate(value, context)
    return value


def draft(context, input_path, value=None):
    value = read(context, input_path) if value is None else value
    validate(value, context)
    check_locks(context, value)
    target = context.project_path("migration.config.yaml")
    now = utc_now()
    plan = {
        "contractVersion": "1.0", "kind": "configuration", "planId": str(uuid4()),
        "runId": "session", "project": str(context.project), "actionClass": "Write",
        "createdAt": timestamp(now), "expiresAt": timestamp(now + timedelta(minutes=value["options"]["G3"] or 60)),
        "baseHash": file_hash(target) if target.exists() else None,
        "payloadHash": payload_hash(value), "payload": value,
        "changedOptions": changes(context, value), "target": "migration.config.yaml",
        "identityHint": getpass.getuser(), "objects": ["migration.config.yaml"],
        "estimatedRows": 0, "reversible": False,
        "calls": [{"operation": "atomic-replace", "path": "migration.config.yaml", "contentSha256": payload_hash(value)}],
    }
    atomic_json(plan_path(context, plan["planId"]), plan)
    atomic_text(
        plan_path(context, plan["planId"]).with_suffix(".md"),
        "# Configuration Write plan\n\n"
        f"Plan `{plan['planId']}`; SHA256 `{digest(plan)}`.\n\n"
        f"Changed options: {', '.join(plan['changedOptions']) or 'none'}.\n\n"
        f"Windows login hint: `{plan['identityHint']}` (not independently confirmed).\n\n"
        "Exact call: atomically replace project migration.config.yaml with the embedded YAML payload. "
        "One local file; zero affected SQL rows and zero Fabric calls. No automatic undo; "
        "reverting requires a fresh approved save. Native approval is required; this preview is not approval.\n\n"
        "```yaml\n" + render(value) + "```\n",
    )
    return plan


def read_plan(context, plan_id, check_base=True):
    plan = load_json(plan_path(context, plan_id))
    keys = {
        "contractVersion","kind","planId","runId","project","actionClass","createdAt","expiresAt",
        "baseHash","payloadHash","payload","changedOptions","target",
        "identityHint","objects","estimatedRows","reversible","calls",
    }
    if not isinstance(plan, dict) or set(plan) != keys or (
        plan["contractVersion"], plan["kind"], plan["project"], plan["actionClass"], plan["target"], plan["runId"]
    ) != ("1.0", "configuration", str(context.project), "Write", "migration.config.yaml", "session"):
        raise SafetyError("Invalid configuration plan contract.")
    if plan["planId"] != identifier(plan_id):
        raise SafetyError("Configuration plan ID mismatch.")
    if (not isinstance(plan["identityHint"], str) or not plan["identityHint"].strip()
            or plan["objects"] != ["migration.config.yaml"] or type(plan["estimatedRows"]) is not int
            or plan["estimatedRows"] != 0 or plan["reversible"] is not False
            or plan["calls"] != [{"operation": "atomic-replace", "path": "migration.config.yaml", "contentSha256": plan["payloadHash"]}]):
        raise SafetyError("Configuration plan effects do not match the fixed local-only operation.")
    if (not isinstance(plan["changedOptions"], list)
            or any(not isinstance(item, str) or item not in {*template(context)["options"], "overrides"} for item in plan["changedOptions"])
            or plan["baseHash"] is not None and not (
                isinstance(plan["baseHash"], str) and re.fullmatch("[a-f0-9]{64}", plan["baseHash"])
            )):
        raise SafetyError("Invalid configuration plan metadata.")
    created, expires = parse_time(plan["createdAt"]), parse_time(plan["expiresAt"])
    if created > utc_now() or expires <= utc_now() or not timedelta(0) < expires - created <= timedelta(minutes=60):
        raise SafetyError("Configuration plan is expired or has invalid timestamps.")
    validate(plan["payload"], context)
    check_locks(context, plan["payload"])
    if payload_hash(plan["payload"]) != plan["payloadHash"]:
        raise SafetyError("Configuration payload hash mismatch.")
    target = context.project_path("migration.config.yaml")
    if check_base and plan["baseHash"] != (file_hash(target) if target.exists() else None):
        raise SafetyError("Configuration changed since this plan was drafted; draft a fresh plan.")
    return plan


def approved_workspaces(value, connection=None, project=None):
    choice = value["options"]["B2"]
    if choice and choice["mode"] == "existing":
        return [{"id": target["id"], "name": target["name"]} for target in choice["targets"]]
    capacity = value["options"]["B1"]
    if not choice or connection is None or project is None or not capacity:
        return []
    names = {target["name"] for target in choice["targets"]}
    return [
        {"id": workspace, "name": name}
        for workspace, name, actual_capacity, tenant in connection.execute(
            "SELECT id,name,capacity,tenant FROM sandbox_workspaces WHERE project=? ORDER BY name", (str(project),),
        )
        if name in names and actual_capacity == capacity["id"] and tenant == value["options"]["A6"]
    ]


def registered(context, connection, plan_id, check_base=True):
    plan = read_plan(context, plan_id, check_base=check_base)
    row = connection.execute(
        "SELECT hash,session,state FROM configuration_plans WHERE project=? AND plan_id=?",
        (str(context.project), plan_id),
    ).fetchone()
    if row != (digest(plan), context.session, "registered"):
        raise SafetyError("Configuration plan is missing, tampered, used or belongs to another session.")
    return plan
