import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.protocol_sources import attach_protocol_source, publish_source_inventory
from hospital_agent.state import AgentState, load_state, save_state


class ProtocolSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.polling = SimpleNamespace(state=True, operations_dirs=[self.root])
        self.config = SimpleNamespace(
            agent_id="1", study_polling=self.polling, state_file=self.root / "state.json"
        )
        self.state = AgentState()
        self.viewer = SimpleNamespace(post_json=MagicMock(return_value=True))
        self.payload = {
            "study_id": "1706",
            "patient": "Павлюк Анатолий Васильевич",
            "room": 1,
            "time_beginning": "2026-10-15T05:20:00Z",
            "name_operation": "КАГ стент 2",
        }

    def file(self, name):
        path = self.root / name
        path.write_bytes(b"fixture")
        os.utime(path, (0, 0))
        return path

    def test_edit_and_move_preserve_source_identity(self):
        old = self.file("old.docx")
        attach_protocol_source(self.config, self.state, old, self.payload)
        original = self.payload["source"]["file_id"]
        self.payload["name_operation"] = "КАГ стент 1"
        attach_protocol_source(self.config, self.state, old, self.payload)
        self.assertEqual(self.payload["source"]["file_id"], original)
        new = self.root / "new.docx"
        old.rename(new)
        attach_protocol_source(self.config, self.state, new, self.payload)
        self.assertEqual(self.payload["source"]["file_id"], original)
        save_state(self.config.state_file, self.state)
        self.assertEqual(
            load_state(self.config.state_file).protocol_sources, self.state.protocol_sources
        )
        publish_source_inventory(self.config, self.polling, self.state, self.viewer)
        self.assertEqual(self.viewer.post_json.call_args.args[1]["file_ids"], [original])

    def test_coexisting_copies_have_independent_source_ids(self):
        a = self.file("a.docx")
        b = self.file("b.docx")
        attach_protocol_source(self.config, self.state, a, self.payload)
        first = self.payload["source"]["file_id"]
        attach_protocol_source(self.config, self.state, b, self.payload)
        self.assertNotEqual(first, self.payload["source"]["file_id"])

    def test_removed_source_is_reported_only_after_complete_scan(self):
        path = self.file("a.docx")
        attach_protocol_source(self.config, self.state, path, self.payload)
        path.unlink()
        publish_source_inventory(self.config, self.polling, self.state, self.viewer)
        self.assertEqual(self.viewer.post_json.call_args.args[1]["file_ids"], [])

    def test_missing_root_does_not_report_deletion(self):
        path = self.file("a.docx")
        attach_protocol_source(self.config, self.state, path, self.payload)
        self.polling.operations_dirs = [self.root / "offline"]
        publish_source_inventory(self.config, self.polling, self.state, self.viewer)
        self.viewer.post_json.assert_not_called()

    def test_partial_scan_does_not_report_deletion(self):
        path = self.file("a.docx")
        attach_protocol_source(self.config, self.state, path, self.payload)
        with patch(
            "hospital_agent.protocol_sources.os.walk", side_effect=PermissionError("locked")
        ):
            publish_source_inventory(self.config, self.polling, self.state, self.viewer)
        self.viewer.post_json.assert_not_called()

    def test_unparsed_new_file_blocks_deletion_of_moved_source(self):
        path = self.file("a.docx")
        attach_protocol_source(self.config, self.state, path, self.payload)
        path.rename(self.root / "new.docx")
        publish_source_inventory(self.config, self.polling, self.state, self.viewer)
        self.viewer.post_json.assert_not_called()

    def test_disabled_polling_never_reports_deletions(self):
        path = self.file("a.docx")
        attach_protocol_source(self.config, self.state, path, self.payload)
        self.polling.state = False
        publish_source_inventory(self.config, self.polling, self.state, self.viewer)
        self.viewer.post_json.assert_not_called()


if __name__ == "__main__":
    unittest.main()
