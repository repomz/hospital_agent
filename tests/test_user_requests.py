import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch

from hospital_agent.polling.user_requests import poll_user_requests, run_user_request
from hospital_agent.services.commands import generate_report_from_payload
from hospital_agent.state import AgentState, load_state


class FakeViewer:
    def __init__(self, payload=None, post_result=True):
        self.payload = payload
        self.post_result = post_result
        self.get_endpoints = []
        self.posts = []

    def get_json(self, endpoint):
        self.get_endpoints.append(endpoint)
        return self.payload

    def post_json(self, endpoint, payload):
        self.posts.append((endpoint, payload))
        return self.post_result


class UserRequestTests(unittest.TestCase):
    def test_list_of_requests_is_not_reprocessed(self):
        requests = [
            {"id": "A", "command": "first"},
            {"id": "B", "command": "second"},
        ]
        viewer = FakeViewer(requests)
        polling = SimpleNamespace(endpoint="/user_requests")

        with TemporaryDirectory() as tmp_dir:
            config = SimpleNamespace(state_file=Path(tmp_dir) / "state.json", agent_id="2")
            state = AgentState()
            with patch(
                "hospital_agent.polling.user_requests.execute_user_command",
                return_value={"ok": True},
            ) as execute:
                self.assertEqual(poll_user_requests(config, polling, viewer, state), 2)
                self.assertEqual(poll_user_requests(config, polling, viewer, state), 0)

        self.assertEqual(execute.call_count, 2)
        self.assertEqual(state.processed_user_request_ids, ["A", "B"])
        self.assertEqual(viewer.get_endpoints, ["/user_requests?agent_id=2"] * 2)

    def test_failed_request_is_available_for_retry(self):
        viewer = FakeViewer()
        payload = {"id": "retry-me", "command": "temporary_failure"}

        with TemporaryDirectory() as tmp_dir:
            config = SimpleNamespace(state_file=Path(tmp_dir) / "state.json")
            state = AgentState()
            with patch(
                "hospital_agent.polling.user_requests.execute_user_command",
                side_effect=RuntimeError("temporary failure"),
            ) as execute:
                with patch("hospital_agent.polling.user_requests.LOGGER.exception"):
                    self.assertFalse(run_user_request(config, viewer, state, payload))
                    self.assertFalse(run_user_request(config, viewer, state, payload))

        self.assertEqual(execute.call_count, 2)
        self.assertEqual(state.processed_user_request_ids, [])

    def test_invalid_request_is_not_retried(self):
        viewer = FakeViewer()
        payload = {"id": "invalid", "command": "invalid_arguments"}

        with TemporaryDirectory() as tmp_dir:
            config = SimpleNamespace(state_file=Path(tmp_dir) / "state.json")
            state = AgentState()
            with patch(
                "hospital_agent.polling.user_requests.execute_user_command",
                side_effect=ValueError("missing required field"),
            ) as execute:
                with patch("hospital_agent.polling.user_requests.LOGGER.warning"):
                    self.assertFalse(run_user_request(config, viewer, state, payload))
                    self.assertFalse(run_user_request(config, viewer, state, payload))

        self.assertEqual(execute.call_count, 1)
        self.assertEqual(state.processed_user_request_ids, ["invalid"])

    def test_legacy_last_request_id_is_migrated(self):
        with TemporaryDirectory() as tmp_dir:
            state_path = Path(tmp_dir) / "state.json"
            state_path.write_text(
                json.dumps({"last_user_request_id": "legacy-id"}),
                encoding="utf-8",
            )

            state = load_state(state_path)

        self.assertEqual(state.processed_user_request_ids, ["legacy-id"])

    def test_generated_report_is_returned_as_command_result(self):
        generated = {
            "report": {"planned_count": 1},
            "json_report_file": "report.json",
            "text_report_file": "report.txt",
        }

        with patch(
            "hospital_agent.services.commands.generate_operations_report",
            return_value=generated,
        ):
            result = generate_report_from_payload({})

        self.assertEqual(result["report"], {"planned_count": 1})
        self.assertEqual(result["json_report_file"], "report.json")

    def test_success_result_is_retried_without_reexecuting_command(self):
        viewer = FakeViewer(post_result=False)
        payload = {
            "id": "durable-result",
            "command": "first",
            "response_endpoint": "/user_requests/durable-result/result",
        }

        with TemporaryDirectory() as tmp_dir:
            config = SimpleNamespace(
                state_file=Path(tmp_dir) / "state.json",
                agent_id="2",
            )
            state = AgentState()
            with patch(
                "hospital_agent.polling.user_requests.execute_user_command",
                return_value={"uploaded": 3},
            ) as execute:
                self.assertFalse(run_user_request(config, viewer, state, payload))
                self.assertIn("durable-result", state.pending_user_request_results)

                viewer.post_result = True
                self.assertFalse(run_user_request(config, viewer, state, payload))

        self.assertEqual(execute.call_count, 1)
        self.assertEqual(state.processed_user_request_ids, ["durable-result"])
        self.assertEqual(state.pending_user_request_results, {})
        endpoint, result = viewer.posts[-1]
        self.assertEqual(endpoint, "/user_requests/durable-result/result")
        self.assertEqual(result["agent_id"], 2)
        self.assertTrue(result["ok"])


if __name__ == "__main__":
    unittest.main()
