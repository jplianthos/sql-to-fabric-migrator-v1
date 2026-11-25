import contextlib
import getpass
import io
import json
import os
import re
import sqlite3
import sys
from pathlib import Path

from . import approvals, configuration, host, movement, provisioning, source
from .cli import parser
from .plans import WRITE_COMMANDS, read_plan
from .safety import (
    Context, SafetyError, atomic_json, contained, digest, identifier,
    file_hash, load_json, reject_links, reject_secrets, strings, unique_keys,
)
from .state import hook_state, read_state, record_event, register_plan, validate_registered


SAFE_TOOLS = {"ask_user", "skill", "update_todo"}
READ_TOOLS = {"view", "rg", "grep", "glob"}
WRITE_TOOLS = {"create", "edit", "apply_patch", "str_replace_editor"}
MCP_READS = {"core_list-workspaces", "core_get-workspace", "core_list-capacities"}
PROTECTED = {
    ".git", ".github", ".blind-paths", ".env", "migration.config.yaml",
    "plans", "config-plans", "approvals.jsonl", "ledger.jsonl",
    "source-queries.jsonl", "inventory.json", "movement",
}


def decision(value, reason):
    return {"permissionDecision": value, "permissionDecisionReason": reason}


def context_from_request(request):
    if not isinstance(request, dict):
        raise SafetyError("Expected a hook payload object.")
    plugin, data = os.environ.get("COPILOT_PLUGIN_ROOT"), os.environ.get("COPILOT_PLUGIN_DATA")
    if not plugin or not data or not request.get("cwd"):
        raise SafetyError("Missing host plugin environment or project cwd.")
    return Context.create(Path(request["cwd"]), Path(plugin), Path(data), request.get("sessionId"))


def shell_arguments(command, context):
    if not isinstance(command, str) or "\n" in command or "\r" in command:
        raise SafetyError("Shell calls must be one canonical s2f wrapper invocation.")
    command = command.strip()
    if not command.startswith("& "):
        raise SafetyError("Direct shell/source/cloud access is prohibited; use the canonical s2f wrapper.")
    command = command[2:].strip()
    tokens = []
    offset = 0
    for match in re.finditer(r"'([^']*)'|([A-Za-z0-9_:\\./-]+)", command):
        separator = command[offset:match.start()]
        if separator.strip() or (tokens and not separator):
            raise SafetyError("Shell operators, interpolation and compound commands are prohibited.")
        token = match[1] if match[1] is not None else match[2]
        if any(character in token for character in ("\x00", "\n", "\r")):
            raise SafetyError("Invalid shell argument.")
        tokens.append(token)
        offset = match.end()
    if command[offset:].strip() or not tokens:
        raise SafetyError("Invalid shell invocation.")
    expected = context.plugin / "scripts" / "s2f.ps1"
    if Path(tokens[0]).resolve() != expected.resolve():
        raise SafetyError("Only the installed s2f wrapper may run.")
    with contextlib.redirect_stderr(io.StringIO()):
        try:
            args = parser().parse_args(tokens[1:])
        except SystemExit:
            raise SafetyError("The wrapper command does not match the s2f command contract.") from None
    if (args.project.resolve() != context.project or args.data.resolve() != context.data
            or args.session != context.session):
        raise SafetyError("Command project, state or session does not match the host session.")
    return args


def protected_arguments(context, arguments):
    text = "\n".join(strings(arguments))
    context.check_blind(text)
    reject_secrets(arguments)
    normalized = text.replace("/", "\\").casefold()
    if "registry.sqlite3" in normalized or str(context.data).casefold() in normalized:
        raise SafetyError("Only hook-mediated s2f calls may access plugin state.")
    return text


