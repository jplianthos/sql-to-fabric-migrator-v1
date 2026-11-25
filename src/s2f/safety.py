import hashlib
import copy
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path


class SafetyError(Exception):
    """An explicit validation failure; never a successful no-op."""


def utc_now():
    return datetime.now(timezone.utc)


def timestamp(value=None):
    return (value or utc_now()).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_time(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}Z", value):
        raise SafetyError("Timestamps must be UTC ISO 8601 with millisecond precision.")
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise SafetyError("Invalid timestamp.") from None


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(canonical(value)).hexdigest()


def file_hash(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            yield str(key)
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)


def reject_secrets(value):
    if isinstance(value, dict):
        for key, item in value.items():
            if re.fullmatch(r"(?i)(?:password|pwd|client_secret|access_token|refresh_token|accountkey|sharedaccesskey)", str(key)) and item:
                raise SafetyError("Potential secret content is prohibited.")
            reject_secrets(item)
    elif isinstance(value, list):
        for item in value:
            reject_secrets(item)
    text = "\n".join(strings(value))
    patterns = (
        r"(?i)\b(?:password|pwd|client_secret|access_token|refresh_token|accountkey|sharedaccesskey)\b['\"\s]*[:=]\s*['\"]?\S+",
        r"(?i)\bBearer\s+\S+",
        r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b",
        r"(?i)[?&]sig=[^&\s]+",
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
    )
    if any(re.search(pattern, text) for pattern in patterns):
        raise SafetyError("Potential secret content is prohibited.")


def load_json(path, limit=1_000_000):
    if not path.is_file():
        raise SafetyError("Required JSON input must be an existing regular file.")
    if path.stat().st_size > limit:
        raise SafetyError("Input exceeds the bounded skeleton file limit.")
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=unique_keys,
            parse_constant=lambda _: (_ for _ in ()).throw(ValueError()),
        )
    except (ValueError, UnicodeError):
        raise SafetyError("Invalid JSON input.") from None
    reject_secrets(value)
    return value


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise SafetyError("Duplicate JSON keys are prohibited.")
        result[key] = value
    return result


@lru_cache(maxsize=2)
def parsed_yaml(text):
    import yaml

    class StrictLoader(yaml.SafeLoader):
        depth = 0

        def compose_node(self, parent, index):
            if self.check_event(yaml.AliasEvent):
                raise SafetyError("YAML aliases are prohibited in configuration.")
            if self.peek_event().tag is not None:
                raise SafetyError("Explicit YAML tags are prohibited in configuration.")
            if self.depth >= 64:
                raise SafetyError("Configuration nesting exceeds the bounded depth limit.")
            self.depth += 1
            try:
                return super().compose_node(parent, index)
            finally:
                self.depth -= 1

        def construct_mapping(self, node, deep=False):
            pairs = [
                (self.construct_object(key, deep=deep), self.construct_object(value, deep=deep))
                for key, value in node.value
            ]
            if any(not isinstance(key, str) for key, _ in pairs):
                raise SafetyError("Configuration mapping keys must be strings.")
            return unique_keys(pairs)

    try:
        value = yaml.load(text, Loader=StrictLoader)
        canonical(value)
    except (yaml.YAMLError, ValueError, TypeError, UnicodeError):
        raise SafetyError("Invalid configuration YAML; use unique string keys and JSON-compatible values.") from None
    reject_secrets(value)
    return value


def load_yaml(path):
    if not path.is_file() or path.stat().st_size > 1_000_000:
        raise SafetyError("Configuration input must be an existing file of at most 1 MB.")
    reject_links(path)
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeError:
        raise SafetyError("Invalid UTF-8 configuration.") from None
    # Cache only parsed, validated content, never a path/hash observation or a caller's mutable copy.
    return copy.deepcopy(parsed_yaml(text))


def contained(path, root):
    resolved = Path(path).resolve()
    if not resolved.is_relative_to(Path(root).resolve()):
        raise SafetyError("Path leaves its permitted root.")
    return resolved


def reject_links(path):
    path = Path(path).absolute()
    if any(part.rstrip(" .") != part or ":" in part for part in path.parts[1:]):
        raise SafetyError("Ambiguous Windows path components and alternate data streams are prohibited.")
    for candidate in (path, *path.parents):
        if candidate.exists() and (
            candidate.is_symlink() or getattr(candidate, "is_junction", lambda: False)()
        ):
            raise SafetyError("Linked paths are prohibited for skeleton state and output.")


def atomic_json(path, value):
    reject_secrets(value)
    atomic_text(path, json.dumps(value, indent=2, allow_nan=False) + "\n")


def atomic_text(path, text):
    reject_links(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=".s2f-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as output:
            output.write(text)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,63}", value):
        raise SafetyError("Invalid identifier.")
    return value


