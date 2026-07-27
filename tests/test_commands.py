import json
import unittest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.config import load_agent_config
from hospital_agent.services.commands import (
    _last_completed_duty_end,
    execute_user_command,
    get_dicom_study,
)
from hospital_agent.state import AgentState


class ViewerStub:
    def __init__(self, result=True):
        self.result = result
        self.posts = []

    def post_json(self, endpoint, payload, **kwargs):
        self.posts.append((endpoint, payload))
        return self.result


class CommandTests(unittest.TestCase):
    def test_report_uses_last_completed_0800_boundary(self):
        before_boundary = datetime(2026, 7, 27, 7, 59, 59)
        after_boundary = datetime(2026, 7, 27, 11, 14)

        self.assertEqual(
            _last_completed_duty_end(before_boundary),
            datetime(2026, 7, 26, 8, 0),
        )
        self.assertEqual(
            _last_completed_duty_end(after_boundary),
            datetime(2026, 7, 27, 8, 0),
        )

    def test_old_command_names_are_not_supported(self):
        config = SimpleNamespace()
        for command in (
            "send_study_to_yandex",
            "send_dicom_to_mapdr",
            "generate_operations_report",
        ):
            with self.subTest(command=command):
                self.assertIsNone(
                    execute_user_command(config, command, {}, "request-id")
                )

    def test_polling_command_changes_memory_and_config_file(self):
        with TemporaryDirectory() as directory:
            base = Path(directory)
            config_path = base / "agent_config.json"
            config_path.write_text(
                json.dumps(
                    {
                        "viewer_url": "http://127.0.0.1:8080",
                        "agent_id": 2,
                        "state_file": "state.json",
                        "ct_polling": {"state": False, "interval_min": 10},
                    }
                ),
                encoding="utf-8",
            )
            config = load_agent_config(config_path)
            state = AgentState()

            result = execute_user_command(
                config,
                "ct_polling_on",
                {},
                "request-id",
                state=state,
            )

            self.assertTrue(result["state"])
            self.assertTrue(config.ct_polling.state)
            saved = json.loads(config_path.read_text(encoding="utf-8"))
            self.assertTrue(saved["ct_polling"]["state"])
            self.assertIn("CT", state.polling_enabled_at)

    def test_get_ct_uses_direct_c_get_and_registers_complete_upload(self):
        download = {
            "ok": True,
            "study_uid": "1.2.3",
            "study_dir": "download",
            "received_files": 2,
            "received_bytes": 30,
            "expected_instances": 2,
            "patient": "Иванов^Иван",
            "age": "055Y",
            "study_date": "20260726",
            "study_time": "101500",
            "description": "КТ",
            "yandex_folder": "Иванов_26.07.2026_1.2.3",
        }
        upload = {
            "yandex_folder": "Иванов_26.07.2026_1.2.3",
            "uploaded_files": 2,
            "uploaded_bytes": 30,
            "failed_files": [],
            "files": [
                {"name": "1.dcm", "size": 10, "url": "https://example/1"},
                {"name": "2.dcm", "size": 20, "url": "https://example/2"},
            ],
            "dicom_link": "s3://bucket/Иванов_26.07.2026_1.2.3",
        }
        pacs_client = MagicMock()
        pacs_client.download_study.return_value = download
        pacs_client.retry_attempts = 3
        pacs_client.retry_delay = 0
        storage = MagicMock()
        storage.upload_folder.return_value = upload
        viewer = ViewerStub()

        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                pacs_config_path=Path(directory) / "missing.json",
                state_file=Path(directory) / "state.json",
            )
            state = AgentState()
            with patch(
                "hospital_agent.services.pacs.PACSClient",
                return_value=pacs_client,
            ), patch(
                "hospital_agent.services.yandex.YandexStorage",
                return_value=storage,
            ):
                result = get_dicom_study(
                    config,
                    {"study_uid": "1.2.3"},
                    "request-id",
                    "CT",
                    viewer,
                    state,
                )

        pacs_client.download_study.assert_called_once_with(
            "1.2.3",
            lookup_metadata=False,
        )
        self.assertEqual(viewer.posts[0][0], "/ct_studies")
        self.assertEqual(viewer.posts[0][1]["dicom_link"], upload["dicom_link"])
        self.assertNotIn("download_dir", result)
        self.assertEqual(len(state.yandex_cleanup), 1)

    def test_partial_yandex_upload_is_an_error_and_is_removed(self):
        pacs_client = MagicMock()
        pacs_client.download_study.return_value = {
            "ok": True,
            "study_uid": "1.2.3",
            "study_dir": "download",
            "received_files": 2,
            "yandex_folder": "folder",
        }
        pacs_client.retry_attempts = 1
        pacs_client.retry_delay = 0
        storage = MagicMock()
        storage.upload_folder.return_value = {
            "yandex_folder": "folder",
            "uploaded_files": 1,
            "uploaded_bytes": 10,
            "failed_files": ["2.dcm"],
            "files": [],
            "dicom_link": "s3://bucket/folder",
        }

        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                pacs_config_path=Path(directory) / "missing.json",
                state_file=Path(directory) / "state.json",
            )
            with patch(
                "hospital_agent.services.pacs.PACSClient",
                return_value=pacs_client,
            ), patch(
                "hospital_agent.services.yandex.YandexStorage",
                return_value=storage,
            ):
                with self.assertRaisesRegex(RuntimeError, "Yandex upload incomplete"):
                    get_dicom_study(
                        config,
                        {"study_uid": "1.2.3"},
                        "request-id",
                        "CT",
                        ViewerStub(),
                        AgentState(),
                    )

        storage.delete_folder.assert_called_once_with("folder")


if __name__ == "__main__":
    unittest.main()
