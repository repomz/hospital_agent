import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PureWindowsPath
from threading import RLock
from typing import Any

DEFAULT_CONFIG_PATH = Path("agent_config.json")
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
DICOM_IMPORT_TIMEOUT_SECONDS = 1800


def record_agent_version(config: "AgentConfig", version: str) -> None:
    """Record the running code version without replacing user configuration."""
    with config.lock:
        raw = json.loads(config.config_path.read_text(encoding="utf-8"))
        original = raw.copy()
        if "updated_at" not in raw:
            raw["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        ordered = {}
        for key, value in raw.items():
            if key == "version":
                continue
            ordered[key] = value
            if key == "updated_at":
                ordered["version"] = version
        if list(original.items()) == list(ordered.items()):
            return
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=config.config_path.parent,
                prefix=".agent-version-",
                suffix=".tmp",
                delete=False,
            ) as file:
                temporary = Path(file.name)
                json.dump(ordered, file, ensure_ascii=False, indent=2)
                file.write("\n")
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, config.config_path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def canonical_viewer_url(value: str) -> str:
    """Migrate only known production IP URLs; retain custom/local installations."""
    value = value.strip().rstrip("/")
    for address in ("135.106.195.161", "135.106.130.37"):
        for scheme in ("http", "https"):
            if value == f"{scheme}://{address}/api":
                return "https://angio.su/api"
    return value


def apply_remote_configuration(config: "AgentConfig", changes: dict) -> None:
    """Validate the entire edit, persist atomically, then update live polling objects."""
    names = ("study_polling", "xa_polling", "ct_polling", "user_requests_polling")
    if set(changes) != {"description", "heartbeat_interval_min", *names}:
        raise ValueError("Unsupported or missing configuration fields")

    def interval(value, low=0.25, high=1440):
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float))
            or not low <= value <= high
        ):
            raise ValueError("Invalid polling interval")
        return float(value)

    heartbeat = interval(changes["heartbeat_interval_min"], 1, 60)
    if not isinstance(changes["description"], str) or len(changes["description"]) > 200:
        raise ValueError("Invalid description")
    for name in names:
        p = changes[name]
        allowed = (
            {"state", "interval_min", "operations_dir"}
            if name == "study_polling"
            else {"state", "interval_min"}
        )
        if not isinstance(p, dict) or set(p) - allowed or not isinstance(p.get("state"), bool):
            raise ValueError("Invalid polling settings")
        interval(p.get("interval_min"))
    if not changes["user_requests_polling"]["state"]:
        raise ValueError("Command polling cannot be disabled remotely")
    dirs = changes["study_polling"].get("operations_dir", [])
    if (
        not isinstance(dirs, list)
        or len(dirs) > 20
        or (changes["study_polling"]["state"] and not dirs)
    ):
        raise ValueError("Specify protocol directories")
    resolved = []
    for value in dirs:
        if not isinstance(value, str) or not value.strip() or len(value) > 1024:
            raise ValueError("Invalid protocol directory")
        path = _resolve_local_path(config.config_path.parent, value)
        if not path.is_dir():
            raise ValueError(f"Protocol directory is unavailable: {value}")
        resolved.append(path)
    with config.lock:
        raw = json.loads(config.config_path.read_text(encoding="utf-8"))
        raw["description"] = changes["description"]
        raw["alive_polling_interval_min"] = heartbeat
        for name in names:
            raw[name] = {**raw.get(name, {}), **changes[name]}
        raw["study_polling"].pop("operations_dirs", None)
        raw["study_polling"]["operations_dir"] = dirs
        raw["updated_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
        temporary = config.config_path.with_name(".remote-config.tmp")
        try:
            with temporary.open("w", encoding="utf-8") as file:
                json.dump(raw, file, ensure_ascii=False, indent=2)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, config.config_path)
        finally:
            temporary.unlink(missing_ok=True)
        config.description = changes["description"]
        config.alive_polling_interval_min = heartbeat
        for name in names:
            polling = getattr(config, name)
            polling.state = changes[name]["state"]
            polling.interval_min = float(changes[name]["interval_min"])
        config.study_polling.operations_dirs = resolved


@dataclass
class PollingConfig:
    """Настройки одного polling-направления hospital_agent."""

    state: bool
    interval_min: float
    operations_dirs: list[Path] | None = None


