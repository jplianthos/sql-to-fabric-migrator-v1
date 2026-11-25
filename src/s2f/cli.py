import argparse
import getpass
import json
import os
import platform
import shutil
import sqlite3
import sys
from pathlib import Path

from . import __version__, approvals, configuration, fabric, source
from .plans import WRITE_COMMANDS, create_plan, read_plan
from .safety import Context, SafetyError, atomic_text, digest, identifier
from .state import ledger_rows, read_state, validate_registered


READ_COMMANDS = ("profile", "scan", "plan-waves", "report")


def parser():
    root = argparse.ArgumentParser(prog="s2f", allow_abbrev=False)
    root.add_argument("--project", required=True, type=Path)
    root.add_argument("--data", required=True, type=Path)
    root.add_argument("--session", required=True)
    commands = root.add_subparsers(dest="command", required=True)
    for command in ("doctor", "whoami", *READ_COMMANDS):
        commands.add_parser(command, allow_abbrev=False)
    for command in ("source-check", "discover"):
        read = commands.add_parser(command, allow_abbrev=False)
        read.add_argument("--run-id", required=True)
        read.add_argument("--query-timeout", required=True, type=int, choices=range(1, 61))
        if command == "source-check":
            read.add_argument("--user", required=True)
    confirm = commands.add_parser("source-confirm", allow_abbrev=False)
    confirm.add_argument("check_id")
    confirm.add_argument("--native-grant", help=argparse.SUPPRESS)
    table = commands.add_parser("table-check", allow_abbrev=False)
    for name in ("run-id", "database", "schema", "table"):
        table.add_argument("--" + name, required=True)
    table.add_argument("--query-timeout", required=True, type=int, choices=range(1, 61))
    reconcile = commands.add_parser("reconcile", allow_abbrev=False)
    reconcile.add_argument("--plan-id", required=True)
    load_status = commands.add_parser("load-status", allow_abbrev=False)
    load_status.add_argument("plan_id")
    config = commands.add_parser("config", allow_abbrev=False)
    config_commands = config.add_subparsers(dest="verb", required=True)
    config_commands.add_parser("init", allow_abbrev=False)
    catalog = config_commands.add_parser("catalog", allow_abbrev=False)
    catalog.add_argument("--group", choices=list("ABCDEFG"))
    validation = config_commands.add_parser("validate", allow_abbrev=False)
    validation.add_argument("--input", type=Path)
    validation.add_argument("--complete", action="store_true")
    config_commands.add_parser("show", allow_abbrev=False)
    difference = config_commands.add_parser("diff", allow_abbrev=False)
    difference.add_argument("--input", required=True, type=Path)
    save = config_commands.add_parser("save", allow_abbrev=False)
    save.add_argument("--plan", action="store_true", required=True)
    save_input = save.add_mutually_exclusive_group(required=True)
    save_input.add_argument("--input", type=Path)
    save_input.add_argument("--answers", help="Exact explicit option answers as a JSON object; other options stay unchanged/unset.")
    apply = config_commands.add_parser("apply", allow_abbrev=False)
    apply.add_argument("plan_id")
    apply.add_argument("--native-grant", help=argparse.SUPPRESS)
    lookup = config_commands.add_parser("lookup", allow_abbrev=False)
    lookup.add_argument("--kind", choices=("capacities", "workspaces"), required=True)
    lookup.add_argument("--user", required=True)
    lookup.add_argument("--tenant", required=True)
    lookup.add_argument("--subscription", required=True)
    lookup.add_argument("--redacted", action="store_true")
    permission = commands.add_parser("approvals", allow_abbrev=False)
    permission.add_argument("category", choices=("reads",))
    permission.add_argument("verb", choices=("allow", "ask", "status"))
    permission.add_argument("--native-grant", help=argparse.SUPPRESS)
    for command in WRITE_COMMANDS:
        write = commands.add_parser(command, allow_abbrev=False)
        write.add_argument("--plan", action="store_true", required=True)
        request = write.add_mutually_exclusive_group(required=True)
        request.add_argument("--request", type=Path)
        request.add_argument("--answers", help="Exact explicit data-movement stage answers; the user need not author a config/request file.")
    execute = commands.add_parser("execute", allow_abbrev=False)
    execute.add_argument("plan_id")
    execute.add_argument("--native-grant", help=argparse.SUPPRESS)
    provisioning_status = commands.add_parser("provision-status", allow_abbrev=False)
    provisioning_status.add_argument("plan_id")
    plans = commands.add_parser("plans", allow_abbrev=False)
    plans.add_argument("verb", choices=("list", "show", "expire"))
    plans.add_argument("plan_id", nargs="?")
    ledger = commands.add_parser("ledger", allow_abbrev=False)
    ledger.add_argument("verb", choices=("show", "export"))
    ledger.add_argument("--run-id")
    return root


