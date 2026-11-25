import hashlib
import hmac
import secrets
from datetime import timedelta
from uuid import uuid4

from . import host
from .safety import SafetyError, digest, identifier, parse_time, reject_links, timestamp, utc_now
from .state import read_state


def mint(connection, context, binding, expires=None):
    grant_id, secret = str(uuid4()), secrets.token_urlsafe(32)
    connection.execute(
        "INSERT INTO native_grants VALUES(?,?,?,?,?,?, 'pending',?)",
        (grant_id, str(context.project), context.session, binding,
         hashlib.sha256(secret.encode()).hexdigest(), expires or timestamp(utc_now() + timedelta(minutes=60)), host.instance()),
    )
    return grant_id + "." + secret


def claim_path(context, grant_id):
    path = context.data / "claims" / identifier(grant_id)
    reject_links(path)
    return path


def claim(context, native_grant, binding):
    if not isinstance(native_grant, str) or native_grant.count(".") != 1:
        raise SafetyError("A fresh native pre-hook grant is required; direct or timed-out calls cannot apply changes.")
    grant_id, secret = native_grant.split(".")
    identifier(grant_id)
    with read_state(context) as connection:
        row = connection.execute(
            "SELECT secret_hash,expires_at,state,instance FROM native_grants "
            "WHERE grant_id=? AND project=? AND session=? AND binding=?",
            (grant_id, str(context.project), context.session, binding),
        ).fetchone()
    if (row is None or row[2] != "pending" or parse_time(row[1]) <= utc_now() or row[3] != host.instance()
            or not hmac.compare_digest(row[0], hashlib.sha256(secret.encode()).hexdigest())):
        raise SafetyError("Native grant is invalid, expired, used or bound to another action/session.")
    path = claim_path(context, grant_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="ascii") as output:
            output.write(digest(binding))
    except FileExistsError:
        raise SafetyError("Native grant has already been claimed; replay is prohibited.") from None
    return grant_id


def finish(connection, context, grant_id, binding):
    row = connection.execute(
        "SELECT expires_at,state,instance FROM native_grants WHERE grant_id=? AND project=? AND session=? AND binding=?",
        (identifier(grant_id), str(context.project), context.session, binding),
    ).fetchone()
    path = claim_path(context, grant_id)
    if (row is None or row[1] != "pending" or parse_time(row[0]) <= utc_now() or row[2] != host.instance()
            or not path.is_file() or path.read_text(encoding="ascii") != digest(binding)):
        raise SafetyError("The successful action has no matching claimed native grant.")
    connection.execute("UPDATE native_grants SET state='consumed' WHERE grant_id=?", (grant_id,))


def reads_allowed(context):
    if not context.registry.exists():
        return False
    with read_state(context) as connection:
        if not connection.execute("SELECT 1 FROM sqlite_master WHERE name='session_reads'").fetchone():
            return False
        row = connection.execute(
            "SELECT enabled,instance FROM session_reads WHERE project=? AND session=?",
            (str(context.project), context.session),
        ).fetchone()
    return row is not None and row[0] == 1 and row[1] == host.instance()


def reset_session(connection, context):
    connection.execute(
        "DELETE FROM source_sessions WHERE project=? AND session=?", (str(context.project), context.session),
    )
    connection.execute(
        "DELETE FROM session_reads WHERE project=? AND session=?", (str(context.project), context.session),
    )
    rows = connection.execute(
        "SELECT grant_id FROM native_grants WHERE project=? AND session=?", (str(context.project), context.session),
    ).fetchall()
    for (grant_id,) in rows:
        claim_path(context, grant_id).unlink(missing_ok=True)
    connection.execute(
        "DELETE FROM native_grants WHERE project=? AND session=?", (str(context.project), context.session),
    )