@dataclass
class AgentConfig:
    """Настройки постоянного hospital_agent из agent_config.json."""

    config_path: Path
    viewer_url: str
    description: str
    log_dir: Path
    state_file: Path
    pacs_config_path: Path
    agent_id: str
    alive_polling_interval_min: float
    user_requests_polling: PollingConfig
    ct_polling: PollingConfig
    xa_polling: PollingConfig
    study_polling: PollingConfig
    lock: RLock


def _as_path_list(values: list[str] | str | None) -> list[Path]:
    """Преобразует строку или список строк в список путей."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return [Path(value) for value in values]


def _resolve_local_path(base_dir: Path, value: str | Path) -> Path:
    """Разрешает относительный путь от каталога agent_config.json."""
    raw_value = str(value)
    path = Path(raw_value).expanduser()
    if path.is_absolute() or PureWindowsPath(raw_value).is_absolute():
        return path
    return base_dir / path


def _polling_config(
    raw_config: dict[str, Any],
    name: str,
) -> PollingConfig:
    """Загружает минимальный блок polling из agent_config.json."""
    raw_polling = raw_config.get(name, {})
    return PollingConfig(
        state=bool(raw_polling.get("state", False)),
        interval_min=float(raw_polling.get("interval_min", 10)),
        operations_dirs=_as_path_list(
            raw_polling.get("operations_dirs", raw_polling.get("operations_dir"))
        )
        if name == "study_polling"
        else None,
    )


def load_agent_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AgentConfig:
    """Загружает agent_config.json и приводит его к настройкам сервиса."""
    config_path = Path(path).expanduser()
    if not config_path.is_absolute():
        config_path = Path.cwd() / config_path
    config_path = config_path.resolve()
    base_dir = config_path.parent
    with config_path.open("r", encoding="utf-8") as file:
        raw_config: dict[str, Any] = json.load(file)
    viewer_url = canonical_viewer_url(str(raw_config["viewer_url"]))
    if not viewer_url.startswith(("http://", "https://")):
        raise ValueError("viewer_url must start with http:// or https://")
    agent_id = str(raw_config.get("agent_id", "")).strip()
    try:
        parsed_agent_id = int(agent_id)
    except ValueError as exc:
        raise ValueError("agent_id must be a positive integer") from exc
    if parsed_agent_id <= 0:
        raise ValueError("agent_id must be a positive integer")

    study_polling = _polling_config(raw_config, "study_polling")
    study_polling.operations_dirs = [
        _resolve_local_path(base_dir, path) for path in study_polling.operations_dirs or []
    ]

    return AgentConfig(
        config_path=config_path,
        viewer_url=viewer_url,
        description=str(raw_config.get("description", "")),
        log_dir=_resolve_local_path(base_dir, raw_config.get("log_dir", "logs/agent")),
        state_file=_resolve_local_path(
            base_dir,
            raw_config.get("state_file", "logs/agent/state.json"),
        ),
        pacs_config_path=_resolve_local_path(
            base_dir,
            raw_config.get("pacs_config_path", "config.json"),
        ),
        agent_id=agent_id,
        alive_polling_interval_min=float(raw_config.get("alive_polling_interval_min", 5)),
        user_requests_polling=_polling_config(raw_config, "user_requests_polling"),
        ct_polling=_polling_config(raw_config, "ct_polling"),
        xa_polling=_polling_config(raw_config, "xa_polling"),
        study_polling=study_polling,
        lock=RLock(),
    )


def update_polling_state(config: AgentConfig, modality: str, enabled: bool) -> None:
    """Атомарно меняет polling state в памяти и agent_config.json."""
    modality = modality.lower()
    if modality not in {"ct", "xa"}:
        raise ValueError(f"unsupported polling modality: {modality}")
    polling_name = f"{modality}_polling"
    polling = getattr(config, polling_name)
    with config.lock:
        polling.state = enabled
        with config.config_path.open("r", encoding="utf-8") as file:
            raw_config: dict[str, Any] = json.load(file)
        raw_polling = raw_config.setdefault(polling_name, {})
        if not isinstance(raw_polling, dict):
            raw_polling = {}
            raw_config[polling_name] = raw_polling
        raw_polling["state"] = enabled
        temporary_path = config.config_path.with_name(f".{config.config_path.name}.tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(raw_config, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(config.config_path)
