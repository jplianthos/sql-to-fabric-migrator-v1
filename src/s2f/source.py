import hashlib
import json
import os
import re
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from uuid import uuid4

import pyodbc
from jsonschema import Draft202012Validator

from . import approvals, configuration, host
from .provisioning import private_path
from .safety import (
    SafetyError, atomic_json, canonical, digest, identifier, load_json,
    parse_time, reject_links, timestamp, utc_now,
)
from .state import read_state


QUERIES = {"identity": 1, "database": 1, "objects": 1000, "columns": 10000}


@dataclass(frozen=True)
class CatalogConnection:
    connection: object
    config_hash: str
    instance: str
    database: str
    table: tuple | None = None


def options(context):
    with read_state(context) as connection:
        approved = connection.execute(
            "SELECT hash FROM configuration WHERE project=?", (str(context.project),),
        ).fetchone()
    if approved != (context.config_hash(),):
        raise SafetyError("Source reads require the exact native-approved configuration.")
    selected = configuration.read(context)["options"]
    if (selected["A2"] != "windows-integrated" or selected["A4"] not in ("sql2016", "auto-detect")
            or not selected["A3"] or selected["A3"]["encrypt"] != "mandatory"):
        raise SafetyError("Source reads require explicit Windows integrated auth, mandatory encryption and an engine profile.")
    if not isinstance(selected["A1"], str) or not re.fullmatch(
        r"lpc:\.(?:\\[A-Za-z0-9_$-]+)?", selected["A1"], re.IGNORECASE,
    ):
        raise SafetyError("This source checkpoint supports only an explicitly configured local shared-memory server.")
    return selected


def sql_text(context, query_id):
    if query_id not in QUERIES:
        raise SafetyError("Ad-hoc source SQL is disabled; only fixed catalog query IDs are accepted.")
    path = context.plugin / "sql" / f"source-{query_id}.sql"
    reject_links(path)
    if not path.is_file() or path.stat().st_size > 32768:
        raise SafetyError("The fixed source query library is missing or oversized.")
    return path.read_text(encoding="utf-8")


