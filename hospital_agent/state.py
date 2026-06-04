import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class AgentState:
    """Локальное состояние hospital_agent между циклами polling."""

    processed_protocols: dict[str, str] = field(default_factory=dict)
    last_agent_request_id: str | None = None
    last_agent_request_ids: dict[str, str] = field(default_factory=dict)


def load_state(path: Path) -> AgentState:
    """Загружает состояние агента из JSON-файла."""
    if not path.exists():
        return AgentState()
    try:
        with path.open("r", encoding="utf-8") as file:
            raw: dict[str, Any] = json.load(file)
    except (OSError, json.JSONDecodeError):
        return AgentState()

    return AgentState(
        processed_protocols={
            str(key): str(value) for key, value in raw.get("processed_protocols", {}).items()
        },
        last_agent_request_id=raw.get("last_agent_request_id"),
        last_agent_request_ids={
            str(key): str(value) for key, value in raw.get("last_agent_request_ids", {}).items()
        },
    )


def save_state(path: Path, state: AgentState) -> None:
    """Сохраняет состояние агента в JSON-файл."""
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "processed_protocols": state.processed_protocols,
        "last_agent_request_id": state.last_agent_request_id,
        "last_agent_request_ids": state.last_agent_request_ids,
    }
    with path.open("w", encoding="utf-8") as file:
        json.dump(payload, file, ensure_ascii=False, indent=2)
