import logging
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, load_agent_config
from .runner import run_agent


def setup_logging(log_dir: Path) -> None:
    """Настраивает вывод логов hospital_agent в консоль и файл."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / "hospital_agent.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
        handlers=[
            logging.StreamHandler(),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
    )


def main() -> None:
    """Точка входа приложения hospital_agent без аргументов запуска."""
    config = load_agent_config(DEFAULT_CONFIG_PATH)
    setup_logging(config.log_dir)
    raise SystemExit(run_agent(config))
