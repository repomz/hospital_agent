import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import RLock
from types import SimpleNamespace
from unittest.mock import patch

from hospital_agent.config import record_agent_version


class ConfigVersionTests(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "agent_config.json"
        self.config = SimpleNamespace(config_path=self.path, lock=RLock())

    def save(self, data):
        self.path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def test_preserves_settings_timestamp_and_positions_version(self):
        original = {
            "version": "old",
            "updated_at": "2026-10-01",
            "agent_id": 2,
            "custom": {"value": "не менять"},
        }
        self.save(original)
        record_agent_version(self.config, "0.2.15")
        result = json.loads(self.path.read_text(encoding="utf-8"))
        self.assertEqual(list(result), ["updated_at", "version", "agent_id", "custom"])
        self.assertEqual(result, {**original, "version": "0.2.15"})

    def test_missing_timestamp_added(self):
        self.save({"agent_id": 2})
        record_agent_version(self.config, "0.2.15")
        result = json.loads(self.path.read_text())
        self.assertEqual(list(result), ["agent_id", "updated_at", "version"])
        self.assertTrue(result["updated_at"])

    def test_unchanged_config_not_rewritten(self):
        self.save({"updated_at": "date", "version": "0.2.15"})
        before = self.path.read_bytes()
        with patch("hospital_agent.config.os.replace") as replace:
            record_agent_version(self.config, "0.2.15")
        replace.assert_not_called()
        self.assertEqual(before, self.path.read_bytes())

    def test_failed_replace_preserves_original_and_cleans_temporary(self):
        self.save({"updated_at": "date", "version": "old"})
        before = self.path.read_bytes()
        with patch("hospital_agent.config.os.replace", side_effect=PermissionError):
            with self.assertRaises(PermissionError):
                record_agent_version(self.config, "0.2.15")
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(list(self.path.parent.iterdir()), [self.path])

    def test_invalid_json_not_overwritten(self):
        self.path.write_text("{broken")
        with self.assertRaises(ValueError):
            record_agent_version(self.config, "0.2.15")
        self.assertEqual(self.path.read_text(), "{broken")

    def test_rollback_records_running_version(self):
        self.save({"updated_at": "date", "version": "0.2.16"})
        record_agent_version(self.config, "0.2.15")
        self.assertEqual(json.loads(self.path.read_text())["version"], "0.2.15")