def configuration_command(args, context):
    result = {"contractVersion": "1.0", "kind": "configuration", "command": args.verb}
    if args.verb == "init":
        return {**result, "configuration": configuration.template(context), "saved": False}
    if args.verb == "catalog":
        return {**result, "options": configuration.catalog(context, args.group)}
    if args.verb == "lookup":
        return {**result, "result": fabric.lookup(args.kind, args.user, args.tenant, args.subscription, args.redacted)}
    if args.verb in ("validate", "show", "diff"):
        value = configuration.read(context, getattr(args, "input", None))
        missing = configuration.validate(value, context, getattr(args, "complete", False))
        configuration.check_locks(context, value)
        if args.verb == "diff":
            return {**result, "changedOptions": configuration.changes(context, value), "saved": False}
        approved = False
        if not getattr(args, "input", None) and context.registry.exists():
            with read_state(context) as connection:
                row = connection.execute("SELECT hash FROM configuration WHERE project=?", (str(context.project),)).fetchone()
                approved = row == (context.config_hash(),)
        return {**result, "configuration": value, "unanswered": missing, "complete": not missing, "approved": approved}
    if args.verb == "save":
        value = configuration.answers(context, args.answers) if args.answers is not None else None
        plan = configuration.draft(context, args.input, value=value)
        return {**result, "status": "draft", "planId": plan["planId"], "planHash": digest(plan),
                "changedOptions": plan["changedOptions"], "registered": False}
    if args.verb == "apply":
        with read_state(context) as connection:
            plan = configuration.registered(context, connection, identifier(args.plan_id))
        binding = "configuration:" + plan["planId"] + ":" + digest(plan)
        grant_id = approvals.claim(context, args.native_grant, binding)
        atomic_text(context.project_path("migration.config.yaml"), configuration.render(plan["payload"]))
        return {**result, "status": "applied", "planId": plan["planId"], "planHash": digest(plan),
                "configHash": plan["payloadHash"], "grantId": grant_id, "cloudWritesEnabled": False}
    raise SafetyError("Unsupported configuration command.")


def environment_context(args):
    plugin = Path(os.environ.get("COPILOT_PLUGIN_ROOT", Path(__file__).resolve().parents[2]))
    if not (plugin / "plugin.json").is_file():
        raise SafetyError("Use the plugin wrapper, or supply its host COPILOT_PLUGIN_ROOT for an installed console entry.")
    return Context.create(args.project, plugin, args.data, args.session)


