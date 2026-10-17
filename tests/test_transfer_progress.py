import threading
import unittest

from hospital_agent.transfer_progress import TransferProgress


class ProgressTests(unittest.TestCase):
    def test_reports_without_blocking_receiver(self):
        received = threading.Event()
        calls = []

        class Viewer:
            def post_json(self, endpoint, payload, timeout_seconds):
                calls.append((endpoint, payload, timeout_seconds))
                received.set()
                return True

        tracker = TransferProgress(Viewer(), 1, "test")
        tracker.update("upload", 20, 100, 1234)
        with tracker:
            self.assertTrue(received.wait(2))
        self.assertEqual(calls[0][1]["completed"], 20)
        self.assertEqual(calls[0][1]["stage"], "upload")
        self.assertFalse(tracker.thread.is_alive())

    def test_offline_progress_does_not_abort_transfer(self):
        class Viewer:
            def post_json(self, *args, **kwargs):
                raise OSError("offline")

        with TransferProgress(Viewer(), 1, "test") as update:
            update("download", 1, 2, 30)
