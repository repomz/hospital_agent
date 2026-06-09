import hashlib
import json
from typing import Any


def request_id(payload: Any) -> str:
    """Возвращает id backend-запроса или стабильный hash payload."""
    if isinstance(payload, dict):
        for key in ("request_id", "id", "uuid"):
            if payload.get(key):
                return str(payload[key])
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def iter_user_requests(payload: Any) -> list[dict[str, Any]]:
    """Приводит ответ /user_requests к списку dict-запросов."""
    if payload in (None, "", [], {}):
        return []
    if isinstance(payload, dict):
        if isinstance(payload.get("requests"), list):
            return [item for item in payload["requests"] if isinstance(item, dict)]
        if isinstance(payload.get("data"), list):
            return [item for item in payload["data"] if isinstance(item, dict)]
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def command_name(payload: dict[str, Any]) -> str:
    """Извлекает имя команды из backend-запроса."""
    return str(payload.get("command") or payload.get("action") or payload.get("type") or "").lower()
