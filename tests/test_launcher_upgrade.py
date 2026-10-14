import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from agent_launcher import UPDATE_CHECK_INTERVAL_SECONDS
from hospital_agent.support.launcher_upgrade import upgrade_launcher


class LauncherUpgradeTests(unittest.TestCase):
    def setUp(self):
        self.directory = TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.active = self.root / ".updates" / ("a" * 64)
        self.bundled = self.active / "hospital_agent" / "support" / "launcher_update.py"
        self.bundled.parent.mkdir(parents=True)
        self.bundled.write_bytes(b"INTERVAL = 300\n")
        self.target = self.root / "agent_launcher.py"
        self.target.write_bytes(b"INTERVAL = 3600\n")
        self.selection = self.root / ".updates" / "selection.json"

    def confirm(self):
        self.selection.write_text(json.dumps({"active": self.active.name}))

    def test_five_minute_interval(self):
        self.assertEqual(UPDATE_CHECK_INTERVAL_SECONDS, 5 * 60)

    @patch("hospital_agent.support.launcher_upgrade.subprocess.Popen")
    def test_no_restart_before_supervisor_health_commit(self, launch):
        self.assertFalse(upgrade_launcher(self.root, self.active))
        launch.assert_not_called()
        self.assertIn(b"3600", self.target.read_bytes())

    @patch("hospital_agent.support.launcher_upgrade.subprocess.Popen")
    def test_serialized_restart_keeps_state_and_cleans_child_environment(self, launch):
        self.confirm()
        state = self.root / "state.json"
        state.write_bytes(b"preserve me")
        with patch.dict("os.environ", {"VIEWER_AGENT_CHILD": "1", "VIEWER_AGENT_STOP": "old"}):
            self.assertTrue(upgrade_launcher(self.root, self.active))
        self.assertTrue((self.root / ".updates" / "stop").exists())
        self.assertEqual(state.read_bytes(), b"preserve me")
        self.assertIn("--wait-for-lock", launch.call_args.args[0])
        self.assertNotIn("VIEWER_AGENT_CHILD", launch.call_args.kwargs["env"])
        self.assertEqual(self.target.read_bytes(), self.bundled.read_bytes())
        self.assertFalse(upgrade_launcher(self.root, self.active))
        launch.assert_called_once()

    @patch(
        "hospital_agent.support.launcher_upgrade.subprocess.Popen", side_effect=OSError("denied")
    )
    def test_failed_restart_keeps_running_agent_and_allows_retry(self, launch):
        self.confirm()
        with self.assertRaises(OSError):
            upgrade_launcher(self.root, self.active)
        self.assertFalse((self.root / ".updates" / "stop").exists())
        self.assertEqual(self.target.read_bytes(), b"INTERVAL = 3600\n")

    @patch("hospital_agent.support.launcher_upgrade.subprocess.Popen")
    def test_manual_install_needs_no_migration(self, launch):
        self.assertFalse(upgrade_launcher(self.root, self.root))
        launch.assert_not_called()

    @patch("hospital_agent.support.launcher_upgrade.subprocess.Popen")
    def test_invalid_launcher_does_not_replace_existing_file(self, launch):
        self.confirm()
        self.bundled.write_bytes(b"invalid python syntax !!!")
        with self.assertRaises(SyntaxError):
            upgrade_launcher(self.root, self.active)
        self.assertEqual(self.target.read_bytes(), b"INTERVAL = 3600\n")
        launch.assert_not_called()
