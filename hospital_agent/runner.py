import logging
import signal
import time
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .alive import send_alive
from .config import AgentConfig, PollingConfig
from .http_client import ViewerClient
from .pacs import poll_agent_request_for_modality, run_pacs_polling
from .protocols import poll_operation_protocols
from .state import AgentState, load_state


LOGGER = logging.getLogger("hospital_agent")
_running = True


@dataclass
class PollingRuntime:
    """Runtime-состояние одного polling-направления."""

    name: str
    config: PollingConfig
    run: Callable[[], None]
    next_run_at: float = 0.0
    last_on_time_date: str | None = None
    startup_done: bool = False


def request_stop(signum: int, frame: object) -> None:
    """Запрашивает мягкую остановку постоянного агента."""
    global _running
    LOGGER.info("Stop signal received: %s", signum)
    _running = False


def configure_signals() -> None:
    """Подключает обработчики SIGINT/SIGTERM для фонового сервиса."""
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def _on_time_due(runtime: PollingRuntime, now_datetime: datetime) -> bool:
    """Проверяет, наступило ли ежедневное время запуска polling."""
    today = now_datetime.date().isoformat()
    if runtime.last_on_time_date == today:
        return False

    try:
        hour, minute = [int(part) for part in runtime.config.on_time.split(":", 1)]
    except ValueError:
        LOGGER.warning("%s has invalid on_time=%s", runtime.name, runtime.config.on_time)
        return False

    if (now_datetime.hour, now_datetime.minute) < (hour, minute):
        return False
    runtime.last_on_time_date = today
    return True


def _run_runtime(runtime: PollingRuntime) -> None:
    """Запускает polling runtime и логирует ошибки без остановки сервиса."""
    try:
        runtime.run()
    except Exception:
        LOGGER.exception("%s polling failed", runtime.name)


def _runtime_due(runtime: PollingRuntime, now_monotonic: float, now_datetime: datetime) -> bool:
    """Определяет, должен ли polling выполниться на текущей итерации."""
    option = runtime.config.work_option
    if option in {"interval", "on_request"}:
        return now_monotonic >= runtime.next_run_at
    if option == "on_time":
        return _on_time_due(runtime, now_datetime)
    if option == "logging_session":
        return not runtime.startup_done
    return False


def _schedule_next(runtime: PollingRuntime, now_monotonic: float) -> None:
    """Планирует следующий запуск polling после выполненного прохода."""
    runtime.startup_done = True
    if runtime.config.work_option in {"interval", "on_request"}:
        runtime.next_run_at = now_monotonic + max(runtime.config.interval_min * 60, 1)


def _build_runtimes(
    config: AgentConfig,
    viewer: ViewerClient,
    state: AgentState,
) -> list[PollingRuntime]:
    """Создает runtime-объекты для активных блоков ct/xa/study polling."""
    runtimes: list[PollingRuntime] = []

    if config.study_polling.state:
        runtimes.append(
            PollingRuntime(
                name="study_polling",
                config=config.study_polling,
                run=lambda: LOGGER.info(
                    "Operation protocol polling finished: sent=%s",
                    poll_operation_protocols(config, config.study_polling, viewer, state),
                ),
            )
        )

    for name, modality, polling in (
        ("ct_polling", "CT", config.ct_polling),
        ("xa_polling", "XA", config.xa_polling),
    ):
        if not polling.state:
            continue

        def run_pacs(modality: str = modality, polling: PollingConfig = polling) -> None:
            """Запускает PACS polling по настроенной модальности."""
            if polling.work_option == "on_request":
                poll_agent_request_for_modality(config, polling, modality, viewer, state)
            else:
                run_pacs_polling(config, polling, modality, viewer, state)

        runtimes.append(PollingRuntime(name=name, config=polling, run=run_pacs))

    return runtimes


def _run_exit_session_runtimes(runtimes: list[PollingRuntime]) -> None:
    """Выполняет polling с work_option=exit_session перед остановкой агента."""
    for runtime in runtimes:
        if runtime.config.work_option == "exit_session":
            _run_runtime(runtime)


def run_agent(config: AgentConfig) -> int:
    """Запускает постоянный polling ct/xa/study по настройкам agent_config."""
    configure_signals()
    state = load_state(config.state_file)
    viewer = ViewerClient(config.viewer_url, config.request_timeout_seconds)
    runtimes = _build_runtimes(config, viewer, state)
    alive_interval = max(config.alive_polling_interval_min * 60, 1)
    next_alive_at = 0.0

    LOGGER.info(
        "Hospital agent started: agent_id=%s environment=%s description=%s viewer_url=%s",
        config.agent_id,
        config.environment,
        config.description,
        config.viewer_url,
    )
    if not runtimes:
        LOGGER.warning("No active polling blocks in agent_config.json")

    while _running:
        now_monotonic = time.monotonic()
        now_datetime = datetime.now()

        if now_monotonic >= next_alive_at:
            try:
                send_alive(config, viewer)
            except Exception:
                LOGGER.exception("Agent alive webhook failed")
            next_alive_at = now_monotonic + alive_interval

        for runtime in runtimes:
            if runtime.config.work_option == "exit_session":
                continue
            if _runtime_due(runtime, now_monotonic, now_datetime):
                _run_runtime(runtime)
                _schedule_next(runtime, now_monotonic)

        time.sleep(1)

    _run_exit_session_runtimes(runtimes)
    LOGGER.info("Hospital agent stopped")
    return 0


def run_agent_once(config: AgentConfig) -> int:
    """Выполняет по одному проходу каждого активного polling для проверки."""
    state = load_state(config.state_file)
    viewer = ViewerClient(config.viewer_url, config.request_timeout_seconds)
    runtimes = _build_runtimes(config, viewer, state)
    send_alive(config, viewer)
    for runtime in runtimes:
        _run_runtime(runtime)
    return 0