def guard(context, sql, scope=None):
    if not isinstance(sql, str) or not sql or len(sql) > 32768:
        raise SafetyError("Source SQL exceeds the bounded guard contract.")
    tool = context.plugin / "tools" / "S2F.ScriptDom" / "bin" / "Release" / "net8.0" / "S2F.ScriptDom.dll"
    reject_links(tool)
    if not tool.is_file():
        raise SafetyError("Build the pinned product S2F.ScriptDom guard before source access; no fallback is permitted.")
    payload = sql.encode("utf-8") if scope is None else canonical({**scope, "sql": sql})
    try:
        result = subprocess.run(
            ["dotnet", str(tool), "--guard" if scope is None else "--table"], input=payload,
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SafetyError("The SQL130 source guard did not complete; no source query was authorized.") from None
    expected = {
        "contractVersion": "1.0", "accepted": True,
        "sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
    }
    if scope is not None:
        expected["scopeSha256"] = hashlib.sha256(payload).hexdigest()
    try:
        receipt = json.loads(result.stdout)
    except ValueError:
        raise SafetyError("The SQL130 source guard returned no valid proof.") from None
    if result.returncode or receipt != expected:
        raise SafetyError("SQL130 source guard rejected the statement; no source query was sent.")


def database_name(value):
    if (not isinstance(value, str) or not value or len(value) > 128
            or any(ord(char) < 32 or char in ";{}" for char in value)):
        raise SafetyError("Source database names must be explicit, bounded ODBC-safe names.")
    return value


def connection_string(selected, database):
    database_name(database)
    trust = "yes" if selected["A3"]["trustServerCertificate"] else "no"
    return (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server={{{selected['A1']}}};Database={{{database}}};"
        f"Trusted_Connection=yes;Encrypt=yes;TrustServerCertificate={trust};"
        "ApplicationIntent=ReadOnly;MARS_Connection=no;"
    )


@contextmanager
def audit_run(context, run_id):
    identifier(run_id)
    lock = private_path(context, "source-locks", digest([str(context.project), run_id]))
    lock.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = lock.open("x", encoding="ascii")
    except FileExistsError:
        raise SafetyError("This source run is active or interrupted; inspect it before reusing its run ID.") from None
    try:
        with descriptor:
            descriptor.write(str(os.getpid()))
            descriptor.flush()
            path = context.run_path(run_id, "source-queries.jsonl")
            reject_links(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("a", encoding="utf-8", newline="\n") as log:
                yield log
    finally:
        lock.unlink()


def query(connection, context, query_id, sql, database, log):
    options(context)
    if database != "master":
        confirmed(context)
    elif query_id != "identity":
        raise SafetyError("Only the fixed own-session identity query may read master.")
    if (not isinstance(connection, CatalogConnection) or connection.config_hash != context.config_hash()
            or connection.instance != host.instance() or connection.database != database
            or (connection.table is not None and query_id not in ("identity", "database"))
            or sql != sql_text(context, query_id)):
        raise SafetyError("Source query does not match the bound connection, approved configuration or fixed query library.")
    guard(context, sql)
    return execute_rows(connection.connection, sql, query_id, database, log, QUERIES[query_id])


def execute_rows(connection, sql, query_id, database, log, limit, columns=None, max_bytes=None):
    start = perf_counter()
    side = "Target" if query_id.startswith("target-") else "Source"
    event = {
        "contractVersion": "1.0", "eventId": str(uuid4()), "observedAt": timestamp(),
        "queryId": query_id, "database": database, "sql": sql,
        "sha256": hashlib.sha256(sql.encode("utf-8")).hexdigest(),
        "status": "started", "durationMs": None, "rows": None,
    }

    def record():
        log.write(canonical(event).decode() + "\n")
        log.flush()
        os.fsync(log.fileno())

    record()
    cursor = None
    try:
        cursor = connection.cursor()
        cursor.execute(sql)
        if not cursor.description or any(
            not isinstance(column, (list, tuple)) or not column or not isinstance(column[0], str)
            for column in cursor.description
        ):
            raise SafetyError(f"{side} query returned an invalid result description.")
        names = [column[0] for column in cursor.description]
        if len(set(names)) != len(names):
            raise SafetyError(f"{side} query returned duplicate column names.")
        if columns is not None and names != columns:
            raise SafetyError("Data query columns differ from the approved explicit projection.")
        rows = []
        size = 0
        while True:
            batch = cursor.fetchmany(min(16 if max_bytes else 256, limit + 1 - len(rows)))
            if not batch:
                break
            if max_bytes:
                size += sum(
                    64 + (len(value.encode("utf-8")) if isinstance(value, str) else
                          len(value) if isinstance(value, bytes) else 0)
                    for row in batch for value in row
                )
                if size > max_bytes:
                    raise SafetyError("Data exceeds the bounded in-memory/file budget; no partial extraction was accepted.")
            rows.extend(batch)
            if len(rows) > limit:
                raise SafetyError("Query exceeds this checkpoint's row bound; no partial result was accepted.")
        if cursor.nextset():
            raise SafetyError("A fixed source SELECT returned an unexpected additional result set.")
        if any(len(row) != len(names) for row in rows):
            raise SafetyError(f"{side} query returned an inconsistent result shape.")
        result = [dict(zip(names, row, strict=True)) for row in rows]
        event.update(status="completed", rows=len(rows))
        return result
    except UnicodeError:
        raise SafetyError("Data contains text that cannot be represented losslessly as UTF-8.") from None
    except pyodbc.Error as error:
        code = str(error.args[0]) if error.args else ""
        state = code if re.fullmatch(r"[A-Z0-9]{5}", code) else "unknown"
        raise SafetyError(f"{side} query failed with SQLSTATE {state}; no raw server response was logged.") from None
    finally:
        try:
            if cursor is not None:
                close(cursor)
        finally:
            if event["status"] != "completed":
                event["status"] = "failed"
            event["durationMs"] = round((perf_counter() - start) * 1000, 3)
            record()


@contextmanager
def read_source(context, database=None, timeout=None, query_ids=(), table=None):
    if database is None or type(timeout) is not int or not 1 <= timeout <= 60:
        raise SafetyError("Ad-hoc source access is disabled; choose a database and an explicit 1-60 second query timeout.")
    selected = options(context)
    config_hash, instance = context.config_hash(), host.instance()
    if database != "master" and database not in (selected["C1"] or []):
        raise SafetyError("Source database is outside the native-approved C1 scope.")
    if database != "master":
        confirmed(context)
        if table is None:
            scope = selected["C2"]
            if scope is not None and (scope["mode"] != "all" or scope["exclude"]):
                raise SafetyError("Filtered C2 catalog scope is not implemented; no broader user-database read was attempted.")
        else:
            table_scope(selected, database, *table)
    elif query_ids:
        raise SafetyError("Only the fixed own-session identity query may read master.")
    texts = {key: sql_text(context, key) for key in ("identity", *query_ids)}
    for sql in texts.values():
        guard(context, sql)
    if "ODBC Driver 18 for SQL Server" not in pyodbc.drivers():
        raise SafetyError("ODBC Driver 18 for SQL Server is unavailable.")
    try:
        connection = pyodbc.connect(connection_string(selected, database), timeout=10, autocommit=True, readonly=True)
    except pyodbc.Error:
        raise SafetyError("Encrypted Windows source connection failed; no account, certificate or protocol fallback was attempted.") from None
    try:
        connection.timeout = timeout
        yield CatalogConnection(connection, config_hash, instance, database, table), texts
    except pyodbc.Error:
        raise SafetyError("Source connection failed; raw server details were not logged.") from None
    finally:
        close(connection)


def close(resource):
    try:
        resource.close()
    except pyodbc.Error:
        raise SafetyError("Source resource cleanup failed; no raw server response was logged.") from None


def windows_user():
    try:
        result = subprocess.run(["whoami.exe"], text=True, capture_output=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        raise SafetyError("Cannot verify the current Windows identity.") from None
    if result.returncode or not result.stdout.strip():
        raise SafetyError("Cannot verify the current Windows identity.")
    return result.stdout.strip()


def probe(connection, context, texts, database, log, expected):
    rows = query(connection, context, "identity", texts["identity"], database, log)
    if len(rows) != 1:
        raise SafetyError("The source identity query did not return exactly one complete observation.")
    value = rows[0]
    string_fields = {"engineVersion", "edition", "productLevel", "sourceUser", "originalUser",
                     "transport", "authentication", "encrypted"}
    if (set(value) != string_fields | {"engineMajor", "isSysadmin"}
            or any(not isinstance(value[key], str) or not value[key] for key in string_fields)
            or value["sourceUser"].casefold() != expected.casefold()
            or value["originalUser"].casefold() != expected.casefold()
            or value["authentication"].casefold() not in ("ntlm", "kerberos")
            or value["transport"].casefold() != "shared memory"
            or value["encrypted"].casefold() != "true"
            or type(value["engineMajor"]) is not int or value["engineMajor"] < 13
            or value["isSysadmin"] not in (0, 1)):
        raise SafetyError("Source identity, engine, Windows authentication, transport or encryption did not match.")
    return value


def check(context, run_id, user, timeout):
    if not isinstance(user, str) or windows_user().casefold() != user.casefold():
        raise SafetyError("Explicit expected source user does not match the current Windows identity.")
    with audit_run(context, run_id) as log, read_source(context, "master", timeout) as (connection, texts):
        identity = probe(connection, context, texts, "master", log, user)
        config_hash = connection.config_hash
    if context.config_hash() != config_hash:
        raise SafetyError("Configuration changed during the source identity check.")
    receipt = {
        "contractVersion": "1.0", "kind": "source-check", "checkId": str(uuid4()),
        "runId": run_id, "identity": identity, "confirmed": False,
        "exactSql2016Host": identity["engineMajor"] == 13,
    }
    atomic_json(private_path(context, "source-checks", receipt["checkId"]), {
        "receipt": receipt, "project": str(context.project), "session": context.session,
        "instance": host.instance(), "configHash": config_hash,
        "expiresAt": timestamp(utc_now() + timedelta(minutes=60)),
    })
    return receipt


def checked(context, check_id):
    options(context)
    value = load_json(private_path(context, "source-checks", identifier(check_id)))
    if (not isinstance(value, dict) or set(value) != {
        "receipt", "project", "session", "instance", "configHash", "expiresAt",
    } or not isinstance(value["receipt"], dict) or value["receipt"].get("kind") != "source-check"):
        raise SafetyError("Invalid private source identity check.")
    schema = Draft202012Validator(load_json(context.plugin / "schemas" / "source-output.schema.json"))
    if not schema.is_valid(value["receipt"]):
        raise SafetyError("Invalid private source identity receipt.")
    if (value["project"], value["session"], value["instance"], value["configHash"]) != (
        str(context.project), context.session, host.instance(), context.config_hash(),
    ) or parse_time(value["expiresAt"]) <= utc_now() or value["receipt"].get("checkId") != check_id:
        raise SafetyError("Source identity check is expired or belongs to another configuration/session/process.")
    return value


def binding(value):
    return "source:" + value["receipt"]["checkId"] + ":" + digest(value)


def confirm(context, check_id, grant):
    value = checked(context, check_id)
    grant_id = approvals.claim(context, grant, binding(value))
    return {
        "contractVersion": "1.0", "kind": "source-confirmation", "status": "requested",
        "checkId": check_id, "grantId": grant_id, "runId": value["receipt"]["runId"],
    }


def confirmed(context):
    with read_state(context) as connection:
        row = connection.execute(
            "SELECT check_id FROM source_sessions WHERE project=? AND session=? AND instance=? AND config_hash=?",
            (str(context.project), context.session, host.instance(), context.config_hash()),
        ).fetchone()
    if row is None:
        raise SafetyError("Confirm the actual source identity through a fresh native source-confirm prompt before discovery.")
    return checked(context, row[0])["receipt"]["identity"]


def qualified(*parts):
    return ".".join("[" + part.replace("]", "]]") + "]" for part in parts)


def sql_identifier(value):
    if (not isinstance(value, str) or not 1 <= len(value) <= 128
            or any(not 32 <= ord(char) <= 126 for char in value)):
        raise SafetyError("This extraction checkpoint requires explicit 1-128 character ASCII SQL identifiers.")
    return qualified(value)


def table_scope(selected, database, schema, table):
    for name in (database, schema, table):
        sql_identifier(name)
    if database not in (selected["C1"] or []):
        raise SafetyError("The table is outside approved C1 database scope.")
    if database.casefold() in {"master", "model", "msdb", "tempdb", "ssisdb"}:
        raise SafetyError("Table extraction is restricted to explicit user databases.")
    scope = selected["C2"]
    if scope is None or scope["exclude"]:
        raise SafetyError("Choose explicit C2 scope; table extraction does not support exclusion patterns yet.")
    object_id = qualified(database, schema, table)
    if ((scope["mode"] == "schemas" and schema not in scope["include"])
            or (scope["mode"] == "list" and object_id not in scope["include"])):
        raise SafetyError("The explicitly requested table is outside approved C2 scope.")


def metadata_sql(context, schema, table):
    for name in (schema, table):
        sql_identifier(name)
    path = context.plugin / "sql" / "table-metadata.sql"
    reject_links(path)
    text = path.read_text(encoding="utf-8")
    values = {"schema": schema, "table": table}
    return re.sub(
        r"N'__s2f_(schema|table)__'",
        lambda match: "N'" + values[match[1]].replace("'", "''") + "'", text,
    )


def table_metadata(context, connection, table, log):
    selected = options(context)
    confirmed(context)
    database, schema, name = (table[key] for key in ("database", "schema", "table"))
    table_scope(selected, database, schema, name)
    if (connection.config_hash != context.config_hash() or connection.instance != host.instance()
            or connection.database != database or connection.table != (schema, name)):
        raise SafetyError("Table metadata does not match the confirmed, bound source connection.")
    sql = metadata_sql(context, schema, name)
    guard(context, sql)
    return execute_rows(connection.connection, sql, "table-metadata", database, log, 128)


def data_sql(context, table, columns, keys, limit=None):
    values = {
        "columns": ", ".join(sql_identifier(name) for name in columns),
        "schema": sql_identifier(table["schema"]), "table": sql_identifier(table["table"]),
        "keys": ", ".join(sql_identifier(name) + " ASC" for name in keys),
    }
    path = context.plugin / "sql" / ("extract-table.sql" if limit is None else "extract-sample.sql")
    reject_links(path)
    text = path.read_text(encoding="utf-8")
    text = re.sub(r"\[__s2f_(columns|schema|table|keys)__\]", lambda match: values[match[1]], text)
    if limit is not None:
        if type(limit) is not int or not 0 <= limit <= 100000:
            raise SafetyError("The explicit sample limit must be bounded by 100000.")
        text = text.replace("TOP (0)", f"TOP ({limit})", 1)
    return text


def table_rows(context, connection, table, columns, keys, limit, max_rows, log):
    selected = options(context)
    confirmed(context)
    database, schema, name = (table[key] for key in ("database", "schema", "table"))
    table_scope(selected, database, schema, name)
    if (connection.config_hash != context.config_hash() or connection.instance != host.instance()
            or connection.database != database or connection.table != (schema, name)):
        raise SafetyError("Extraction does not match the confirmed, bound source connection.")
    sql = data_sql(context, table, columns, keys, limit)
    guard(context, sql, {"schema": schema, "table": name, "columns": columns, "keys": keys, "limit": limit})
    return execute_rows(
        connection.connection, sql, "table-data", database, log, max_rows,
        columns=columns, max_bytes=64_000_000,
    )


def discover(context, run_id, timeout):
    selected = options(context)
    identity = confirmed(context)
    databases = selected["C1"]
    if (not databases or len(databases) > 16 or len({name.casefold() for name in databases}) != len(databases)
            or any(name.casefold() in {"master", "model", "msdb", "tempdb", "ssisdb"} for name in databases)):
        raise SafetyError("Discovery requires 1-16 explicit, distinct user databases in approved C1; no databases are inferred.")
    for name in databases:
        database_name(name)
    user = windows_user()
    if identity["sourceUser"].casefold() != user.casefold():
        raise SafetyError("The confirmed Windows/source identity changed before discovery.")
    inventory = {
        "contractVersion": "1.0", "runId": run_id, "configHash": context.config_hash(),
        "databases": [], "objects": [], "definitionsExtracted": False, "ssis": "deferred-not-inspected",
        "coverage": "objects-columns-only", "consistency": "nontransactional",
    }
    with audit_run(context, run_id) as log:
        for database in databases:
            with read_source(context, database, timeout, ("database", "objects", "columns")) as (connection, texts):
                if probe(connection, context, texts, database, log, user) != identity:
                    raise SafetyError("Source identity or engine changed after confirmation.")
                rows = query(connection, context, "database", texts["database"], database, log)
                if len(rows) != 1 or rows[0].get("name") != database or rows[0].get("state") != "ONLINE":
                    raise SafetyError("The explicitly selected source database is unavailable or has a different name.")
                metadata = rows[0]
                if metadata.get("canViewDefinition") != 1:
                    raise SafetyError(
                        "Database VIEW DEFINITION is missing or unknown. Ask the DBA to review GRANT VIEW DEFINITION; "
                        "the plugin never grants permissions or silently proceeds without this visibility check."
                    )
                inventory["databases"].append(metadata)
                objects = query(connection, context, "objects", texts["objects"], database, log)
                by_id = {}
                for item in objects:
                    if (type(item.get("objectId")) is not int or any(
                        not isinstance(item.get(key), str) or not item[key]
                        for key in ("schemaName", "name", "objectType")
                    )):
                        raise SafetyError("Source catalog returned a malformed object.")
                    if item["objectId"] in by_id:
                        raise SafetyError("Source returned duplicate object IDs.")
                    item["objectType"] = item["objectType"].strip()
                    item.update(
                        id=qualified(database, item["schemaName"], item["name"]),
                        database=database, columns=[],
                    )
                    by_id[item["objectId"]] = item
                for column in query(connection, context, "columns", texts["columns"], database, log):
                    object_id = column.pop("objectId", None)
                    if type(object_id) is not int:
                        raise SafetyError("Source catalog returned a malformed column.")
                    if object_id not in by_id:
                        raise SafetyError("Source catalog changed during discovery; retry only after reviewing a fresh Read.")
                    by_id[object_id]["columns"].append(column)
                inventory["objects"].extend(by_id.values())
                if probe(connection, context, texts, database, log, user) != identity:
                    raise SafetyError("Source identity or engine changed during discovery.")
        if len(canonical(inventory)) > 1_000_000:
            raise SafetyError("Inventory exceeds the bounded output size; no partial inventory was accepted.")
        if context.config_hash() != inventory["configHash"]:
            raise SafetyError("Configuration changed during discovery; no inventory was accepted.")
        schema = Draft202012Validator(load_json(context.plugin / "schemas" / "inventory.schema.json"))
        if next(schema.iter_errors(inventory), None) is not None:
            raise SafetyError("Source catalog did not satisfy the inventory contract; no partial inventory was accepted.")
        path = context.run_path(run_id, "inventory.json")
        atomic_json(path, inventory)
    return {
        "contractVersion": "1.0", "kind": "source-discovery", "runId": run_id,
        "status": "discovered", "inventoryHash": digest(inventory),
        "databaseCount": len(inventory["databases"]), "objectCount": len(inventory["objects"]),
        "definitionsExtracted": False,
    }
