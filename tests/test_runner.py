import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from hospital_agent.runner import _run_scheduled_report, _scheduled_report_period
from hospital_agent.state import AgentState


class ScheduledReportTests(unittest.TestCase):
    def test_monday_report_covers_friday_saturday_and_sunday(self):
        monday = datetime(2026, 7, 27, 8, 0)

        self.assertEqual(_scheduled_report_period(monday), 3)

    def test_other_days_cover_previous_duty_only(self):
        for day in range(28, 32):
            with self.subTest(day=day):
                current = datetime(2026, 7, day, 8, 0)
                self.assertEqual(_scheduled_report_period(current), 1)

    def test_scheduled_report_passes_monday_period_to_generator(self):
        config = SimpleNamespace(
            report_time="08:00",
            state_file=Path("unused-state.json"),
        )
        state = AgentState()
        viewer = object()
        monday = datetime(2026, 7, 27, 11, 14)

        with patch("hospital_agent.runner.datetime") as datetime_mock, patch(
            "hospital_agent.runner.generate_report_from_payload"
        ) as generate_report, patch("hospital_agent.runner.save_state"):
            datetime_mock.now.return_value = monday
            _run_scheduled_report(config, viewer, state)

        generate_report.assert_called_once_with(config, {"period": 3}, viewer)
        self.assertEqual(state.last_report_date, "2026-07-27")


if __name__ == "__main__":
    unittest.main()
