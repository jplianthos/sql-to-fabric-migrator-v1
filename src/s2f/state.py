import json
import sqlite3
from contextlib import contextmanager

from .plans import read_plan
from .safety import SafetyError, atomic_text, canonical, digest, identifier, timestamp


DDL = """
CREATE TABLE IF NOT EXISTS plans (
    project TEXT NOT NULL, plan_id TEXT NOT NULL, hash TEXT NOT NULL,
    session TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('registered','consumed','expired')),
    PRIMARY KEY(project, plan_id)
);
CREATE TABLE IF NOT EXISTS configuration (
    project TEXT PRIMARY KEY, hash TEXT NOT NULL, workspaces TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit (
    project TEXT NOT NULL, event_id TEXT NOT NULL, run_id TEXT NOT NULL,
    payload TEXT NOT NULL, PRIMARY KEY(project, event_id)
);
CREATE TABLE IF NOT EXISTS ownership (
    project TEXT NOT NULL, workspace TEXT NOT NULL, item TEXT NOT NULL,
    PRIMARY KEY(project, workspace, item)
);
CREATE TABLE IF NOT EXISTS configuration_plans (
    project TEXT NOT NULL, plan_id TEXT NOT NULL, hash TEXT NOT NULL,
    session TEXT NOT NULL, state TEXT NOT NULL,
    PRIMARY KEY(project, plan_id)
);
CREATE TABLE IF NOT EXISTS configuration_locks (
    project TEXT NOT NULL, option_id TEXT NOT NULL, value_hash TEXT NOT NULL,
    reason TEXT NOT NULL, PRIMARY KEY(project, option_id)
);
CREATE TABLE IF NOT EXISTS native_grants (
    grant_id TEXT PRIMARY KEY, project TEXT NOT NULL, session TEXT NOT NULL,
    binding TEXT NOT NULL, secret_hash TEXT NOT NULL, expires_at TEXT NOT NULL,
    state TEXT NOT NULL, instance TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS session_reads (
    project TEXT NOT NULL, session TEXT NOT NULL, enabled INTEGER NOT NULL, instance TEXT NOT NULL,
    PRIMARY KEY(project, session)
);
CREATE TABLE IF NOT EXISTS source_sessions (
    project TEXT NOT NULL, session TEXT NOT NULL, instance TEXT NOT NULL,
    config_hash TEXT NOT NULL, check_id TEXT NOT NULL,
    PRIMARY KEY(project,session)
);
CREATE TABLE IF NOT EXISTS sandbox_workspaces (
    project TEXT NOT NULL, id TEXT NOT NULL, name TEXT NOT NULL, capacity TEXT NOT NULL,
    tenant TEXT NOT NULL, plan_id TEXT NOT NULL, PRIMARY KEY(project,id), UNIQUE(project,name)
);
CREATE TABLE IF NOT EXISTS fabric_jobs (
    project TEXT NOT NULL, plan_id TEXT NOT NULL, hash TEXT NOT NULL, session TEXT NOT NULL,
    grant_id TEXT NOT NULL, operation_id TEXT, next_poll TEXT, state TEXT NOT NULL,
    receipt_id TEXT NOT NULL, PRIMARY KEY(project,plan_id)
);
CREATE TABLE IF NOT EXISTS owned_items (
    project TEXT NOT NULL, workspace TEXT NOT NULL, item TEXT NOT NULL, type TEXT NOT NULL,
    name TEXT NOT NULL, schemas_enabled INTEGER, PRIMARY KEY(project,workspace,item)
);
CREATE TABLE IF NOT EXISTS data_steps (
    project TEXT NOT NULL, plan_id TEXT NOT NULL, hash TEXT NOT NULL, session TEXT NOT NULL,
    stage TEXT NOT NULL, run_id TEXT NOT NULL, grant_id TEXT NOT NULL, operation_id TEXT,
    next_poll TEXT, state TEXT NOT NULL, receipt_id TEXT NOT NULL, PRIMARY KEY(project,plan_id)
);
"""


@contextmanager
def hook_state(context):
    """Only hook entry points may open state for writing."""
    connection = sqlite3.connect(context.registry, timeout=0.15)
    try:
        connection.executescript(DDL)
        connection.execute("BEGIN IMMEDIATE")
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@contextmanager
def read_state(context):
    if not context.registry.is_file():
        raise SafetyError("Hook registry is unavailable; start a trusted plugin session.")
    connection = sqlite3.connect(context.registry.as_uri() + "?mode=ro", uri=True, timeout=0.15)
    try:
        yield connection
    finally:
        connection.close()