@dataclass(frozen=True)
class Context:
    project: Path
    plugin: Path
    data: Path
    session: str

    @classmethod
    def create(cls, project, plugin, data, session):
        if not session or not isinstance(session, str) or len(session) > 200:
            raise SafetyError("A host session ID is required.")
        if any(character in session for character in ("'", "\x00", "\n", "\r")):
            raise SafetyError("Invalid host session ID.")
        for path in (project, plugin, data):
            if not Path(path).is_absolute():
                raise SafetyError("Project, plugin and data roots must be absolute.")
            if any(character in str(path) for character in ("'", "\x00", "\n", "\r")):
                raise SafetyError("Skeleton roots cannot contain quotes or control characters.")
        reject_links(plugin)
        cls(Path(project), Path(plugin), Path(data), session).check_blind(str(project), include_project=False)
        for path in (project, data):
            reject_links(path)
        project, plugin, data = (Path(path).resolve() for path in (project, plugin, data))
        if not project.is_dir() or not plugin.is_dir() or not data.is_dir():
            raise SafetyError("Project, plugin and plugin data directories must exist.")
        if project.is_relative_to(plugin) or plugin.is_relative_to(project):
            raise SafetyError("The chosen project and plugin installation must be separate trees.")
        if project.is_relative_to(data) or data.is_relative_to(project) or data.is_relative_to(plugin) or plugin.is_relative_to(data):
            raise SafetyError("Plugin state must be separate from project and installation.")
        return cls(project, plugin, data, session)

    def project_path(self, relative):
        path = self.project / relative
        self.check_blind(str(path))
        reject_links(path)
        path = contained(path, self.project)
        self.check_blind(str(path))
        return path

    def check_blind(self, value, include_project=True):
        groups = []
        for root in ((self.plugin, self.project) if include_project else (self.plugin,)):
            blind = root / ".blind-paths"
            if not blind.exists():
                continue
            reject_links(blind)
            if blind.stat().st_size > 32_768:
                raise SafetyError("Blind configuration exceeds the bounded hook size limit.")
            entries = blind.read_text(encoding="utf-8").splitlines()
            if len(entries) > 64:
                raise SafetyError("Skeleton blind configuration is limited to 64 entries.")
            groups.append(entries)
        if include_project:
            extra = self.runtime_options().get("G1")
            extra = [] if extra is None else extra
            if not isinstance(extra, list) or len(extra) > 64 or any(not isinstance(item, str) for item in extra):
                raise SafetyError("Invalid runtime blind paths.")
            groups.append(extra)
        for entries in groups:
            for entry in entries:
                entry = entry.strip()
                if not entry or entry.startswith("#"):
                    continue
                name = entry.rstrip("\\/").replace("\\", "/").rsplit("/", 1)[-1]
                if not name or name in (".", "..") or re.fullmatch(r"[A-Za-z]:", name):
                    raise SafetyError("Invalid blind-path configuration.")
                if re.search(r"(?<![\w.-])" + re.escape(name) + r"(?![\w.-])", value, re.IGNORECASE):
                    raise SafetyError("Configured blind path access is prohibited.")

    def runtime_options(self):
        path = self.project / "migration.config.yaml"
        if not path.exists():
            return {}
        reject_links(path)
        value = load_yaml(path)
        if (not isinstance(value, dict) or value.get("contractVersion") != "1.0"
                or not isinstance(value.get("options"), dict)):
            raise SafetyError("Configuration must contain the structured options contract.")
        options = value["options"]
        if options.get("G3") is not None and (type(options["G3"]) is not int or not 1 <= options["G3"] <= 60):
            raise SafetyError("Invalid configured plan expiry.")
        if options.get("G4") not in (None, "never", "owned-only"):
            raise SafetyError("Invalid configured delete policy.")
        return value["options"]

    def run_root(self):
        value = self.runtime_options().get("G2")
        value = "s2f-runs" if value is None else value
        if not isinstance(value, str) or not value.strip():
            raise SafetyError("G2 must be a project-contained folder.")
        path = self.project_path(value)
        parts = path.relative_to(self.project).parts
        if not parts or parts[0].casefold() in {
            ".git", ".github", "plans", "config-plans", "migration.config.yaml", ".blind-paths",
        } or parts[0].casefold().startswith(".env"):
            raise SafetyError("Run output cannot replace project or policy paths.")
        return path

    def run_path(self, run_id, *parts):
        return self.project_path(self.run_root() / identifier(run_id) / Path(*parts))

    def config_hash(self):
        path = self.project_path("migration.config.yaml")
        if not path.is_file():
            raise SafetyError("Configuration is unset; configure and approve it before planning.")
        if path.stat().st_size > 1_000_000:
            raise SafetyError("Configuration exceeds the bounded skeleton file limit.")
        reject_secrets({"content": path.read_text(encoding="utf-8")})
        return file_hash(path)

    @property
    def registry(self):
        path = self.data / "registry.sqlite3"
        reject_links(path)
        return path
