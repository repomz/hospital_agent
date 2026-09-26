import json
import unittest
from pathlib import Path
from types import SimpleNamespace

from hospital_agent.config import PollingConfig
from hospital_agent.polling.alive import build_alive_payload


class HeartbeatConfigurationTests(unittest.TestCase):
    def test_payload_contains_operational_settings_but_not_credentials(self):
        polling = PollingConfig(True, 5, [Path("operations")])
        config = SimpleNamespace(
            log_dir=Path("logs"),
            state_file=Path("state.json"),
            pacs_config_path=Path("config.json"),
            agent_id="1",
            description="Agent",
            alive_polling_interval_min=1,
            study_polling=polling,
            xa_polling=polling,
            ct_polling=polling,
            user_requests_polling=polling,
            secret="never-send-this",
        )
        payload = build_alive_payload(config)
        snapshot = payload["configuration"]
        self.assertEqual(snapshot["study_polling"]["operations_dir"], ["operations"])
        self.assertEqual(snapshot["xa_polling"], {"state": True, "interval_min": 5})
        self.assertNotIn("never-send-this", json.dumps(payload))
