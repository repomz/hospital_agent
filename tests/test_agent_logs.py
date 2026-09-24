import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from hospital_agent.polling.logs import _completed_log_hours, upload_agent_logs
from hospital_agent.state import AgentState


class FakeViewer:
    def __init__(self):
        self.posts = []

    def post_json(self, endpoint, payload, timeout_seconds=None):
        self.posts.append((endpoint, payload))
        return True


class AgentLogUploadTests(unittest.TestCase):
    def test_groups_complete_hours_and_preserves_traceback_lines(self):
        with TemporaryDirectory() as directory:
            log_dir = Path(directory)
            (log_dir / "2026-09-23.log").write_text(
                "2026-09-23 09:01:00,000 | INFO | first\n"
                "2026-09-23 09:02:00,000 | ERROR | failed\n"
                "Traceback line\n"
                "2026-09-23 10:01:00,000 | INFO | current\n",
                encoding="utf-8",
            )

            chunks = _completed_log_hours(log_dir, datetime(2026, 9, 23, 10, 30))

            self.assertEqual(list(chunks), [datetime(2026, 9, 23, 9)])
            self.assertIn("Traceback line", chunks[datetime(2026, 9, 23, 9)])
            self.assertNotIn("current", chunks[datetime(2026, 9, 23, 9)])

    def test_upload_is_idempotent_across_repeated_polling(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            log_dir = base / "logs"
            log_dir.mkdir()
            (log_dir / "2026-09-23.log").write_text(
                "2026-09-23 09:01:00,000 | INFO | ready\n",
                encoding="utf-8",
            )
            config = SimpleNamespace(
                log_dir=log_dir,
                state_file=base / "state.json",
                agent_id="2",
            )
            viewer = FakeViewer()
            state = AgentState()
            now = datetime(2026, 9, 23, 10, 30).astimezone()

            self.assertEqual(upload_agent_logs(config, viewer, state, now), 1)
            self.assertEqual(upload_agent_logs(config, viewer, state, now), 0)
            self.assertEqual(len(viewer.posts), 1)
            self.assertEqual(viewer.posts[0][0], "/agent_logs")


if __name__ == "__main__":
    unittest.main()
