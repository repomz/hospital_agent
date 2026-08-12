import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import Any


MAX_PROCESSED_USER_REQUEST_IDS = 1000


@dataclass
class AgentState:
    """Локальное состояние hospital_agent между циклами polling."""

    processed_protocols: dict[str, str] = field(default_factory=dict)
    processed_protocol_keys: list[str] = field(default_factory=list)
    last_user_request_id: str | None = None
    processed_user_request_ids: list[str] = field(default_factory=list)
    pending_user_request_results: dict[str, dict[str, Any]] = field(default_factory=dict)
    polling_enabled_at: dict[str, str] = field(default_factory=dict)
    processed_modality_studies: dict[str, list[str]] = field(default_factory=dict)
    pending_xa_studies: dict[str, dict[str, Any]] = field(default_factory=dict)
    yandex_cleanup: list[dict[str, Any]] = field(default_factory=list)
    last_report_date: str | None = None
    lock: Any = field(default_factory=RLock, repr=False, compare=False)


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
    raw_processed_protocol_keys = raw.get("processed_protocol_keys", [])
    if not isinstance(raw_processed_protocol_keys, list):
        raw_processed_protocol_keys = []

    return AgentState(
        processed_protocols={
            str(key): str(value) for key, value in raw.get("processed_protocols", {}).items()
        },
        processed_protocol_keys=[
            str(value)
            for value in raw_processed_protocol_keys
            if value not in (None, "")
        ],
        last_user_request_id=last_user_request_id,
        processed_user_request_ids=processed_user_request_ids,
        pending_user_request_results=pending_user_request_results,
        polling_enabled_at={
            str(key): str(value)
            for key, value in raw.get("polling_enabled_at", {}).items()
        },
        processed_modality_studies={
            str(key): [str(item) for item in value]
            for key, value in raw.get("processed_modality_studies", {}).items()
            if isinstance(value, list)
        },
        pending_xa_studies={
            str(key): value
            for key, value in raw.get("pending_xa_studies", {}).items()
            if isinstance(value, dict)
        },
        yandex_cleanup=[
            item for item in raw.get("yandex_cleanup", []) if isinstance(item, dict)
        ],
        last_report_date=raw.get("last_report_date"),
    )


def save_state(path: Path, state: AgentState) -> None:
    """Атомарно сохраняет состояние агента в JSON-файл."""
    with state.lock:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "processed_protocols": state.processed_protocols,
            "processed_protocol_keys": state.processed_protocol_keys,
            "last_user_request_id": state.last_user_request_id,
            "processed_user_request_ids": state.processed_user_request_ids,
            "pending_user_request_results": state.pending_user_request_results,
            "polling_enabled_at": state.polling_enabled_at,
            "processed_modality_studies": state.processed_modality_studies,
            "pending_xa_studies": state.pending_xa_studies,
            "yandex_cleanup": state.yandex_cleanup,
            "last_report_date": state.last_report_date,
        }
        temporary_path = path.with_name(f".{path.name}.tmp")
        with temporary_path.open("w", encoding="utf-8") as file:
            json.dump(payload, file, ensure_ascii=False, indent=2)
            file.flush()
            os.fsync(file.fileno())
        temporary_path.replace(path)
