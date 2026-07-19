import logging
from typing import Any
from urllib.parse import urlencode

from ..config import AgentConfig, PollingConfig
from ..http_client import ViewerClient
from ..services.commands import execute_user_command
from ..state import MAX_PROCESSED_USER_REQUEST_IDS, AgentState, save_state
from ..support.backend_requests import command_name, iter_user_requests, request_id


LOGGER = logging.getLogger("hospital_agent.user_requests")


def _user_request_was_processed(state: AgentState, current_request_id: str) -> bool:
    """Проверяет ID запроса по журналу и старому полю состояния."""
    return (
        current_request_id in state.processed_user_request_ids
        or current_request_id == state.last_user_request_id
    )


def _mark_user_request_processed(
    config: AgentConfig,
    state: AgentState,
    current_request_id: str,
) -> None:
    """Запоминает завершенный или намеренно проигнорированный запрос."""
    if current_request_id not in state.processed_user_request_ids:
        state.processed_user_request_ids.append(current_request_id)
    state.processed_user_request_ids = state.processed_user_request_ids[
        -MAX_PROCESSED_USER_REQUEST_IDS:
    ]
    state.pending_user_request_results.pop(current_request_id, None)
    state.last_user_request_id = current_request_id
    save_state(config.state_file, state)


def _result_delivery(
    config: AgentConfig,
    request_payload: dict[str, Any],
    request_id: str,
    command: str,
    ok: bool,
    retryable: bool = False,
    result: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any] | None:
    """Формирует сохраняемое подтверждение результата для backend."""
    endpoint = request_payload.get("response_endpoint") or request_payload.get("callback_endpoint")
    if not endpoint:
        return None
    payload = {
        "agent_id": int(config.agent_id),
        "ok": ok,
        "retryable": retryable,
        "result": result or {},
        "error": error,
    }
    return {"endpoint": str(endpoint), "payload": payload}


def _deliver_result(viewer: ViewerClient, delivery: dict[str, Any] | None) -> bool:
    """Отправляет сохраненный результат; legacy-запрос без callback считается подтвержденным."""
    if delivery is None:
        return True
    return viewer.post_json(str(delivery["endpoint"]), delivery["payload"])


def _finish_terminal_request(
    config: AgentConfig,
    viewer: ViewerClient,
    state: AgentState,
    current_request_id: str,
    delivery: dict[str, Any] | None,
) -> bool:
    """Надежно сохраняет terminal result до его подтверждения backend."""
    if delivery is not None:
        state.pending_user_request_results[current_request_id] = delivery
        save_state(config.state_file, state)
    if not _deliver_result(viewer, delivery):
        LOGGER.warning("Result acknowledgement failed for request %s", current_request_id)
        return False
    _mark_user_request_processed(config, state, current_request_id)
    return True


def run_user_request(
    config: AgentConfig,
    viewer: ViewerClient,
    state: AgentState,
    request_payload: dict[str, Any],
) -> bool:
    """Выполняет один backend-запрос из /user_requests."""
    current_request_id = request_id(request_payload)
    pending_delivery = state.pending_user_request_results.get(current_request_id)
    if pending_delivery is not None:
        if _deliver_result(viewer, pending_delivery):
            _mark_user_request_processed(config, state, current_request_id)
        return False
    if _user_request_was_processed(state, current_request_id):
        return False

    command = command_name(request_payload)
    if not command:
        LOGGER.warning("User request has no command/action/type: %s", request_payload)
        delivery = _result_delivery(
            config,
            request_payload,
            current_request_id,
            "",
            False,
            error="command is required",
        )
        _finish_terminal_request(config, viewer, state, current_request_id, delivery)
        return False

    try:
        result = execute_user_command(config, command, request_payload, current_request_id, viewer)
        if result is None:
            LOGGER.info("Ignoring unsupported user request command: %s", command)
            delivery = _result_delivery(
                config,
                request_payload,
                current_request_id,
                command,
                False,
                error=f"unsupported command: {command}",
            )
            _finish_terminal_request(config, viewer, state, current_request_id, delivery)
            return False
    except ValueError as exc:
        LOGGER.warning("Invalid user request %s: %s", current_request_id, exc)
        delivery = _result_delivery(
            config,
            request_payload,
            current_request_id,
            command,
            False,
            error=str(exc),
        )
        _finish_terminal_request(config, viewer, state, current_request_id, delivery)
        return False
    except Exception as exc:
        LOGGER.exception("User request %s failed", current_request_id)
        delivery = _result_delivery(
            config,
            request_payload,
            current_request_id,
            command,
            False,
            retryable=True,
            error=str(exc),
        )
        _deliver_result(viewer, delivery)
        # Не помечаем запрос завершенным: backend может вернуть его снова,
        # когда временная ошибка сети или внешнего сервиса будет устранена.
        return False

    delivery = _result_delivery(
        config,
        request_payload,
        current_request_id,
        command,
        True,
        result=result,
    )
    if not _finish_terminal_request(config, viewer, state, current_request_id, delivery):
        return False
    LOGGER.info("User request %s command=%s finished", current_request_id, command)
    return True


def poll_user_requests(
    config: AgentConfig,
    polling: PollingConfig,
    viewer: ViewerClient,
    state: AgentState,
) -> int:
    """Опрашивает backend_url/user_requests и выполняет поддержанные команды."""
    separator = "&" if "?" in polling.endpoint else "?"
    endpoint = f"{polling.endpoint}{separator}{urlencode({'agent_id': config.agent_id})}"
    payload = viewer.get_json(endpoint)
    processed_count = 0
    for request_payload in iter_user_requests(payload):
        if run_user_request(config, viewer, state, request_payload):
            processed_count += 1
    return processed_count
