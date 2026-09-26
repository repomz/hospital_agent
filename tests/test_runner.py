import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.runner import PollingRuntime, _build_runtimes, _schedule_runtimes
from hospital_agent.state import AgentState


class PollingRuntimeTests(unittest.TestCase):
    def test_logs_are_scheduled_on_next_clock_hour(self):
        for now, expected in (
            (datetime(2026, 9, 26, 21, 23), 2220),
            (datetime(2026, 9, 26, 23, 59, 59), 1),
            (datetime(2026, 9, 27, 0, 0), 3600),
        ):
            with self.subTest(now=now), patch("hospital_agent.runner.datetime") as clock:
                clock.now.return_value = now
                runtime = PollingRuntime(
                    "agent_logs", SimpleNamespace(state=True, interval_min=60), lambda: None
                )
                executor = MagicMock()
                _schedule_runtimes([runtime], executor, 100)
                self.assertEqual(runtime.next_run_at, 100 + expected)
                executor.submit.assert_called_once()

    def test_protocol_polling_uses_its_own_switch_and_interval(self):
        xa_polling = SimpleNamespace(state=True, interval_min=7)
        study_polling = SimpleNamespace(
            state=False,
            interval_min=1,
            operations_dirs=[Path("operations")],
        )
        config = SimpleNamespace(
            user_requests_polling=SimpleNamespace(state=True, interval_min=1),
            study_polling=study_polling,
            ct_polling=SimpleNamespace(state=False, interval_min=10),
            xa_polling=xa_polling,
        )

        runtimes = _build_runtimes(config, object(), AgentState())
        protocols = next(runtime for runtime in runtimes if runtime.name == "protocols")

        self.assertIs(protocols.config, study_polling)
        self.assertIsNot(protocols.config, xa_polling)


if __name__ == "__main__":
    unittest.main()