def tool_paths(arguments):
    paths = []
    if isinstance(arguments, str):
        arguments = {"patch": arguments}
    if not isinstance(arguments, dict):
        raise SafetyError("File tools need structured path arguments.")
    for key in ("path", "paths", "file_path", "filePath", "filename"):
        value = arguments.get(key)
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, list) and all(isinstance(item, str) for item in value):
            paths.extend(value)
    patch = arguments.get("patch") or arguments.get("input")
    if isinstance(patch, str):
        paths.extend(re.findall(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", patch, re.MULTILINE))
    if not 1 <= len(paths) <= 64:
        raise SafetyError("Between one and 64 explicit file paths are required.")
    return paths


def ask_with_grant(request, context, binding, reason, expires=None, scoped_plan=None):
    with hook_state(context) as connection:
        grant = approvals.mint(connection, context, binding, expires)
        if scoped_plan is not None:
            provisioning.attest_scope(context, scoped_plan, grant)
    return {
        **decision("ask", reason),
        "modifiedArgs": {
            **request["toolArgs"],
            "command": request["toolArgs"]["command"] + " --native-grant '" + grant + "'",
        },
    }


def public_command(request):
    return re.sub(
        r"\s+--native-grant(?:=|\s+)(?:'[^']*'|[A-Za-z0-9._-]+)",
        "", request["toolArgs"]["command"],
    )


def read_decision(context, reason):
    return decision("allow" if approvals.reads_allowed(context) else "ask", reason)


def pre_tool(request, context, lead_scope=False):
    tool, arguments = request.get("toolName"), request.get("toolArgs")
    if not isinstance(tool, str) or arguments is None:
        raise SafetyError("Missing tool name or arguments.")
    # The exact wrapper is the sole exception to direct state/install path references.
    if tool == "powershell":
        if not isinstance(arguments, dict) or set(arguments) - {"command", "description", "initial_wait", "mode", "shellId"}:
            raise SafetyError("Unsupported shell options; detached or alternate execution is prohibited.")
        if arguments.get("mode", "sync") != "sync":
            raise SafetyError("Background s2f execution is not supported.")
        context.check_blind(str(arguments))
        reject_secrets(arguments)
        args = shell_arguments(arguments.get("command"), context)
        if getattr(args, "native_grant", None) is not None:
            raise SafetyError("Internal grants cannot be supplied by callers; only the timely native pre-hook may inject one.")
        role = request.get("agentType", "").rsplit(":", 1)[-1]
        if role in ("tsql-converter", "code-reviewer"):
            raise SafetyError("This agent cannot run shell commands in the skeleton.")
        if args.command == "config" and args.verb in ("validate", "diff", "save"):
            if not getattr(args, "answers", None):
                context.project_path(getattr(args, "input", None) or "migration.config.yaml")
        if args.command == "config" and args.verb == "save":
            return {}
        if args.command == "config" and args.verb == "apply":
            if role in ("estate-analyst", "validator"):
                raise SafetyError("This agent has read-only boundaries.")
            with read_state(context) as connection:
                plan = configuration.registered(context, connection, identifier(args.plan_id))
            return ask_with_grant(
                request, context, "configuration:" + plan["planId"] + ":" + digest(plan),
                "Write local migration.config.yaml | plan " + plan["planId"] + " | SHA256 " + digest(plan)
                + " | changed: " + (", ".join(plan["changedOptions"]) or "none")
                + " | Windows login hint (unconfirmed): " + plan["identityHint"]
                + ". Inspect config-plans/" + plan["planId"] + ".md. No SQL or Fabric calls.",
                plan["expiresAt"],
            )
        if args.command == "approvals" and args.verb != "status":
            if role in ("estate-analyst", "validator"):
                raise SafetyError("This agent cannot change session permission settings.")
            return ask_with_grant(
                request, context, "reads:" + args.verb,
                "Allow bounded admitted Reads for THIS session only; Writes/Execute still prompt."
                if args.verb == "allow" else "Restore ask-every-time Reads for this session.",
            )
        if args.command == "source-confirm":
            if role not in ("", "migration-lead"):
                raise SafetyError("This agent cannot confirm source identity for the session.")
            value = source.checked(context, identifier(args.check_id))
            identity = value["receipt"]["identity"]
            return ask_with_grant(
                request, context, source.binding(value),
                "Confirm this session's Windows/source identity " + identity["sourceUser"]
                + " | engine: " + identity["engineVersion"] + " " + identity["edition"]
                + " | sysadmin: " + str(identity["isSysadmin"])
                + " | source check " + args.check_id
                + ". Confirms source identity, not extraction approval. Source data movement needs separate exact plans/native grants; no source writes or modules.",
                value["expiresAt"],
            )
        if args.command in ("source-check", "discover"):
            if role not in ("", "migration-lead", "estate-analyst", "validator"):
                raise SafetyError("This agent cannot access the source catalog.")
            selected = source.options(context)
            if args.command == "discover":
                source.confirmed(context)
            return read_decision(
                context, "Read fixed SQL2016 source " + ("identity in master" if args.command == "source-check"
                    else "catalogs in approved C1 " + json.dumps(selected["C1"]))
                + ". Query timeout " + str(args.query_timeout) + " seconds. No data rows, modules or writes.",
            )
        if args.command == "table-check":
            movement.settings(context)
            source.confirmed(context)
            if role not in ("", "migration-lead", "estate-analyst", "validator"):
                raise SafetyError("This agent cannot inspect the source table.")
            return read_decision(
                context, "Read metadata/primary-key readiness for the exact requested source table. "
                "No data rows, modules or Fabric writes; extraction requires a separate exact Write plan.",
            )
        if args.command in ("load-status", "reconcile"):
            movement.job(context, identifier(args.plan_id), {"pending"} if args.command == "load-status"
                         else {"loaded", "reconciled", "mismatch"})
            return read_decision(
                context, "Read the approved load operation status and target metadata only; never repeat its POST."
                if args.command == "load-status" else
                "Re-read the exact selected source slice and actual Lakehouse SQL endpoint rows, bounded by the plan. "
                "Writes only project-local comparison evidence; no source/Fabric mutation or automatic refresh.",
            )
        if args.command == "execute":
            with read_state(context) as connection:
                plan = validate_registered(connection, context, identifier(args.plan_id))
            if plan.get("kind") == movement.KIND:
                if role not in ("", "migration-lead"):
                    raise SafetyError("Only migration-lead can execute data movement.")
                source.confirmed(context)
                movement.ensure_unattempted(context, plan)
                reason = (
                    plan["summary"] + " | plan " + plan["planId"] + " | SHA256 " + digest(plan)
                    + " | Windows " + plan["identity"]["windows"] + " | Fabric " + plan["identity"]["fabricUser"]
                    + " | tenant " + plan["identity"]["tenant"] + " | " + plan["calls"][0]["description"]
                    + " | exact call " + plan["calls"][0]["method"] + " " + plan["calls"][0]["path"]
                    + " | inspect plans/" + plan["planId"] + ".md for scope, budgets, body and verification reads."
                )
                if lead_scope:
                    return ask_with_grant(
                        request, context, provisioning.binding(plan), reason, plan["expiresAt"], scoped_plan=plan,
                    )
                return {}
            if plan.get("kind") == provisioning.KIND:
                if role not in ("", "migration-lead"):
                    raise SafetyError("Only migration-lead can execute sandbox provisioning.")
                provisioning.ensure_unattempted(context, plan)
                reason = (
                    plan["summary"] + " | plan " + plan["planId"] + " | SHA256 " + digest(plan)
                    + " | user " + plan["identity"]["fabricUser"] + " | tenant " + plan["identity"]["tenant"]
                    + " | exact POST " + plan["calls"][0]["path"] + " "
                    + json.dumps(plan["calls"][0]["body"], separators=(",", ":"))
                    + " | inspect plans/" + plan["planId"] + ".md for all verification reads. "
                    "May consume Fabric capacity; no SQL, updates/deletes or automatic undo."
                )
                if lead_scope:
                    return ask_with_grant(
                        request, context, provisioning.binding(plan), reason, plan["expiresAt"], scoped_plan=plan,
                    )
                # Defer the native ask to the scoped hook; the CLI independently requires its grant.
                return {}
            if role != "migration-lead":
                raise SafetyError("Only a host-identified migration-lead may request execution.")
            return decision("ask", f"{plan['summary']} | plan {plan['planId']} | SHA256 {digest(plan)}. Live execution remains disabled.")
        if args.command in WRITE_COMMANDS:
            if role in ("estate-analyst", "validator"):
                raise SafetyError("This agent has read-only boundaries.")
            if args.request is not None:
                context.project_path(args.request)
            return {}
        if args.command == "plans" and args.verb == "expire":
            read_plan(context, identifier(args.plan_id), allow_expired=True)
            return decision("ask", "Expire this plan in the hook-owned registry; this cannot approve execution.")
        return read_decision(context, "Read action: review this bounded s2f command.")
    protected_arguments(context, arguments)
    if tool in SAFE_TOOLS:
        return {}
    if tool in READ_TOOLS | WRITE_TOOLS:
        for value in tool_paths(arguments):
            context.check_blind(value)
            candidate = Path(value)
            if not candidate.is_absolute():
                candidate = context.project / candidate
            reject_links(candidate)
            resolved = candidate.resolve()
            if resolved.is_relative_to(context.plugin) and tool in READ_TOOLS:
                relative = resolved.relative_to(context.plugin)
                if not relative.parts or relative.parts[0] not in {"references", "schemas", "rules", "probes", "skills"}:
                    raise SafetyError("Only packaged reference areas may be read from the installation.")
                candidate = contained(candidate, context.plugin)
                relative = candidate.relative_to(context.plugin)
            else:
                candidate = contained(candidate, context.project)
                relative = candidate.relative_to(context.project)
            context.check_blind(str(candidate))
            if any(part.casefold() == ".blind-paths" for part in relative.parts):
                raise SafetyError("Blind configuration is hook-owned and is not model context.")
            if tool in {"rg", "grep"} and not candidate.is_file():
                raise SafetyError("Skeleton content searches require explicit files, not recursive directory traversal.")
            if tool in WRITE_TOOLS:
                if any(part.casefold() in PROTECTED for part in relative.parts):
                    raise SafetyError("Configuration, plans, audit, credentials and policy files are hook/CLI-owned.")
                role = request.get("agentType", "").rsplit(":", 1)[-1]
                if role in ("migration-lead", "estate-analyst", "code-reviewer", "validator"):
                    raise SafetyError("This agent cannot directly edit generated project files.")
                if role in ("tsql-converter", "fabric-engineer"):
                    area = "converted" if role == "tsql-converter" else "fabric"
                    root = context.run_root()
                    if not candidate.is_relative_to(root):
                        raise SafetyError("Agent write leaves its permitted run-output area.")
                    output = candidate.relative_to(root)
                    if len(output.parts) < 3 or output.parts[1] != area:
                        raise SafetyError("Agent write leaves its permitted run-output area.")
            elif any(part.casefold().startswith(".env") for part in relative.parts):
                raise SafetyError("Credential files must not be read into model context.")
        if tool in READ_TOOLS:
            return read_decision(context, "Read project files.")
        return decision("ask", "Write project files; no source or Fabric execution is authorized.")
    # MCP is read-only at the server as well; only this small verified surface is exposed here.
    if tool.startswith(("s2f-fabric-", "s2f-fabric/")) and any(
        tool.endswith(name) for name in MCP_READS
    ):
        return decision("deny", "Fabric reads remain disabled until configuration confirms the exact user and tenant.")
    raise SafetyError("Tool is not admitted by the conservative skeleton policy; direct source/cloud bypass is prohibited.")


def post_tool(request, context):
    if request.get("toolName") != "powershell":
        return {}
    args = shell_arguments(request["toolArgs"].get("command"), context)
    result = request.get("toolResult")
    if not isinstance(result, dict):
        raise SafetyError("A successful platform tool result is required for registration.")
    if result.get("resultType") != "success":
        return failed_tool(request, context)
    # A successful shell tool can still contain a nonzero process exit.
    text = result.get("textResultForLlm", "")
    decoder = json.JSONDecoder(object_pairs_hook=unique_keys)
    try:
        receipt, remainder = decoder.raw_decode(text.lstrip())
    except (ValueError, TypeError):
        raise SafetyError("The CLI did not return a machine-readable success receipt.") from None
    tail = text.lstrip()[remainder:].strip()
    if tail:
        exit_result = re.fullmatch(r"<shellId: [^<>\r\n]+ completed with exit code (\d+)>", tail)
        if exit_result is None:
            raise SafetyError("The shell did not report a bounded successful exit.")
        if int(exit_result[1]) != 0:
            return failed_tool(request, context)
    if not isinstance(receipt, dict) or receipt.get("contractVersion") != "1.0":
        raise SafetyError("Unsupported CLI receipt contract.")
    if receipt.get("status") == "blocked":
        return failed_tool(request, context)
    event_id = digest({
        "session": context.session, "timestamp": request.get("timestamp"),
        "command": public_command(request), "receipt": receipt,
    })
    with hook_state(context) as connection:
        if connection.execute(
            "SELECT 1 FROM audit WHERE project=? AND event_id=?", (str(context.project), event_id),
        ).fetchone():
            return {"additionalContext": "S2F already recorded this successful event; no action was repeated."}
        plan = None
        run_id = "session"
        if args.command == "config" and args.verb == "save":
            plan = configuration.read_plan(context, identifier(receipt.get("planId")))
            if (receipt.get("kind"), receipt.get("status"), receipt.get("planHash")) != (
                "configuration", "draft", digest(plan),
            ):
                raise SafetyError("Configuration draft receipt does not match the saved plan.")
            values = (str(context.project), plan["planId"], digest(plan), context.session)
            existing = connection.execute(
                "SELECT hash,session,state FROM configuration_plans WHERE project=? AND plan_id=?", values[:2],
            ).fetchone()
            if existing is not None and existing != (values[2], values[3], "registered"):
                raise SafetyError("Configuration plan cannot be changed, reused or rebound.")
            connection.execute("INSERT OR IGNORE INTO configuration_plans VALUES(?,?,?,?,'registered')", values)
        elif args.command == "config" and args.verb == "apply":
            plan = configuration.registered(context, connection, identifier(args.plan_id), check_base=False)
            if (receipt.get("status"), receipt.get("planId"), receipt.get("planHash"), receipt.get("configHash")) != (
                "applied", plan["planId"], digest(plan), plan["payloadHash"],
            ) or file_hash(context.project_path("migration.config.yaml")) != plan["payloadHash"]:
                raise SafetyError("Applied configuration receipt or actual file hash does not match the approved plan.")
            approvals.finish(
                connection, context, receipt.get("grantId"), "configuration:" + plan["planId"] + ":" + digest(plan),
            )
            configuration.check_locks(context, plan["payload"], connection)
            connection.execute(
                "INSERT OR REPLACE INTO configuration VALUES(?,?,?)",
                (str(context.project), plan["payloadHash"],
                 json.dumps(configuration.approved_workspaces(plan["payload"], connection, context.project))),
            )
            connection.execute(
                "UPDATE plans SET state='expired' WHERE project=? AND state='registered'", (str(context.project),),
            )
            connection.execute("UPDATE configuration_plans SET state='expired' WHERE project=?", (str(context.project),))
            connection.execute(
                "UPDATE configuration_plans SET state='consumed' WHERE project=? AND plan_id=?",
                (str(context.project), plan["planId"]),
            )
        elif args.command == "approvals" and args.verb != "status":
            if (receipt.get("kind"), receipt.get("status"), receipt.get("mode")) != ("read-allowance", "requested", args.verb):
                raise SafetyError("Invalid session read-allowance receipt.")
            approvals.finish(connection, context, receipt.get("grantId"), "reads:" + args.verb)
            connection.execute(
                "INSERT OR REPLACE INTO session_reads VALUES(?,?,?,?)",
                (str(context.project), context.session, int(args.verb == "allow"), host.instance()),
            )
        elif args.command in ("source-check", "source-confirm"):
            value = source.checked(context, identifier(receipt.get("checkId")))
            if args.command == "source-check":
                if receipt != value["receipt"] or receipt["runId"] != args.run_id:
                    raise SafetyError("Source check receipt differs from the private verified connection result.")
            else:
                if (receipt.get("kind"), receipt.get("status"), receipt.get("checkId"), receipt.get("runId")) != (
                    "source-confirmation", "requested", args.check_id, value["receipt"]["runId"],
                ):
                    raise SafetyError("Source confirmation receipt does not match the session check.")
                approvals.finish(connection, context, receipt.get("grantId"), source.binding(value))
                connection.execute(
                    "INSERT OR REPLACE INTO source_sessions VALUES(?,?,?,?,?)",
                    (str(context.project), context.session, host.instance(), context.config_hash(), args.check_id),
                )
            run_id = value["receipt"]["runId"]
        elif args.command == "discover":
            inventory = load_json(context.run_path(identifier(args.run_id), "inventory.json"))
            if (receipt.get("kind"), receipt.get("status"), receipt.get("runId"), receipt.get("inventoryHash"),
                receipt.get("databaseCount"), receipt.get("objectCount"), receipt.get("definitionsExtracted")) != (
                "source-discovery", "discovered", args.run_id, digest(inventory),
                len(inventory["databases"]), len(inventory["objects"]), False,
            ):
                raise SafetyError("Discovery receipt does not match the saved inventory.")
            run_id = args.run_id
        elif args.command == "table-check":
            if receipt != movement.checked_table(context, identifier(receipt.get("checkId")), fresh=True):
                raise SafetyError("Table check receipt differs from the private verified metadata result.")
            run_id = receipt["runId"]
        elif args.command in WRITE_COMMANDS:
            if receipt.get("status") != "draft":
                raise SafetyError("Draft plan receipt was not valid.")
            plan = register_plan(connection, context, identifier(receipt.get("planId")))
            if (receipt.get("planHash") != digest(plan) or receipt.get("runId") != plan["runId"]
                    or receipt.get("executionEnabled") is not (plan.get("kind") in (provisioning.KIND, movement.KIND))):
                raise SafetyError("Plan receipt does not match the on-disk plan.")
            run_id = plan["runId"]
        elif args.command == "execute":
            plan = read_plan(context, identifier(args.plan_id))
            if plan.get("kind") == movement.KIND:
                movement.record(context, connection, plan, receipt, "initial")
            elif plan.get("kind") == provisioning.KIND:
                provisioning.record(context, connection, plan, receipt, initial=True)
            else:
                raise SafetyError("Legacy execution receipts are not accepted.")
            run_id = plan["runId"]
        elif args.command in ("load-status", "reconcile"):
            plan = read_plan(context, identifier(args.plan_id), allow_expired=True)
            movement.record(context, connection, plan, receipt, "status" if args.command == "load-status" else "reconcile")
            run_id = plan["runId"]
        elif args.command == "provision-status":
            plan, _ = provisioning.job(context, connection, identifier(args.plan_id))
            provisioning.record(context, connection, plan, receipt, initial=False)
            run_id = plan["runId"]
        elif args.command == "plans" and args.verb == "expire":
            plan = read_plan(context, identifier(args.plan_id), allow_expired=True)
            if (receipt.get("status"), receipt.get("planId"), receipt.get("planHash")) != (
                "expiration-requested", plan["planId"], digest(plan),
            ):
                raise SafetyError("Expiration receipt does not match the plan.")
            row = connection.execute(
                "SELECT hash,session,state FROM plans WHERE project=? AND plan_id=?",
                (str(context.project), plan["planId"]),
            ).fetchone()
            if row not in (
                (digest(plan), context.session, "registered"),
                (digest(plan), context.session, "expired"),
            ):
                raise SafetyError("Plan cannot be expired by this session.")
            connection.execute(
                "UPDATE plans SET state='expired' WHERE project=? AND plan_id=?",
                (str(context.project), plan["planId"]),
            )
            run_id = plan["runId"]
        record_event(connection, context, event_id, run_id, public_command(request), "completed", plan)
        output = context.run_path(run_id, "commands", f"{event_id}.json")
        atomic_json(output, receipt)
    return {"additionalContext": "S2F audit recorded. Bounded bronze extraction/upload/load requires exact plans and scoped native grants. Pending/loaded receipts are not value-equivalence proof; reconcile must read the actual target. Silver/gold conversion remains unavailable."}


def failed_tool(request, context):
    if request.get("toolName") != "powershell":
        return {}
    shell_arguments(request["toolArgs"].get("command"), context)
    event_id = digest({
        "session": context.session, "timestamp": request.get("timestamp"),
        "command": public_command(request),
    })
    with hook_state(context) as connection:
        record_event(
            connection, context, event_id, "session",
            public_command(request), "failed",
        )
        atomic_json(
            context.run_path("session", "commands", f"{event_id}.json"),
            {"contractVersion": "1.0", "status": "blocked", "error": "Native tool or CLI command failed; no plan was registered."},
        )
    return {"additionalContext": "S2F recorded command failure. No plan approval or successful execution is inferred."}


def session_start(context):
    with hook_state(context) as connection:
        approvals.reset_session(connection, context)
        row = connection.execute(
            "SELECT hash,workspaces FROM configuration WHERE project=?", (str(context.project),),
        ).fetchone()
        current_hash = context.config_hash() if (context.project / "migration.config.yaml").is_file() else None
        approved = row is not None and row[0] == current_hash
        fence = json.loads(row[1]) if approved else []
    prefix = (
        f"& '{context.plugin / 'scripts' / 's2f.ps1'}' --project '{context.project}' "
        f"--data '{context.data}' --session '{context.session}'"
    )
    return {"additionalContext": (
        "S2F skeleton: require manual permission mode. Allow-all weakens ordinary approvals; "
        "command-hook timeouts fail open. New sandbox Workspace/Lakehouse/Warehouse creation requires "
        "an exact plan and a fresh migration-lead scoped native grant; hook timeout cannot authorize a write. "
        "A bounded Parquet/file-drop bronze route is available through separate extraction/upload/load plans. "
        "Only project-created, schema-disabled Lakehouses and the explicitly selected preview load API are supported. "
        "Reconciliation must reread the source and actual target; no silver/gold or module conversion is implemented. "
        "Fixed SQL2016 source identity/catalog reads require approved configuration and source-confirm for this session. "
        "Identity confirmation is pending: Windows may be inspected with whoami, source/Fabric are NOT connected. "
        "Windows login hint (not confirmed): " + getpass.getuser() + ". "
        "Current configuration SHA256: " + (current_hash or "unset") + "; hook-approved: " + str(approved)
        + "; approved workspace fence: " + json.dumps(fence) + ". "
        "Selected tenant (not identity-confirmed): " + json.dumps(context.runtime_options().get("A6")) + ". "
        "Use ask_user for choices, never approval. "
        "Reads ask every time until an explicit native-approved session opt-in; restart/resume resets it. "
        "Configuration choices are user-authored: config init/catalog/validate/diff/save --plan, then native-approved config apply. "
        "Use this exact canonical wrapper prefix for local commands: " + prefix
    )}


def main(event, payload=None):
    try:
        request = payload if payload is not None else json.loads(
            sys.stdin.read(1_000_001), object_pairs_hook=unique_keys,
        )
        if len(json.dumps(request)) > 1_000_000:
            raise SafetyError("Hook payload exceeds its bounded input limit.")
        context = context_from_request(request)
        if type(request.get("timestamp")) is not int:
            raise SafetyError("The host event timestamp is required for idempotent audit.")
        if event == "sessionStart":
            result = session_start(context)
        elif event == "sessionEnd":
            with hook_state(context) as connection:
                approvals.reset_session(connection, context)
            result = {}
        elif event == "preToolUse":
            result = pre_tool(request, context)
        elif event == "leadPreToolUse":
            result = {}
            if request.get("toolName") == "powershell":
                args = shell_arguments(request["toolArgs"].get("command"), context)
                if args.command == "execute":
                    result = pre_tool(request, context, lead_scope=True)
        elif event == "postToolUse":
            result = post_tool(request, context)
        elif event == "postToolUseFailure":
            result = failed_tool(request, context)
        else:
            raise SafetyError("Unsupported hook event.")
        print(json.dumps(result, separators=(",", ":")))
        return 0
    except (SafetyError, ValueError, TypeError, KeyError, OSError, sqlite3.Error, ModuleNotFoundError) as error:
        reason = str(error) if isinstance(error, SafetyError) else "Hook validation failed; local context is missing, invalid or locked."
        print(json.dumps(decision("deny", reason), separators=(",", ":")))
        return 0 if event in ("preToolUse", "leadPreToolUse") else 1
