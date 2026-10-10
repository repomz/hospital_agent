import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from hospital_agent.state import AgentState, load_state, save_state


class StateSaveTests(unittest.TestCase):
    def test_permanent_lock_preserves_existing_state_and_removes_temporary_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            save_state(target, AgentState(last_report_date="2026-10-01"))
            with (
                patch(
                    "hospital_agent.state.os.replace", side_effect=PermissionError("locked")
                ) as replace,
                patch("hospital_agent.state.time.sleep"),
            ):
                with self.assertRaises(PermissionError):
                    save_state(target, AgentState(last_report_date="2026-10-10"))
            self.assertEqual(replace.call_count, 5)
            self.assertEqual(load_state(target).last_report_date, "2026-10-01")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_retries_atomic_replace_after_transient_windows_lock(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "state.json"
            real_replace = os.replace
            attempts = 0

            def flaky_replace(source, destination):
                nonlocal attempts
                attempts += 1
                if attempts == 1:
                    raise PermissionError("transient file lock")
                real_replace(source, destination)

            state = AgentState(last_report_date="2026-10-04")
            with patch("hospital_agent.state.os.replace", side_effect=flaky_replace):
                save_state(target, state)

            self.assertEqual(attempts, 2)
            self.assertEqual(load_state(target).last_report_date, "2026-10-04")
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
