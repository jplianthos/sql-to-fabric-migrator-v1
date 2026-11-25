import json
import hashlib
import re
import struct
import subprocess
from datetime import timedelta
from http.client import HTTPException
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener
from uuid import UUID

from .safety import SafetyError, digest, parse_time, timestamp, unique_keys, utc_now


API = "https://api.fabric.microsoft.com"
READ_PATHS = {"capacities": "/v1/capacities", "workspaces": "/v1/workspaces"}
STORAGE = "https://onelake.blob.fabric.microsoft.com"
STORAGE_RESOURCE = "https://storage.azure.com/"
SQL_RESOURCE = "https://database.windows.net/"


def _az(arguments):
    try:
        result = subprocess.run(
            ["pwsh", "-NoProfile", "-Command", "az " + " ".join(arguments)],
            text=True, encoding="utf-8", capture_output=True, timeout=45,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise SafetyError("Azure CLI did not complete; check the user sign-in without changing accounts automatically.") from None
    if result.returncode:
        raise SafetyError("Azure CLI command failed; verify its invocation and cached user sign-in. No account or permission was changed.")
    try:
        return json.loads(result.stdout, object_pairs_hook=unique_keys)
    except ValueError:
        raise SafetyError("Azure CLI returned invalid JSON; no raw output was logged.") from None


def check_identity(expected_user, expected_tenant, expected_subscription):
    account = _az(["account", "show", "--output", "json"])
    user = account.get("user") if isinstance(account, dict) else None
    if (not isinstance(user, dict) or user.get("type") != "user"
            or str(user.get("name", "")).casefold() != expected_user.casefold()
            or str(account.get("tenantId", "")).casefold() != expected_tenant.casefold()
            or str(account.get("id", "")).casefold() != expected_subscription.casefold()):
        raise SafetyError("Cached Azure user, tenant or subscription does not match the explicitly requested context.")


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        return None


def _get(kind, token):
    if kind not in READ_PATHS:
        raise SafetyError("Fabric supports only the bounded capacities/workspaces GET lookups.")
    request = Request(API + READ_PATHS[kind], headers={"Authorization": "Bearer " + token}, method="GET")
    try:
        with build_opener(_NoRedirect()).open(request, timeout=45) as response:
            if response.status != 200:
                raise SafetyError("Fabric did not return HTTP 200.")
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise SafetyError("Fabric first page exceeds the bounded response limit.")
        return json.loads(data, object_pairs_hook=unique_keys)
    except HTTPError as error:
        raise SafetyError(
            f"Fabric GET returned HTTP {error.code}; verify delegated read scopes and capacity/workspace access. No retry or write occurred."
        ) from None
    except (URLError, TimeoutError, ValueError):
        raise SafetyError("Fabric read failed; raw response/token contents were not logged.") from None


def lookup(kind, expected_user, expected_tenant, expected_subscription, redacted=False):
    if kind not in READ_PATHS or not isinstance(expected_user, str) or not expected_user.strip():
        raise SafetyError("An explicit lookup kind and expected user are required.")
    try:
        expected_tenant, expected_subscription = str(UUID(expected_tenant)), str(UUID(expected_subscription))
    except (ValueError, TypeError, AttributeError):
        raise SafetyError("The live lookup requires explicit tenant and subscription UUIDs.") from None
    token = _user_token({
        "identity": {"fabricUser": expected_user, "tenant": expected_tenant},
        "subscription": expected_subscription,
    })
    result = _get(kind, token)
    del token
    check_identity(expected_user, expected_tenant, expected_subscription)
    if not isinstance(result, dict) or not isinstance(result.get("value"), list):
        raise SafetyError("Fabric returned an invalid catalog envelope.")
    entries = result["value"]
    if any(not isinstance(item, dict) or not all(isinstance(item.get(key), str) and item[key] for key in ("id", "displayName")) for item in entries):
        raise SafetyError("Fabric returned an invalid catalog entry.")
    summary = {
        "operation": kind, "httpStatus": 200, "identityMatchedBeforeAndAfter": True,
        "firstPageOnly": True, "hasMore": bool(result.get("continuationToken") or result.get("continuationUri")),
        "hasResults": bool(entries), "readOnly": True,
    }
    if kind == "capacities":
        summary["hasActiveCapacity"] = any(item.get("state") == "Active" for item in entries)
    if not redacted:
        fields = ("id", "displayName", "sku", "region", "state") if kind == "capacities" else ("id", "displayName", "type", "capacityId")
        summary["choices"] = [{key: item[key] for key in fields if key in item} for item in entries]
    return summary


def _request(method, path, token, body=None):
    guid = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
    allowed = rf"/v1/(?:capacities|workspaces(?:/{guid}(?:/(?:lakehouses|warehouses)(?:/{guid})?)?)?|operations/{guid}(?:/result)?)"
    if not isinstance(path, str) or re.fullmatch(allowed, path) is None or method not in {"GET", "POST"}:
        raise SafetyError("Fabric request leaves the fixed provisioning API surface.")
    if method == "POST" and (re.fullmatch(rf"/v1/workspaces(?:/{guid}/(?:lakehouses|warehouses))?", path) is None or not isinstance(body, dict)):
        raise SafetyError("Only the exact planned resource-creation POST is supported.")
    return _send_json(method, path, token, body, {200} if method == "GET" else {201, 202})


def _send_json(method, path, token, body, allowed_status):
    request = Request(
        API + path, method=method,
        data=json.dumps(body, separators=(",", ":"), allow_nan=False).encode() if body is not None else None,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
    )
    try:
        with build_opener(_NoRedirect()).open(request, timeout=45) as response:
            status, headers = response.status, response.headers
            data = response.read(2_000_001)
        if len(data) > 2_000_000:
            raise SafetyError("Fabric response exceeds the bounded provisioning response limit.")
        if status not in allowed_status:
            raise SafetyError("Fabric returned an unsupported HTTP status; do not repeat the planned write.")
        result = json.loads(data, object_pairs_hook=unique_keys) if data else None
        if status != 202 and not isinstance(result, dict):
            raise SafetyError("Fabric returned an invalid resource/operation envelope.")
        return status, result, {
            "operationId": headers.get("x-ms-operation-id"),
            "retryAfter": headers.get("Retry-After"),
        }
    except HTTPError as error:
        raise SafetyError(f"Fabric {method} returned HTTP {error.code}; no retry, redirect, permission grant or account change was attempted.") from None
    except (OSError, ValueError, HTTPException):
        message = ("Fabric transport failed; the planned write may have occurred. Inspect Fabric before any recovery; no retry was attempted."
                   if method == "POST" else "Fabric read transport failed; no write or retry occurred.")
        raise SafetyError(message) from None


def _user_token(plan, resource=API):
    if resource not in {API, STORAGE_RESOURCE, SQL_RESOURCE}:
        raise SafetyError("Only the fixed Fabric, Storage and SQL user-token audiences are admitted.")
    identity = plan["identity"]
    check_identity(identity["fabricUser"], identity["tenant"], plan["subscription"])
    credential = _az([
        "account", "get-access-token", "--subscription", plan["subscription"],
        "--resource", resource, "--output", "json",
    ])
    if (not isinstance(credential, dict) or not isinstance(credential.get("tenant"), str)
            or credential["tenant"].casefold() != identity["tenant"]
            or not isinstance(credential.get("accessToken"), str) or not credential["accessToken"]):
        raise SafetyError("The user token does not match the explicitly planned tenant.")
    # Tokens remain in transport memory only, never receipts, evidence or exception text.
    return credential["accessToken"]


def _identity_after(plan):
    identity = plan["identity"]
    check_identity(identity["fabricUser"], identity["tenant"], plan["subscription"])


def validate_created(plan, resource, capacity):
    from .provisioning import uuid

    if not isinstance(resource, dict):
        raise SafetyError("Creation did not return a verifiable resource.")
    uuid(resource.get("id"))
    if resource.get("displayName") != plan["calls"][0]["body"]["displayName"] or resource.get("type") != plan["resourceType"]:
        raise SafetyError("Returned resource name/type does not match the exact create plan; no ownership was registered.")
    if plan["resourceType"] == "Workspace":
        if resource.get("capacityId") != capacity:
            raise SafetyError("Created workspace is not on the explicitly selected capacity.")
        keys = ("id", "displayName", "type", "capacityId")
    else:
        if resource.get("workspaceId") != plan["workspaces"][0]["id"]:
            raise SafetyError("Returned item leaves the approved, project-created workspace.")
        keys = ("id", "displayName", "type", "workspaceId")
    return {key: resource[key] for key in keys}


def _verify_created(plan, returned, token, capacity):
    from .provisioning import uuid

    if not isinstance(returned, dict):
        raise SafetyError("The create result is not a resource object; inspect Fabric before recovery.")
    resource_id = uuid(returned.get("id"))
    if returned.get("displayName") != plan["objects"][0] or returned.get("type") != plan["resourceType"]:
        raise SafetyError("The create result does not match the planned name/type.")
    if plan["resourceType"] != "Workspace" and returned.get("workspaceId") != plan["workspaces"][0]["id"]:
        raise SafetyError("The create result leaves the planned workspace.")
    path = plan["calls"][0]["path"] + "/" + resource_id
    _, confirmed, _ = _request("GET", path, token)
    if confirmed.get("id") != resource_id:
        raise SafetyError("Creation verification returned a different resource ID.")
    return validate_created(plan, confirmed, capacity)


def _next_poll(headers):
    value = headers.get("retryAfter")
    if isinstance(value, str):
        value = value.lstrip("0")
    if (not isinstance(value, str) or not value.isascii() or not value.isdecimal()
            or len(value) > 5 or not 1 <= int(value) <= 86400):
        raise SafetyError("Fabric did not supply a valid bounded Retry-After; do not reissue the create POST.")
    return timestamp(utc_now() + timedelta(seconds=int(value)))


def execute_plan(plan, context=None, native_grant=None):
    from . import approvals, provisioning
    from .state import read_state, validate_registered

    if context is not None and plan.get("kind") == "data-movement":
        from .movement import execute
        return execute(context, plan, native_grant)
    if context is None or plan.get("kind") != provisioning.KIND:
        raise SafetyError("Live execution of legacy/descriptive plans is disabled; only exact sandbox provisioning is supported.")
    with read_state(context) as connection:
        checked = validate_registered(connection, context, plan["planId"])
    if digest(checked) != digest(plan):
        raise SafetyError("The executor did not receive the registered exact plan.")
    provisioning.ensure_unattempted(context, plan)
    grant_id = approvals.claim(context, native_grant, provisioning.binding(plan))
    provisioning.check_scope(context, plan, grant_id)
    provisioning.check_windows(plan)
    token = _user_token(plan)
    selected = context.runtime_options()
    capacity = selected["B1"]["id"]
    if plan["resourceType"] == "Workspace":
        _, listing, _ = _request("GET", "/v1/capacities", token)
        entries = listing.get("value")
        if not isinstance(entries, list) or not any(
            isinstance(item, dict) and (item.get("id"), item.get("displayName"), item.get("state"))
            == (capacity, selected["B1"]["name"], "Active") for item in entries
        ):
            raise SafetyError("The explicitly named capacity is not active/visible on the bounded first page; no creation was attempted.")
    else:
        workspace = plan["workspaces"][0]
        _, current, _ = _request("GET", "/v1/workspaces/" + workspace["id"], token)
        if (current.get("id"), current.get("displayName"), current.get("capacityId"), current.get("type")) != (
            workspace["id"], workspace["name"], capacity, "Workspace",
        ):
            raise SafetyError("The project-created workspace was renamed, moved or is not accessible; no item was created.")
    _identity_after(plan)
    # Revalidate immediately before the single irreversible POST; no write retries.
    with read_state(context) as connection:
        validate_registered(connection, context, plan["planId"])
    provisioning.claim_target(context, plan, grant_id)
    call = plan["calls"][0]
    status, resource, headers = _request("POST", call["path"], token, call["body"])
    if status == 202:
        if plan["resourceType"] == "Workspace":
            raise SafetyError("Workspace creation returned an undocumented asynchronous result; inspect Fabric, never repeat the POST.")
        operation_id = provisioning.uuid(headers.get("operationId"))
        next_poll = _next_poll(headers)
        _identity_after(plan)
        return provisioning.publish(context, plan, grant_id, "pending", operation_id=operation_id, next_poll=next_poll)
    verified = _verify_created(plan, resource, token, capacity)
    _identity_after(plan)
    return provisioning.publish(context, plan, grant_id, "created", resource=verified)


def provisioning_status(context, plan_id):
    from . import provisioning
    from .state import read_state

    with read_state(context) as connection:
        plan, row = provisioning.job(context, connection, plan_id)
    grant_id, operation_id, next_poll = row[2:5]
    previous = row[6]
    if parse_time(next_poll) > utc_now():
        return provisioning.publish(
            context, plan, grant_id, "pending", operation_id=operation_id, next_poll=next_poll, previous=previous,
        )
    provisioning.check_windows(plan)
    token = _user_token(plan)
    path = "/v1/operations/" + provisioning.uuid(operation_id)
    _, state, headers = _request("GET", path, token)
    status = state.get("status")
    if status in ("NotStarted", "Running"):
        result = provisioning.publish(
            context, plan, grant_id, "pending", operation_id=operation_id, next_poll=_next_poll(headers), previous=previous,
        )
    elif status == "Failed":
        result = provisioning.publish(context, plan, grant_id, "failed", operation_id=operation_id, previous=previous)
    elif status == "Succeeded":
        _, resource, _ = _request("GET", path + "/result", token)
        verified = _verify_created(plan, resource, token, context.runtime_options()["B1"]["id"])
        result = provisioning.publish(
            context, plan, grant_id, "created", resource=verified, operation_id=operation_id, previous=previous,
        )
    else:
        raise SafetyError("Fabric operation status is unrecognized; no ownership was registered and no creation was repeated.")
    _identity_after(plan)
    return result


def _movement_request(method, path, token, body=None):
    guid = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
    base = rf"/v1/workspaces/{guid}/lakehouses/{guid}/tables"
    if method == "GET" and re.fullmatch(base + r"\?maxResults=100", path) and body is None:
        return _send_json(method, path, token, None, {200})
    if (method == "POST" and re.fullmatch(base + r"/[A-Za-z_][A-Za-z0-9_]{0,127}/load", path)
            and isinstance(body, dict) and set(body) == {"relativePath", "pathType", "mode", "recursive", "formatOptions"}
            and body["pathType"] == "File" and body["mode"] == "Append" and body["recursive"] is False
            and body["formatOptions"] == {"format": "Parquet"} and isinstance(body["relativePath"], str)
            and re.fullmatch(r"Files/s2f/[a-f0-9]{16}/[a-z0-9][a-z0-9-]{0,63}/" + guid + r"\.parquet", body["relativePath"])):
        return _send_json(method, path, token, body, {202})
    raise SafetyError("Movement request leaves the fixed schema-disabled Parquet load/list API surface.")


def _storage_request(method, path, token, content=None, etag=None, expected_size=None):
    from .data import MAX_BYTES

    guid = r"[a-f0-9]{8}(?:-[a-f0-9]{4}){3}-[a-f0-9]{12}"
    if (not isinstance(path, str) or re.fullmatch(
        rf"/{guid}/{guid}/Files/s2f/[a-f0-9]{{16}}/[a-z0-9][a-z0-9-]{{0,63}}/{guid}\.parquet", path,
    ) is None or method not in {"PUT", "GET"}):
        raise SafetyError("OneLake access leaves the exact new-file landing surface.")
    headers = {"Authorization": "Bearer " + token, "x-ms-version": "2021-06-08"}
    if method == "PUT":
        if not isinstance(content, bytes) or not 0 < len(content) <= MAX_BYTES or etag is not None:
            raise SafetyError("OneLake PUT requires the bounded exact artifact, never an update.")
        headers.update({
            "If-None-Match": "*", "x-ms-blob-type": "BlockBlob", "Content-Type": "application/octet-stream",
        })
    else:
        if (content is not None or not isinstance(etag, str) or not 1 <= len(etag) <= 512
                or any(ord(char) < 32 or ord(char) > 126 for char in etag)
                or type(expected_size) is not int or not 0 < expected_size <= MAX_BYTES):
            raise SafetyError("OneLake readback requires the exact created ETag and bounded expected file size.")
        headers["If-Match"] = etag
    request = Request(STORAGE + path, method=method, data=content, headers=headers)
    try:
        with build_opener(_NoRedirect()).open(request, timeout=60) as response:
            if response.status != (201 if method == "PUT" else 200) or response.headers.get("x-ms-rejected-headers"):
                raise SafetyError("OneLake did not honor the expected conditional request; inspect the retained attempt, never retry a PUT.")
            if method == "PUT":
                result = response.headers.get("ETag")
                if not isinstance(result, str) or not result or len(result) > 512:
                    raise SafetyError("OneLake returned no bounded created-file ETag; do not retry.")
                return result
            result = response.read(expected_size + 1)
            if len(result) != expected_size:
                raise SafetyError("OneLake readback length differs from the exact artifact.")
            return result
    except HTTPError as error:
        raise SafetyError(f"OneLake {method} returned HTTP {error.code}; no overwrite, redirect or retry occurred.") from None
    except (OSError, ValueError, HTTPException):
        raise SafetyError("OneLake transport failed; a PUT may have occurred. Inspect retained evidence; no retry was attempted.") from None


def _movement_preflight(context, plan, token):
    from .movement import target_scope
    from .state import read_state

    with read_state(context) as connection:
        workspace, name = target_scope(context, plan["spec"]["target"], connection)
    _, current, _ = _request("GET", "/v1/workspaces/" + workspace["id"], token)
    if (current.get("id"), current.get("displayName"), current.get("capacityId"), current.get("type")) != (
        workspace["id"], workspace["name"], context.runtime_options()["B1"]["id"], "Workspace",
    ):
        raise SafetyError("The target workspace moved, was renamed or no longer matches the configured capacity/fence.")
    target = plan["spec"]["target"]
    base = f"/v1/workspaces/{workspace['id']}/lakehouses/{target['lakehouseId']}"
    _, lakehouse, _ = _request("GET", base, token)
    properties = lakehouse.get("properties")
    if ((lakehouse.get("id"), lakehouse.get("displayName"), lakehouse.get("type"), lakehouse.get("workspaceId"))
            != (target["lakehouseId"], name, "Lakehouse", workspace["id"]) or not isinstance(properties, dict)
            or properties.get("defaultSchema") is not None
            or properties.get("oneLakeFilesPath") != (
                f"https://onelake.dfs.fabric.microsoft.com/{workspace['id']}/{target['lakehouseId']}/Files"
            )):
        raise SafetyError("The verified Lakehouse identity, schema mode or OneLake Files path differs from the planned target.")
    return base, lakehouse


def _table_state(plan, base, token, absent):
    _, result, _ = _movement_request("GET", base + "/tables?maxResults=100", token)
    if (not isinstance(result, dict) or not isinstance(result.get("data"), list)
            or len(result["data"]) > 100 or result.get("continuationToken") or result.get("continuationUri")
            or any(not isinstance(item, dict) or not isinstance(item.get("name"), str) for item in result["data"])):
        raise SafetyError("The complete bounded Lakehouse table list is unavailable; absence/existence cannot be established.")
    name = plan["spec"]["target"]["tableName"]
    matches = [item for item in result["data"] if item["name"].casefold() == name.casefold()]
    if absent:
        if matches:
            raise SafetyError("The target table already exists; it was not adopted, appended to or overwritten.")
    elif (len(matches) != 1 or matches[0].get("name") != name
          or matches[0].get("format") != "Delta" or matches[0].get("type") != "Managed"):
        raise SafetyError("The completed load has no exact visible managed Delta table; no successful migration is inferred.")


def upload_parquet(context, plan, content, before_write):
    from .movement import storage_path

    token = _user_token(plan)
    _movement_preflight(context, plan, token)
    storage_token = _user_token(plan, STORAGE_RESOURCE)
    _identity_after(plan)
    before_write()
    path = storage_path(context, plan)
    etag = _storage_request("PUT", path, storage_token, content)
    observed = _storage_request("GET", path, storage_token, etag=etag, expected_size=len(content))
    if hashlib.sha256(observed).hexdigest() != plan["artifact"]["sha256"]:
        raise SafetyError("OneLake readback SHA256 differs from the extracted artifact; no upload completion was recorded.")
    _identity_after(plan)
    return etag


def load_parquet(context, plan, content, before_write):
    from .movement import storage_path

    token = _user_token(plan)
    base, _ = _movement_preflight(context, plan, token)
    _table_state(plan, base, token, absent=True)
    # Verify the uploaded bytes immediately before the single load POST as well.
    storage_token = _user_token(plan, STORAGE_RESOURCE)
    path = storage_path(context, plan)
    upload_plan_id = plan["spec"]["previousPlanId"]
    from .movement import previous
    _, upload = previous(context, upload_plan_id, "upload")
    if upload["artifact"] != plan["artifact"]:
        raise SafetyError("Load artifact differs from its verified upload.")
    observed = _storage_request("GET", path, storage_token, etag=upload["artifact"]["etag"], expected_size=len(content))
    if hashlib.sha256(observed).hexdigest() != plan["artifact"]["sha256"]:
        raise SafetyError("Uploaded bytes changed before loading; no load was submitted.")
    _identity_after(plan)
    before_write()
    call = plan["calls"][0]
    _, _, headers = _movement_request("POST", call["path"], token, call["body"])
    from .provisioning import uuid
    operation = uuid(headers.get("operationId"))
    next_poll = _next_poll(headers)
    _identity_after(plan)
    return operation, next_poll


def load_parquet_status(context, plan, operation):
    from .provisioning import check_windows, uuid

    check_windows(plan)
    token = _user_token(plan)
    _, result, headers = _request("GET", "/v1/operations/" + uuid(operation), token)
    state = result.get("status")
    if state in ("NotStarted", "Running"):
        status, next_poll = "pending", _next_poll(headers)
    elif state == "Failed":
        status, next_poll = "failed", None
    elif state == "Succeeded":
        base, _ = _movement_preflight(context, plan, token)
        _table_state(plan, base, token, absent=False)
        status, next_poll = "loaded", None
    else:
        raise SafetyError("Unrecognized load operation status; no successful migration was inferred.")
    _identity_after(plan)
    return status, next_poll


def read_loaded_table(context, plan):
    import pyodbc
    from . import source
    from .provisioning import check_windows, uuid

    check_windows(plan)
    token = _user_token(plan)
    base, lakehouse = _movement_preflight(context, plan, token)
    _table_state(plan, base, token, absent=False)
    endpoint = lakehouse["properties"].get("sqlEndpointProperties")
    server = endpoint.get("connectionString") if isinstance(endpoint, dict) else None
    if (not isinstance(server, str) or re.fullmatch(r"[a-z0-9-]+\.datawarehouse\.fabric\.microsoft\.com", server) is None
            or endpoint.get("provisioningStatus") != "Success"):
        raise SafetyError("The exact Lakehouse SQL analytics endpoint is not ready or its hostname is unsupported; no refresh write occurred.")
    uuid(endpoint.get("id"))
    database = source.database_name(lakehouse["displayName"])
    encoded = _user_token(plan, SQL_RESOURCE).encode("utf-16-le")
    token_buffer = struct.pack("<I", len(encoded)) + encoded
    connection_string = (
        "Driver={ODBC Driver 18 for SQL Server};"
        f"Server=tcp:{server},1433;Database={{{database}}};"
        "Encrypt=yes;TrustServerCertificate=no;ApplicationIntent=ReadOnly;MARS_Connection=no;"
    )
    if "ODBC Driver 18 for SQL Server" not in pyodbc.drivers():
        raise SafetyError("ODBC Driver 18 is required for actual target readback.")
    try:
        connection = pyodbc.connect(
            connection_string, attrs_before={1256: token_buffer}, timeout=10, autocommit=True, readonly=True,
        )
    except pyodbc.Error:
        raise SafetyError("Encrypted user-token target connection failed; check endpoint permissions/readiness. No fallback or refresh write occurred.") from None
    identity_sql = "SELECT DB_NAME() AS databaseName, SUSER_SNAME() AS targetUser;"
    spec = plan["spec"]
    query = source.data_sql(
        context, {"schema": "dbo", "table": spec["target"]["tableName"]},
        [column["name"] for column in spec["columns"]], spec["keys"],
    )
    try:
        connection.timeout = spec["queryTimeout"]
        with source.audit_run(context, plan["runId"]) as log:
            def identity():
                result = source.execute_rows(connection, identity_sql, "target-identity", database, log, 1)
                if (len(result) != 1 or set(result[0]) != {"databaseName", "targetUser"}
                        or result[0]["databaseName"] != database or not isinstance(result[0]["targetUser"], str)
                        or result[0]["targetUser"].casefold() != plan["identity"]["fabricUser"].casefold()):
                    raise SafetyError("The target SQL database/user identity differs from the explicitly planned Lakehouse/user.")
            identity()
            rows = source.execute_rows(
                connection, query, "target-data", database, log, spec["maxRows"],
                columns=[column["name"] for column in spec["columns"]], max_bytes=64_000_000,
            )
            identity()
    finally:
        source.close(connection)
    _identity_after(plan)
    return rows
