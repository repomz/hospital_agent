import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


MAX_PROCESSED_USER_REQUEST_IDS = 1000


@dataclass
class AgentState:
    """Локальное состояние hospital_agent между циклами polling."""

    processed_protocols: dict[str, str] = field(default_factory=dict)
    last_agent_request_id: str | None = None
    last_agent_request_ids: dict[str, str] = field(default_factory=dict)
    last_user_request_id: str | None = None
    processed_user_request_ids: list[str] = field(default_factory=list)
    pending_user_request_results: dict[str, dict[str, Any]] = field(default_factory=dict)


def load_state(path: Path) -> AgentState:
    """Загружает состояние агента из JSON-файла."""
    if not path.exists():
        return AgentState()
    try:
        with path.open("r", encoding="utf-8") as file:
            raw: dict[str, Any] = json.load(file)
    except (OSError, json.JSONDecodeError):
        return AgentState()

    raw_last_user_request_id = raw.get("last_user_request_id")
    last_user_request_id = (
        str(raw_last_user_request_id) if raw_last_user_request_id not in (None, "") else None
    )
    raw_processed_user_request_ids = raw.get("processed_user_request_ids", [])
    if not isinstance(raw_processed_user_request_ids, list):
        raw_processed_user_request_ids = []
    processed_user_request_ids = [
        str(value)
        for value in raw_processed_user_request_ids
        if value not in (None, "")
    ][-MAX_PROCESSED_USER_REQUEST_IDS:]
    # Совместимость со state-файлом, записанным старыми версиями агента.
    if last_user_request_id and str(last_user_request_id) not in processed_user_request_ids:
        processed_user_request_ids.append(str(last_user_request_id))
        processed_user_request_ids = processed_user_request_ids[-MAX_PROCESSED_USER_REQUEST_IDS:]
    raw_pending_results = raw.get("pending_user_request_results", {})
    if not isinstance(raw_pending_results, dict):
        raw_pending_results = {}
    pending_user_request_results = {
        str(key): value
        for key, value in raw_pending_results.items()
        if isinstance(value, dict)
    }

    return AgentState(
        processed_protocols={
            str(key): str(value) for key, value in raw.get("processed_protocols", {}).items()
        },
        last_agent_request_id=raw.get("last_agent_request_id"),
        last_agent_request_ids={
            str(key): str(value) for key, value in raw.get("last_agent_request_ids", {}).items()
        },
        last_user_request_id=last_user_request_id,
        processed_user_request_ids=processed_user_request_ids,
        pending_user_request_results=pending_user_request_results,
    )


def save_state(path: Path, state: AgentState) -> None:
    """Атомарно сохраняет состояние агента в JSON-файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed_protocols": state.processed_protocols,
        "last_agent_request_id": state.last_agent_request_id,
        "last_agent_request_ids": state.last_agent_request_ids,
        "last_user_request_id": state.last_user_request_id,
        "processed_user_request_ids": state.processed_user_request_ids,
        "pending_user_request_results": state.pending_user_request_results,
    }
    temporary_path = path.with_name(f".{path.name}.tmp")
    with temporary_path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    temporary_path.replace(path)
