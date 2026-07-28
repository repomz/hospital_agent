import json
import logging
import os
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from hospital_agent.app import (
    AgentContextFilter,
    DailyFileHandler,
    is_pythonw,
    load_environment,
    resolve_config_path,
    run_agent_resilient,
    setup_logging,
)
from hospital_agent.config import load_agent_config


class AppStartupTests(unittest.TestCase):
    def test_runtime_is_restarted_after_unexpected_error(self):
        config = object()
        with patch(
            "hospital_agent.app.run_agent",
            side_effect=(RuntimeError("temporary crash"), 0),
        ) as run, patch("hospital_agent.app.time.sleep") as sleep, patch(
            "hospital_agent.app.logging.getLogger"
        ):
            result = run_agent_resilient(config, restart_delay=10)

        self.assertEqual(result, 0)
        self.assertEqual(run.call_count, 2)
        sleep.assert_called_once_with(10)

    def test_standard_dotenv_is_loaded_without_overriding_process_values(self):
        with TemporaryDirectory() as tmp_dir, patch.dict(
            os.environ,
            {"YANDEX_BUCKET": "from-process"},
            clear=True,
        ):
            base_dir = Path(tmp_dir)
            (base_dir / ".env").write_text(
                "\n".join(
                    (
                        "YANDEX_BUCKET=from-file",
                        "YANDEX_ENDPOINT=https://storage.example",
                        "YANDEX_ACCESS_KEY_ID=access-id",
                        "YANDEX_SECRET_ACCESS_KEY=secret",
                    )
                ),
                encoding="utf-8",
            )
            (base_dir / "env.txt").write_text(
                "YANDEX_ENDPOINT=https://ignored.example",
                encoding="utf-8",
            )

            self.assertTrue(load_environment(base_dir))
            self.assertEqual(os.environ["YANDEX_BUCKET"], "from-process")
            self.assertEqual(os.environ["YANDEX_ENDPOINT"], "https://storage.example")
            self.assertEqual(os.environ["YANDEX_ACCESS_KEY_ID"], "access-id")
            self.assertEqual(os.environ["YANDEX_SECRET_ACCESS_KEY"], "secret")

    def test_log_context_contains_python_file_and_line(self):
        record = logging.LogRecord(
            name="hospital_agent.services.commands",
            level=logging.ERROR,
            pathname="/project/hospital_agent/services/commands.py",
            lineno=269,
            msg="backend rejected report",
            args=(),
            exc_info=None,
        )

        AgentContextFilter("2").filter(record)

        self.assertEqual(record.agent_name, "agent_2")
        self.assertEqual(
            record.source_location,
            "hospital_agent/services/commands.py:269",
        )

    def tearDown(self):
        logging.shutdown()
        for handler in logging.getLogger().handlers[:]:
            logging.getLogger().removeHandler(handler)

    def test_pythonw_detection_supports_windows_paths(self):
        self.assertTrue(is_pythonw(r"C:\Python311\pythonw.exe"))
        self.assertTrue(is_pythonw(r"C:\Python311\pythonw3.11.exe"))
        self.assertFalse(is_pythonw(r"C:\Python311\python.exe"))

    def test_terminal_logging_does_not_create_log_directory(self):
        with TemporaryDirectory() as tmp_dir:
            log_dir = Path(tmp_dir) / "logs"

            setup_logging(log_dir, background=False)

            self.assertFalse(log_dir.exists())
            self.assertEqual(len(logging.getLogger().handlers), 1)
            self.assertIsInstance(logging.getLogger().handlers[0], logging.StreamHandler)
            self.assertNotIsInstance(logging.getLogger().handlers[0], DailyFileHandler)
            self.assertEqual(logging.getLogger("pynetdicom").level, logging.WARNING)
            self.assertEqual(logging.getLogger("pydicom").level, logging.WARNING)

    def test_background_logging_switches_file_after_midnight(self):
        with TemporaryDirectory() as tmp_dir:
            log_dir = Path(tmp_dir)
            current_day = [date(2026, 7, 24)]
            handler = DailyFileHandler(log_dir, date_provider=lambda: current_day[0])
            handler.setFormatter(logging.Formatter("%(message)s"))
            logger = logging.getLogger("test.daily-log")
            logger.handlers = [handler]
            logger.propagate = False
            logger.setLevel(logging.INFO)

            logger.info("first day")
            current_day[0] = date(2026, 7, 25)
            logger.info("second day")
            handler.close()

            self.assertEqual(
                (log_dir / "2026-07-24.log").read_text(encoding="utf-8").strip(),
                "first day",
            )
            self.assertEqual(
                (log_dir / "2026-07-25.log").read_text(encoding="utf-8").strip(),
                "second day",
            )

    def test_config_is_found_in_current_directory(self):
        with TemporaryDirectory() as tmp_dir:
            config_path = Path(tmp_dir) / "agent_config.json"
            config_path.write_text("{}", encoding="utf-8")

            with patch("hospital_agent.app.Path.cwd", return_value=Path(tmp_dir)):
                resolved = resolve_config_path()

            self.assertEqual(resolved, config_path.resolve())

    def test_relative_config_paths_use_config_directory(self):
        raw_config = {
            "viewer_url": "http://127.0.0.1:8080",
            "agent_id": 2,
            "log_dir": "logs/agent",
            "state_file": "logs/agent/state.json",
            "pacs_config_path": "config.json",
            "plan_dir": "plans",
            "report_dir": "reports",
            "report_time": "08:00",
            "study_polling": {
                "operations_dir": "operations",
            },
        }
        with TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir).resolve()
            config_path = base_dir / "agent_config.json"
            config_path.write_text(
                json.dumps(raw_config),
                encoding="utf-8",
            )

            config = load_agent_config(config_path)

            self.assertEqual(config.log_dir, base_dir / "logs" / "agent")
            self.assertEqual(config.state_file, base_dir / "logs" / "agent" / "state.json")
            self.assertEqual(config.pacs_config_path, base_dir / "config.json")
            self.assertEqual(config.study_polling.operations_dirs, [base_dir / "operations"])
            self.assertEqual(config.plan_dir, base_dir / "plans")
            self.assertEqual(config.report_dir, base_dir / "reports")


if __name__ == "__main__":
    unittest.main()