def dispatch(args, context):
    if args.command == "doctor":
        return {
            "contractVersion": "1.0", "version": __version__, "python": platform.python_version(),
            "tools": {name: shutil.which(name) is not None for name in ("pwsh", "node", "dotnet", "az", "sqlcmd")},
            "liveExecutionEnabled": True, "sourceAccessEnabled": True,
            "enabledFabricOperations": [
                "create-workspace", "create-lakehouse", "create-warehouse",
                "conditional-onelake-upload", "preview-load-new-bronze-table", "bronze-readback",
            ],
            "identityConfirmation": "required-before-live-access",
        }
    if args.command == "whoami":
        return {
            "contractVersion": "1.0", "windows": getpass.getuser(),
            "source": "not-connected", "fabric": "not-connected",
            "identityConfirmed": False,
        }
    if args.command == "config":
        return configuration_command(args, context)
    if args.command == "source-check":
        return source.check(context, args.run_id, args.user, args.query_timeout)
    if args.command == "source-confirm":
        return source.confirm(context, args.check_id, args.native_grant)
    if args.command == "discover":
        return source.discover(context, args.run_id, args.query_timeout)
    if args.command in ("table-check", "load-status", "reconcile"):
        from . import movement
        if args.command == "table-check":
            return movement.table_check(context, args.run_id, args.database, args.schema, args.table, args.query_timeout)
        if args.command == "load-status":
            return movement.load_status(context, identifier(args.plan_id))
        return movement.reconcile(context, identifier(args.plan_id))
    if args.command == "approvals":
        if args.verb == "status":
            return {"contractVersion": "1.0", "kind": "read-allowance", "status": "current",
                    "enabled": approvals.reads_allowed(context)}
        binding = "reads:" + args.verb
        grant_id = approvals.claim(context, args.native_grant, binding)
        return {"contractVersion": "1.0", "kind": "read-allowance", "status": "requested",
                "mode": args.verb, "grantId": grant_id}
    if args.command in READ_COMMANDS:
        raise SafetyError(f"{args.command} is not implemented; no source or Fabric access occurred.")
    if args.command in WRITE_COMMANDS:
        plan = create_plan(context, args.command, args.request, args.answers)
        return {
            "contractVersion": "1.0", "status": "draft", "planId": plan["planId"],
            "runId": plan["runId"], "planHash": digest(plan),
            "registered": False, "executionEnabled": plan.get("kind") in ("fabric-provisioning", "data-movement"),
        }
    if args.command == "execute":
        with read_state(context) as connection:
            plan = validate_registered(connection, context, identifier(args.plan_id))
        return fabric.execute_plan(plan, context, args.native_grant)
    if args.command == "provision-status":
        return fabric.provisioning_status(context, identifier(args.plan_id))
    if args.command == "plans":
        if args.verb == "list":
            if args.plan_id:
                raise SafetyError("plans list does not accept a plan ID.")
            directory = context.project_path("plans")
            return {"contractVersion": "1.0", "plans": sorted(path.stem for path in directory.glob("*.json"))}
        if not args.plan_id:
            raise SafetyError("This plans command requires a plan ID.")
        plan = read_plan(context, identifier(args.plan_id), allow_expired=args.verb == "expire")
        if args.verb == "expire":
            return {
                "contractVersion": "1.0", "status": "expiration-requested",
                "planId": plan["planId"], "planHash": digest(plan),
            }
        return plan
    if args.command == "ledger":
        with read_state(context) as connection:
            rows = ledger_rows(connection, context)
        if args.verb == "export":
            if not args.run_id:
                raise SafetyError("ledger export requires an explicit --run-id.")
            path = context.run_path(identifier(args.run_id), "ledger.jsonl")
            atomic_text(path, "".join(json.dumps(row) + "\n" for row in rows))
        return {"contractVersion": "1.0", "items": rows}
    raise SafetyError("Unknown command.")


def main(argv=None):
    try:
        args = parser().parse_args(argv)
        result = dispatch(args, environment_context(args))
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0
    except (SafetyError, OSError, sqlite3.Error, ModuleNotFoundError) as error:
        if isinstance(error, ModuleNotFoundError):
            message = "Install the pinned plugin runtime dependencies in .venv before using configuration."
        else:
            message = str(error) if isinstance(error, SafetyError) else "Required local state is unavailable or locked."
        print(json.dumps({"contractVersion": "1.0", "status": "blocked", "error": message}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
