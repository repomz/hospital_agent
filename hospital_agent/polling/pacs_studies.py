import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from ..config import AgentConfig, PollingConfig
from ..http_client import ViewerClient
from ..state import AgentState, save_state


LOGGER = logging.getLogger("hospital_agent.pacs")


def _request_id(payload: Any) -> str:
    """Возвращает идентификатор запроса viewer или стабильный hash payload."""
    if isinstance(payload, dict):
        for key in ("request_id", "id", "uuid"):
            if payload.get(key):
                return str(payload[key])
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def should_poll_pacs_studies(
    payload: Any,
    state: AgentState,
    modality: str,
) -> tuple[bool, str | None]:
    """Определяет, содержит ли /agent_request новый запрос на опрос PACS."""
    if payload in (None, "", [], {}):
        return False, None

    if isinstance(payload, list):
        if not payload:
            return False, None
        payload = payload[0]

    if not isinstance(payload, dict):
        request_id = _request_id(payload)
        return request_id != state.last_agent_request_ids.get(modality.upper()), request_id

    status = str(payload.get("status", "")).lower()
    if status in {"empty", "none", "no_request", "idle"}:
        return False, None

    modality_lower = modality.lower()
    allowed_actions = {
        f"poll_{modality_lower}_studies",
        f"{modality_lower}_studies",
        f"find_{modality_lower}_today",
    }
    action = str(payload.get("action") or payload.get("type") or payload.get("command") or "").lower()
    if action and action not in allowed_actions:
        return False, None

    if payload.get("enabled") is False:
        return False, None

    request_id = _request_id(payload)
    return request_id != state.last_agent_request_ids.get(modality.upper()), request_id


def find_pacs_studies(config: AgentConfig, modality: str, period: str) -> list[dict[str, Any]]:
    """Выполняет PACS C-FIND через существующий PACSClient."""
    from ..services.pacs import PACSClient
    from ..support.dicom import load_pacs_config

    pacs_config = load_pacs_config(str(config.pacs_config_path))
    client = PACSClient(pacs_config)
    return client.find_studies(modality=modality, period=period)


def poll_agent_request_for_modality(
    config: AgentConfig,
    polling: PollingConfig,
    modality: str,
    viewer: ViewerClient,
    state: AgentState,
) -> bool:
    """Опрашивает /agent_request и при запросе отправляет PACS studies на viewer."""
    request_payload = viewer.get_json("/agent_request")
    should_run, request_id = should_poll_pacs_studies(request_payload, state, modality)
    if not should_run:
        return False

    return run_pacs_polling(config, polling, modality, viewer, state, request_id)


def run_pacs_polling(
    config: AgentConfig,
    polling: PollingConfig,
    modality: str,
    viewer: ViewerClient,
    state: AgentState,
    request_id: str | None = None,
) -> bool:
    """Выполняет PACS FIND по модальности и отправляет результат в viewer."""
    studies = find_pacs_studies(config, modality=modality, period=polling.period)
    response_payload = {
        "agent_id": config.agent_id,
        "request_id": request_id,
        "source": "pacs_find",
        "query": {"modality": modality, "period": polling.period},
        "studies": studies,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    if viewer.post_json(polling.endpoint, response_payload):
        if request_id is not None:
            state.last_agent_request_id = request_id
            state.last_agent_request_ids[modality.upper()] = request_id
        save_state(config.state_file, state)
        LOGGER.info("Sent %s %s studies for request %s", len(studies), modality, request_id)
        return True
    return False
