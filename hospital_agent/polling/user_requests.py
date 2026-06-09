import logging
from datetime import datetime, timezone
from typing import Any

from ..config import AgentConfig, PollingConfig
from ..http_client import ViewerClient
from ..services.commands import execute_user_command
from ..state import AgentState, save_state
from ..support.backend_requests import command_name, iter_user_requests, request_id


LOGGER = logging.getLogger("hospital_agent.user_requests")


def _post_result(
    viewer: ViewerClient,
    request_payload: dict[str, Any],
    request_id: str,
    command: str,
    ok: bool,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    """Отправляет результат выполнения команды, если backend передал endpoint."""
    endpoint = request_payload.get("response_endpoint") or request_payload.get("callback_endpoint")
    if not endpoint:
        return
    payload = {
        "request_id": request_id,
        "command": command,
        "ok": ok,
        "result": result or {},
        "error": error,
        "sent_at": datetime.now(timezone.utc).isoformat(),
    }
    viewer.post_json(str(endpoint), payload)


def run_user_request(
    config: AgentConfig,
    viewer: ViewerClient,
    state: AgentState,
    request_payload: dict[str, Any],
) -> bool:
    """Выполняет один backend-запрос из /user_requests."""
    current_request_id = request_id(request_payload)
    if current_request_id == state.last_user_request_id:
        return False

    command = command_name(request_payload)
    if not command:
        LOGGER.warning("User request has no command/action/type: %s", request_payload)
        state.last_user_request_id = current_request_id
        save_state(config.state_file, state)
        return False

    try:
        result = execute_user_command(config, command, request_payload, current_request_id)
        if result is None:
            LOGGER.info("Ignoring unsupported user request command: %s", command)
            return False
    except Exception as exc:
        LOGGER.exception("User request %s failed", current_request_id)
        _post_result(viewer, request_payload, current_request_id, command, False, error=str(exc))
        state.last_user_request_id = current_request_id
        save_state(config.state_file, state)
        return False

    _post_result(viewer, request_payload, current_request_id, command, True, result=result)
    state.last_user_request_id = current_request_id
    save_state(config.state_file, state)
    LOGGER.info("User request %s command=%s finished", current_request_id, command)
    return True


def poll_user_requests(
    config: AgentConfig,
    polling: PollingConfig,
    viewer: ViewerClient,
    state: AgentState,
) -> int:
    """Опрашивает backend_url/user_requests и выполняет поддержанные команды."""
    payload = viewer.get_json(polling.endpoint)
    processed_count = 0
    for request_payload in iter_user_requests(payload):
        if run_user_request(config, viewer, state, request_payload):
            processed_count += 1
    return processed_count
