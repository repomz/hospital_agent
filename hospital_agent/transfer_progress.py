"""Bounded background reporting; DICOM receiver callbacks never wait for HTTP."""

import threading


class TransferProgress:
    def __init__(self, viewer, agent_id, request_id):
        self.viewer = viewer
        self.endpoint = f"/user_requests/{request_id}/progress"
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.value = {
            "agent_id": int(agent_id),
            "stage": "download",
            "completed": 0,
            "total": 0,
            "bytes": 0,
        }
        self.thread = threading.Thread(target=self._run, daemon=True)

    def update(self, stage, completed=0, total=0, bytes_count=0):
        with self.lock:
            self.value.update(stage=stage, completed=completed, total=total or 0, bytes=bytes_count)

    def _run(self):
        while not self.stop.is_set():
            with self.lock:
                value = dict(self.value)
            try:
                self.viewer.post_json(self.endpoint, value, timeout_seconds=8)
            except Exception:
                pass  # Progress must never abort delivery of clinical data.
            self.stop.wait(10)

    def __enter__(self):
        self.thread.start()
        return self.update

    def __exit__(self, *_):
        self.stop.set()
        self.thread.join(timeout=10)
