import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any


DEFAULT_CONFIG_PATH = Path("agent_config.json")
POLLING_WORK_OPTIONS = {"on_time", "on_request", "interval", "exit_session", "logging_session"}
ENVIRONMENTS = {"prod", "dev", "test"}


@dataclass(frozen=True)
class PollingConfig:
    """Настройки одного polling-направления hospital_agent."""

    state: bool
    work_option: str
    interval_min: float
    on_time: str
    endpoint: str
    period: str = "today"
    operations_dirs: list[Path] | None = None


@dataclass(frozen=True)
class AgentConfig:
    """Настройки постоянного hospital_agent из agent_config.json."""

    viewer_url: str
    environment: str
    description: str
    log_dir: Path
    state_file: Path
    pacs_config_path: Path
    agent_id: str
    request_timeout_seconds: int
    alive_polling_interval_min: float
    user_requests_polling: PollingConfig
    ct_polling: PollingConfig
    xa_polling: PollingConfig
    study_polling: PollingConfig


def _as_path_list(values: list[str] | str | None) -> list[Path]:
    """Преобразует строку или список строк в список путей."""
    if values is None:
        return []
    if isinstance(values, str):
        values = [values]
    return [Path(value) for value in values]


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивно накладывает override-настройки окружения на базовый конфиг."""
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def _apply_environment(raw_config: dict[str, Any]) -> dict[str, Any]:
    """Возвращает конфиг с примененным профилем prod/dev/test."""
    environment = str(raw_config.get("environment", "prod")).strip()
    if environment not in ENVIRONMENTS:
        raise ValueError(f"environment must be one of {sorted(ENVIRONMENTS)}, got {environment!r}")

    environments = raw_config.get("environments", {})
    if not isinstance(environments, dict):
        raise ValueError("environments must be an object with prod/dev/test keys")

    base_config = {key: value for key, value in raw_config.items() if key != "environments"}
    override = environments.get(environment, {})
    if not isinstance(override, dict):
        raise ValueError(f"environments.{environment} must be an object")

    merged = _deep_merge(base_config, override)
    merged["environment"] = environment
    return merged


def _polling_config(
    raw_config: dict[str, Any],
    name: str,
    endpoint: str,
    period: str = "today",
) -> PollingConfig:
    """Загружает и валидирует один блок polling из agent_config.json."""
    raw_polling = raw_config.get(name, {})
    work_option = str(raw_polling.get("work_option", "interval")).strip()
    if work_option not in POLLING_WORK_OPTIONS:
        raise ValueError(
            f"{name}.work_option must be one of {sorted(POLLING_WORK_OPTIONS)}, got {work_option!r}"
        )

    polling = PollingConfig(
        state=bool(raw_polling.get("state", False)),
        work_option=work_option,
        interval_min=float(raw_polling.get("interval_min", 10)),
        on_time=str(raw_polling.get("on_time", "07:30")),
        endpoint=str(raw_polling.get("endpoint", endpoint)),
        period=str(raw_polling.get("period", period)),
        operations_dirs=_as_path_list(
            raw_polling.get("operations_dirs", raw_polling.get("operations_dir"))
        ),
    )
    if name != "study_polling":
        return replace(polling, operations_dirs=None)
    return polling


def load_agent_config(path: str | Path = DEFAULT_CONFIG_PATH) -> AgentConfig:
    """Загружает agent_config.json и приводит его к настройкам сервиса."""
    config_path = Path(path)
    with config_path.open("r", encoding="utf-8") as file:
        raw_config: dict[str, Any] = json.load(file)
    raw_config = _apply_environment(raw_config)

    viewer_url = str(raw_config["viewer_url"]).rstrip("/")
    return AgentConfig(
        viewer_url=viewer_url,
        environment=str(raw_config.get("environment", "prod")),
        description=str(raw_config.get("description", "")),
        log_dir=Path(raw_config.get("log_dir", "logs/agent")),
        state_file=Path(raw_config.get("state_file", "logs/agent/state.json")),
        pacs_config_path=Path(raw_config.get("pacs_config_path", "config.json")),
        agent_id=str(raw_config.get("agent_id", "hospital-agent")),
        request_timeout_seconds=int(raw_config.get("request_timeout_seconds", 30)),
        alive_polling_interval_min=float(raw_config.get("alive_polling_interval_min", 5)),
        user_requests_polling=_polling_config(raw_config, "user_requests_polling", "/user_requests"),
        ct_polling=_polling_config(raw_config, "ct_polling", "/ct_studies"),
        xa_polling=_polling_config(raw_config, "xa_polling", "/xa_studies"),
        study_polling=_polling_config(raw_config, "study_polling", "/studies"),
    )
