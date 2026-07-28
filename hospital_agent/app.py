import logging
import sys
import time
from datetime import date
from pathlib import Path, PureWindowsPath
from typing import Callable

from dotenv import load_dotenv

from .config import DEFAULT_CONFIG_PATH, load_agent_config
from .runner import run_agent


RUNTIME_RESTART_DELAY_SECONDS = 10


def run_agent_resilient(config: object, restart_delay: float = RUNTIME_RESTART_DELAY_SECONDS) -> int:
    """Перезапускает runtime после неожиданной внутренней ошибки."""
    while True:
        try:
            return run_agent(config)
        except Exception:
            logging.getLogger("hospital_agent.startup").exception(
                "Agent runtime crashed; restarting in %s seconds",
                restart_delay,
            )
            time.sleep(restart_delay)


class AgentContextFilter(logging.Filter):
    """Добавляет в запись имя агента и точное место вызова логирования."""

    def __init__(self, agent_id: str) -> None:
        super().__init__()
        self.agent_name = f"agent_{agent_id}"

    def filter(self, record: logging.LogRecord) -> bool:
        record.agent_name = self.agent_name
        source_path = Path(record.pathname)
        try:
            package_index = source_path.parts.index("hospital_agent")
            source_name = Path(*source_path.parts[package_index:]).as_posix()
        except ValueError:
            source_name = record.filename
        record.source_location = f"{source_name}:{record.lineno}"
        return True


class DailyFileHandler(logging.Handler):
    """Пишет лог в файл текущей даты и переключает файл после полуночи."""

    def __init__(
        self,
        log_dir: Path,
        date_provider: Callable[[], date] = date.today,
    ) -> None:
        super().__init__()
        self.log_dir = log_dir
        self.date_provider = date_provider
        self.current_date: str | None = None
        self.file_handler: logging.FileHandler | None = None

    def _ensure_file_handler(self) -> logging.FileHandler:
        current_date = self.date_provider().isoformat()
        if self.file_handler is not None and self.current_date == current_date:
            return self.file_handler

        if self.file_handler is not None:
            self.file_handler.close()

        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.current_date = current_date
        self.file_handler = logging.FileHandler(
            self.log_dir / f"{current_date}.log",
            encoding="utf-8",
        )
        self.file_handler.setFormatter(self.formatter)
        return self.file_handler

    def emit(self, record: logging.LogRecord) -> None:
        """Записывает сообщение в файл, соответствующий текущей дате."""
        try:
            self._ensure_file_handler().emit(record)
        except Exception:
            self.handleError(record)

    def close(self) -> None:
        """Закрывает открытый дневной файл."""
        if self.file_handler is not None:
            self.file_handler.close()
            self.file_handler = None
        super().close()


def is_pythonw(executable: str | Path | None = None) -> bool:
    """Определяет фоновый запуск интерпретатором pythonw."""
    executable_name = PureWindowsPath(str(executable or sys.executable)).stem.casefold()
    return executable_name.startswith("pythonw")


def resolve_config_path() -> Path:
    """Находит agent_config.json независимо от рабочей папки процесса."""
    current_dir_config = Path.cwd() / DEFAULT_CONFIG_PATH
    if current_dir_config.exists():
        return current_dir_config.resolve()
    return Path(__file__).resolve().parent.parent / DEFAULT_CONFIG_PATH


def load_environment(base_dir: Path) -> bool:
    """Загружает стандартный .env рядом с agent_config.json."""
    return load_dotenv(dotenv_path=base_dir / ".env", override=False)


def setup_logging(
    log_dir: Path,
    background: bool | None = None,
    agent_id: str = "unknown",
) -> None:
    """Настраивает только файл для pythonw или только консоль для python."""
    if background is None:
        background = is_pythonw()
    handlers: list[logging.Handler]
    if background:
        handlers = [DailyFileHandler(log_dir)]
    else:
        handlers = [logging.StreamHandler()]

    context_filter = AgentContextFilter(agent_id)
    for handler in handlers:
        handler.addFilter(context_filter)
    logging.basicConfig(
        level=logging.INFO,
        format=(
            "%(asctime)s | %(levelname)s | %(agent_name)s | "
            "%(source_location)s | %(message)s"
        ),
        handlers=handlers,
        force=True,
    )
    # pynetdicom на INFO печатает каждый DICOM dataset и каждый C-STORE instance.
    # Агент логирует итог целого исследования самостоятельно.
    logging.getLogger("pynetdicom").setLevel(logging.WARNING)
    logging.getLogger("pydicom").setLevel(logging.WARNING)


def main() -> None:
    """Точка входа приложения hospital_agent без аргументов запуска."""
    background = is_pythonw()
    config_path = resolve_config_path()
    try:
        load_environment(config_path.parent)
        config = load_agent_config(config_path)
    except Exception:
        setup_logging(config_path.parent / "logs" / "agent", background)
        logging.getLogger("hospital_agent.startup").exception(
            "Cannot start hospital agent using config %s",
            config_path,
        )
        raise

    setup_logging(config.log_dir, background, config.agent_id)
    logging.getLogger("hospital_agent.startup").info(
        "Logging mode=%s",
        "daily_file" if background else "terminal",
    )
    raise SystemExit(run_agent_resilient(config))
