import os
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.reconciliation import reconcile_year
from hospital_agent.state import AgentState, load_state


class ReconciliationTests(unittest.TestCase):
    def run_case(self, status, send=True):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        path = root / "protocol.docx"
        path.write_bytes(b"test")
        os.utime(path, (0, 0))
        config = SimpleNamespace(
            agent_id="1",
            state_file=root / "state.json",
            study_polling=SimpleNamespace(operations_dirs=[root]),
        )
        payload = {
            "study_id": "1",
            "patient": "Иванов Иван Иванович",
            "room": 1,
            "name_operation": "КАГ",
            "time_beginning": datetime.now().astimezone().replace(hour=0, minute=0).isoformat(),
        }
        viewer = SimpleNamespace(
            post_json_response=MagicMock(return_value={"statuses": [status]} if status else None),
            post_json=MagicMock(return_value=send),
        )
        state = AgentState()
        with (
            patch("hospital_agent.reconciliation.parse_protocol", return_value=payload),
            patch("hospital_agent.reconciliation.time.sleep"),
        ):
            result = reconcile_year(config, viewer, state)
        return root, path, config, viewer, state, result

    def test_present_not_uploaded_and_state_persisted(self):
        _, path, config, viewer, state, result = self.run_case("present")
        self.assertTrue(result)
        viewer.post_json.assert_not_called()
        self.assertIn(str(path), load_state(config.state_file).processed_protocols)
        self.assertTrue(reconcile_year(config, viewer, state))
        self.assertEqual(viewer.post_json_response.call_count, 1)

    def test_missing_uploaded(self):
        _, _, _, viewer, _, result = self.run_case("missing")
        self.assertTrue(result)
        viewer.post_json.assert_called_once()

    def test_conflict_requires_review(self):
        root, path, _, viewer, state, result = self.run_case("review")
        self.assertTrue(result)
        viewer.post_json.assert_not_called()
        self.assertIn(str(path), state.reconciliation["blocked"])
        self.assertIn(
            "неоднозначное", (root / "protocol_review.csv").read_text(encoding="utf-8-sig")
        )

    def test_upgrade_revisits_blocked_files_without_touching_other_state(self):
        _, path, config, viewer, state, _ = self.run_case("review")
        state.reconciliation.pop("revision")
        state.processed_user_request_ids.append("keep-request")
        viewer.post_json_response.return_value = {"statuses": ["present"]}
        payload = {
            "study_id": "1",
            "patient": "Иванов Иван Иванович",
            "room": 1,
            "name_operation": "КАГ",
            "time_beginning": datetime.now().astimezone().replace(hour=0, minute=0).isoformat(),
        }
        with (
            patch("hospital_agent.reconciliation.parse_protocol", return_value=payload),
            patch("hospital_agent.reconciliation.time.sleep"),
        ):
            self.assertTrue(reconcile_year(config, viewer, state))
        self.assertNotIn(str(path), state.reconciliation["blocked"])
        self.assertNotIn(str(path), state.reconciliation["review"])
        self.assertEqual(state.processed_user_request_ids, ["keep-request"])
        viewer.post_json.assert_not_called()
        self.assertEqual(viewer.post_json_response.call_count, 2)
        self.assertTrue(reconcile_year(config, viewer, state))
        self.assertEqual(viewer.post_json_response.call_count, 2)

    def test_offline_never_blindly_uploads(self):
        _, _, _, viewer, _, result = self.run_case(None)
        self.assertFalse(result)
        viewer.post_json.assert_not_called()

    def test_failed_upload_is_not_marked_processed(self):
        _, path, _, _, state, result = self.run_case("missing", False)
        self.assertFalse(result)
        self.assertNotIn(str(path), state.processed_protocols)

    def test_old_local_receipt_does_not_hide_missing_server_protocol(self):
        _, _, config, viewer, state, _ = self.run_case("present")
        state.reconciliation = {}
        viewer.post_json_response.return_value = {"statuses": ["missing"]}
        payload = {
            "study_id": "1",
            "patient": "Иванов Иван Иванович",
            "room": 1,
            "name_operation": "КАГ",
            "time_beginning": datetime.now().astimezone().replace(hour=0, minute=0).isoformat(),
        }
        with (
            patch("hospital_agent.reconciliation.parse_protocol", return_value=payload),
            patch("hospital_agent.reconciliation.time.sleep"),
        ):
            self.assertTrue(reconcile_year(config, viewer, state))
        viewer.post_json.assert_called_once()
