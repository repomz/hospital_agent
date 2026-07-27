import logging
import signal
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime
from typing import Callable

from .config import AgentConfig, DEFAULT_REQUEST_TIMEOUT_SECONDS, PollingConfig
from .http_client import ViewerClient
from .polling.alive import send_alive
from .polling.pacs_studies import (
    cleanup_expired_yandex_studies,
    disable_expired_polling,
    run_modality_polling,
)
from .polling.protocols import poll_operation_protocols
from .polling.user_requests import poll_user_requests
from .services.commands import generate_report_from_payload
from .state import AgentState, load_state, save_state


LOGGER = logging.getLogger("hospital_agent")
_running = True


@dataclass
class PollingRuntime:
    """Независимый polling runtime, который не перекрывается сам с собой."""

    name: str
    config: PollingConfig
    run: Callable[[], object]
    next_run_at: float = 0.0
    future: Future[object] | None = None


def request_stop(signum: int, frame: object) -> None:
    """Запрашивает мягкую остановку постоянного агента."""
    global _running
    LOGGER.info("Stop signal received: %s", signum)
    _running = False


def configure_signals() -> None:
    """Подключает обработчики SIGINT/SIGTERM."""
    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)


def _build_runtimes(
    config: AgentConfig,
    viewer: ViewerClient,
    state: AgentState,
) -> list[PollingRuntime]:
    """Создает независимые runtime для команд, протоколов и DICOM."""
    return [
        PollingRuntime(
            "user_requests",
            config.user_requests_polling,
            lambda: poll_user_requests(config, config.user_requests_polling, viewer, state),
        ),
        PollingRuntime(
            "protocols",
            config.study_polling,
            lambda: poll_operation_protocols(config, config.study_polling, viewer, state),
        ),
        PollingRuntime(
            "ct",
            config.ct_polling,
            lambda: run_modality_polling(config, config.ct_polling, "CT", viewer, state),
        ),
        PollingRuntime(
            "xa",
            config.xa_polling,
            lambda: run_modality_polling(config, config.xa_polling, "XA", viewer, state),
        ),
        PollingRuntime(
            "yandex_cleanup",
            PollingConfig(state=True, interval_min=1),
            lambda: cleanup_expired_yandex_studies(config, state),
        ),
    ]


def _collect_runtime(runtime: PollingRuntime) -> None:
    """Логирует завершение фонового прохода и освобождает runtime."""
    if runtime.future is None or not runtime.future.done():
        return
    try:
        result = runtime.future.result()
        if result:
            LOGGER.info("%s finished: result=%s", runtime.name, result)
    except Exception:
        LOGGER.exception("%s polling failed", runtime.name)
    runtime.future = None


def _schedule_runtimes(
    runtimes: list[PollingRuntime],
    executor: ThreadPoolExecutor,
    now_monotonic: float,
) -> None:
    """Запускает due-runtime параллельно, не допуская перекрытия одного направления."""
    for runtime in runtimes:
        _collect_runtime(runtime)
        if not runtime.config.state:
            runtime.next_run_at = 0.0
            continue
        if runtime.future is not None:
            continue
        if now_monotonic < runtime.next_run_at:
            continue
        runtime.future = executor.submit(runtime.run)
        runtime.next_run_at = now_monotonic + max(runtime.config.interval_min * 60, 1)


def _run_scheduled_report(
    config: AgentConfig,
    viewer: ViewerClient,
    state: AgentState,
) -> None:
    """Один раз в день создает отчет за завершившееся в 08:00 дежурство."""
    now = datetime.now()
    try:
        report_hour, report_minute = [
            int(part) for part in config.report_time.split(":", 1)
        ]
    except (TypeError, ValueError):
        LOGGER.warning("Invalid report_time=%r", config.report_time)
        return
    today = now.date().isoformat()
    if state.last_report_date == today:
        return
    if (now.hour, now.minute) < (report_hour, report_minute):
        return
    period = _scheduled_report_period(now)
    generate_report_from_payload(config, {"period": period}, viewer)
    with state.lock:
        state.last_report_date = today
        save_state(config.state_file, state)
    LOGGER.info("Duty report sent for %s: period_days=%s", today, period)


def _scheduled_report_period(now: datetime) -> int:
    """Возвращает три дня в понедельник и один день в остальные дни."""
    return 3 if now.weekday() == 0 else 1


def run_agent(config: AgentConfig) -> int:
    """Запускает постоянный многопоточный hospital agent."""
    global _running
    _running = True
    configure_signals()
    state = load_state(config.state_file)
    viewer = ViewerClient(config.viewer_url, DEFAULT_REQUEST_TIMEOUT_SECONDS)
    runtimes = _build_runtimes(config, viewer, state)
    alive_interval = max(config.alive_polling_interval_min * 60, 1)
    next_alive_at = 0.0
    next_report_check_at = 0.0

    LOGGER.info(
        "Started: agent_id=%s description=%s viewer_url=%s",
        config.agent_id,
        config.description,
        config.viewer_url,
    )
    with ThreadPoolExecutor(max_workers=len(runtimes) + 1) as executor:
        report_future: Future[object] | None = None
        while _running:
            now_monotonic = time.monotonic()
            if now_monotonic >= next_alive_at:
                disable_expired_polling(config, state)
                try:
                    send_alive(config, viewer)
                except Exception:
                    LOGGER.exception("Heartbeat failed")
                next_alive_at = now_monotonic + alive_interval

            _schedule_runtimes(runtimes, executor, now_monotonic)

            if report_future is not None and report_future.done():
                try:
                    report_future.result()
                except Exception:
                    LOGGER.exception("Scheduled report failed")
                report_future = None
            if report_future is None and now_monotonic >= next_report_check_at:
                report_future = executor.submit(_run_scheduled_report, config, viewer, state)
                next_report_check_at = now_monotonic + 60
            time.sleep(1)

    LOGGER.info("Stopped")
    return 0


def run_agent_once(config: AgentConfig) -> int:
    """Выполняет по одному проходу активных runtime для проверки."""
    state = load_state(config.state_file)
    viewer = ViewerClient(config.viewer_url, DEFAULT_REQUEST_TIMEOUT_SECONDS)
    send_alive(config, viewer)
    for runtime in _build_runtimes(config, viewer, state):
        if runtime.config.state:
            runtime.run()
    _run_scheduled_report(config, viewer, state)
    return 0