def register_plan(connection, context, plan_id):
    plan = read_plan(context, plan_id)
    values = (str(context.project), plan_id, digest(plan), context.session)
    existing = connection.execute(
        "SELECT hash,session,state FROM plans WHERE project=? AND plan_id=?", values[:2],
    ).fetchone()
    if existing is not None:
        if existing != (values[2], values[3], "registered"):
            raise SafetyError("A registered plan cannot be changed, reused or rebound to another session.")
        return plan
    connection.execute(
        "INSERT INTO plans(project,plan_id,hash,session,state) VALUES (?,?,?,?,'registered')", values,
    )
    return plan


def validate_registered(connection, context, plan_id):
    plan = read_plan(context, plan_id)
    row = connection.execute(
        "SELECT hash,session,state FROM plans WHERE project=? AND plan_id=?",
        (str(context.project), plan_id),
    ).fetchone()
    if row != (digest(plan), context.session, "registered"):
        raise SafetyError("Plan is missing, tampered, consumed, expired or belongs to another session.")
    config = connection.execute(
        "SELECT hash,workspaces FROM configuration WHERE project=?", (str(context.project),),
    ).fetchone()
    if config is None or config[0] != context.config_hash():
        raise SafetyError("Configuration has not been approved by the configuration workflow.")
    if plan.get("kind") in ("fabric-provisioning", "data-movement"):
        return plan
    approved = json.loads(config[1])
    for workspace in plan["workspaces"]:
        if workspace not in approved:
            raise SafetyError("Plan targets a workspace outside the approved configuration.")
    for call in plan["calls"]:
        if call["operation"] != "create" and connection.execute(
            "SELECT 1 FROM ownership WHERE project=? AND workspace=? AND item=?",
            (str(context.project), call["workspaceId"], call["itemId"]),
        ).fetchone() is None:
            raise SafetyError("Plan would change an item not owned by this project.")
    return plan


def consume_plan(connection, context, plan_id):
    plan = validate_registered(connection, context, plan_id)
    updated = connection.execute(
        "UPDATE plans SET state='consumed' WHERE project=? AND plan_id=? AND state='registered'",
        (str(context.project), plan_id),
    )
    if updated.rowcount != 1:
        raise SafetyError("Plan is no longer available for single-use execution.")
    return plan


def record_event(connection, context, event_id, run_id, command, status, plan=None):
    identifier(run_id)
    payload = {
        "contractVersion": "1.0", "eventId": event_id, "observedAt": timestamp(),
        "sessionId": context.session, "runId": run_id, "command": command,
        "status": status, "planId": plan["planId"] if plan else None,
        "planHash": digest(plan) if plan else None,
    }
    connection.execute(
        "INSERT OR IGNORE INTO audit(project,event_id,run_id,payload) VALUES(?,?,?,?)",
        (str(context.project), event_id, run_id, canonical(payload).decode()),
    )
    export_audit(connection, context, run_id)


def export_audit(connection, context, run_id):
    rows = connection.execute(
        "SELECT payload FROM audit WHERE project=? AND run_id=? ORDER BY rowid",
        (str(context.project), identifier(run_id)),
    ).fetchall()
    path = context.run_path(run_id, "approvals.jsonl")
    atomic_text(path, "".join(row[0] + "\n" for row in rows))


def ledger_rows(connection, context):
    return [
        {"contractVersion": "1.0", "workspaceId": workspace, "itemId": item, "state": "deployed"}
        for workspace, item in connection.execute(
            "SELECT workspace,item FROM ownership WHERE project=? ORDER BY workspace,item",
            (str(context.project),),
        )
    ]


def record_created_item(connection, context, plan_id, workspace_id, item_id, allow_expired=False):
    """Call only after verifying the executor's private response proof, never arbitrary tool IDs."""
    plan = read_plan(context, plan_id, allow_expired=allow_expired)
    row = connection.execute(
        "SELECT hash,state FROM plans WHERE project=? AND plan_id=?",
        (str(context.project), plan_id),
    ).fetchone()
    if row != (digest(plan), "consumed") or not any(
        call["operation"] == "create" and call["workspaceId"] == workspace_id
        for call in plan["calls"]
    ) or not isinstance(item_id, str) or not item_id.strip():
        raise SafetyError("Ownership requires a consumed create plan and a verified item identity.")
    connection.execute(
        "INSERT INTO ownership(project,workspace,item) VALUES(?,?,?)",
        (str(context.project), workspace_id, item_id),
    )
