import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from types import SimpleNamespace

from hospital_agent.config import PollingConfig, apply_remote_configuration


class RemoteConfigTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.path = self.root / "agent_config.json"
        self.path.write_text(
            json.dumps(
                {
                    "agent_id": "2",
                    "version": "0.2.15",
                    "viewer_url": "https://angio.su",
                    "custom": 123,
                }
            )
        )
        self.config = SimpleNamespace(config_path=self.path, lock=RLock())
        self.changes = {"description": "Операционная 2", "heartbeat_interval_min": 2}
        for name in ("study_polling", "xa_polling", "ct_polling", "user_requests_polling"):
            setattr(self.config, name, PollingConfig(True, 10))
            self.changes[name] = {"state": True, "interval_min": 3}
        self.changes["study_polling"]["operations_dir"] = [str(self.root)]

    def test_persists_and_updates_existing_runtime_objects(self):
        polling = self.config.study_polling
        apply_remote_configuration(self.config, self.changes)
        result = json.loads(self.path.read_text())
        self.assertEqual(result["custom"], 123)
        self.assertEqual(result["version"], "0.2.15")
        self.assertEqual(result["agent_id"], "2")
        self.assertEqual(result["alive_polling_interval_min"], 2)
        self.assertIs(polling, self.config.study_polling)
        self.assertEqual(polling.interval_min, 3)
        self.assertEqual(polling.operations_dirs, [self.root])

    def test_rejects_unavailable_paths_without_writing(self):
        before = self.path.read_bytes()
        self.changes["study_polling"]["operations_dir"] = [str(self.root / "absent")]
        with self.assertRaises(ValueError):
            apply_remote_configuration(self.config, self.changes)
        self.assertEqual(self.path.read_bytes(), before)

    def test_rejects_disabling_commands_or_editing_identity(self):
        self.changes["user_requests_polling"]["state"] = False
        with self.assertRaises(ValueError):
            apply_remote_configuration(self.config, self.changes)
        self.changes["user_requests_polling"]["state"] = True
        self.changes["agent_id"] = "1"
        with self.assertRaises(ValueError):
            apply_remote_configuration(self.config, self.changes)

    def test_invalid_interval_is_not_written(self):
        before = self.path.read_bytes()
        self.changes["xa_polling"]["interval_min"] = 0
        with self.assertRaises(ValueError):
            apply_remote_configuration(self.config, self.changes)
        self.assertEqual(self.path.read_bytes(), before)
