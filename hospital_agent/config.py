import json
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from threading import RLock
from typing import Any


DEFAULT_CONFIG_PATH = Path("agent_config.json")
DEFAULT_REQUEST_TIMEOUT_SECONDS = 30
DICOM_IMPORT_TIMEOUT_SECONDS = 1800


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
    plan_dir: Path
    report_dir: Path
    report_time: str
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
    viewer_url = str(raw_config["viewer_url"]).rstrip("/")
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
        _resolve_local_path(base_dir, path)
        for path in study_polling.operations_dirs or []
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
        plan_dir=_resolve_local_path(
            base_dir,
            raw_config.get("plan_dir", r"C:\Users\Angio_hir1\Desktop\План Отчеты"),
        ),
        report_dir=_resolve_local_path(
            base_dir,
            raw_config.get(
                "report_dir",
                r"C:\Users\Angio_hir1\Desktop\План Отчеты\отчеты",
            ),
        ),
        report_time=str(raw_config.get("report_time", "08:00")),
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
