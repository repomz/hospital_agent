import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.config import load_agent_config
from hospital_agent.services.commands import (
    execute_user_command,
    find_dicom_studies,
    find_operation_protocols,
    import_operation_protocol,
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
    def test_find_dicom_studies_exposes_patient_name_for_frontend(self):
        config = SimpleNamespace(pacs_config_path=Path("pacs.json"))
        with patch(
            "hospital_agent.support.dicom.load_pacs_config", return_value={}
        ), patch("hospital_agent.services.pacs.PACSClient") as pacs_client:
            pacs_client.return_value.find_studies.return_value = [
                {"uid": "1.2.3", "name": "Иванов Иван"}
            ]
            result = find_dicom_studies(
                config, {"patient": "Иванов", "period": "week"}, "XA"
            )

        self.assertEqual(result["studies"][0]["patient"], "Иванов Иван")

    def test_find_then_import_selected_protocol(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "protocol.docx"
            path.write_bytes(b"docx")
            protocol = {
                "study_id": "77",
                "patient": "Иванов И.И.",
                "name_operation": "Коронарография",
            }
            config = SimpleNamespace(
                agent_id="2",
                study_polling=SimpleNamespace(operations_dirs=[Path(directory)]),
            )
            viewer = ViewerStub()
            with patch(
                "hospital_agent.polling.protocols.iter_protocol_files",
                return_value=[path],
            ), patch(
                "hospital_agent.polling.protocols.parse_protocol",
                return_value=protocol,
            ):
                found = find_operation_protocols(config, {"patient": "Иванов"})
                selected = found["protocols"][0]
                result = import_operation_protocol(
                    config,
                    {"protocol_ref": selected["protocol_ref"]},
                    viewer,
                )

            self.assertTrue(result["imported"])
            self.assertEqual(viewer.posts, [("/studies", protocol)])

    def test_old_command_names_are_not_supported(self):
        config = SimpleNamespace()
        for command in (
            "send_study_to_yandex",
            "send_dicom_to_mapdr",
            "generate_operations_report",
            "get_report",
        ):
            with self.subTest(command=command):
                self.assertIsNone(
                    execute_user_command(config, command, {}, "request-id")
                )

    def test_sync_studies_scans_configured_protocol_directories(self):
        config = SimpleNamespace(
            study_polling=SimpleNamespace(operations_dirs=[Path("operations")]),
        )
        viewer = ViewerStub()
        state = AgentState()
        with patch(
            "hospital_agent.polling.protocols.poll_operation_protocols",
            return_value=3,
        ) as poll:
            result = execute_user_command(
                config,
                "sync_studies",
                {},
                "request-id",
                viewer=viewer,
                state=state,
            )

        self.assertEqual(result, {"sent": 3})
        poll.assert_called_once_with(config, config.study_polling, viewer, state)

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
            "modalities": ["CT"],
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
        storage.upload_folder.side_effect = lambda _source, folder, *_args: {
            **upload,
            "yandex_folder": folder,
            "dicom_link": f"s3://bucket/{folder}",
        }
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
        self.assertEqual(viewer.posts[0][0], "/ct_studies?force_pacs=true")
        self.assertTrue(viewer.posts[0][1]["dicom_link"].startswith("s3://bucket/"))
        self.assertNotIn("download_dir", result)
        self.assertEqual(len(state.yandex_cleanup), 1)

    def test_partial_yandex_upload_is_an_error_and_is_removed(self):
        pacs_client = MagicMock()
        pacs_client.download_study.return_value = {
            "ok": True,
            "study_uid": "1.2.3",
            "study_dir": "download",
            "received_files": 2,
            "patient": "Иванов^Иван",
            "study_date": "20260726",
            "modalities": ["CT"],
            "yandex_folder": "folder",
        }
        pacs_client.retry_attempts = 1
        pacs_client.retry_delay = 0
        storage = MagicMock()
        partial_upload = {
            "yandex_folder": "folder",
            "uploaded_files": 1,
            "uploaded_bytes": 10,
            "failed_files": ["2.dcm"],
            "files": [],
            "dicom_link": "s3://bucket/folder",
        }
        storage.upload_folder.side_effect = lambda _source, folder, *_args: {
            **partial_upload,
            "yandex_folder": folder,
            "dicom_link": f"s3://bucket/{folder}",
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

        deleted_folder = storage.delete_folder.call_args.args[0]
        self.assertTrue(deleted_folder.startswith("folder_"))

    def test_get_ct_rejects_an_xa_study_before_yandex_upload(self):
        pacs_client = MagicMock()
        pacs_client.download_study.return_value = {
            "ok": True,
            "study_uid": "1.2.3",
            "study_dir": "download",
            "received_files": 1,
            "patient": "Иванов^Иван",
            "study_date": "20260726",
            "modalities": ["XA"],
            "yandex_folder": "folder",
        }
        storage = MagicMock()

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
            ), self.assertRaisesRegex(RuntimeError, "modality mismatch"):
                get_dicom_study(
                    config,
                    {"study_uid": "1.2.3"},
                    "request-id",
                    "CT",
                    ViewerStub(),
                    AgentState(),
                )

        storage.upload_folder.assert_not_called()

    def test_get_ct_rejects_invalid_study_uid_without_contacting_pacs(self):
        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                pacs_config_path=Path(directory) / "missing.json",
                state_file=Path(directory) / "state.json",
            )
            with patch("hospital_agent.services.pacs.PACSClient") as pacs_client:
                with self.assertRaisesRegex(ValueError, "valid DICOM study_uid"):
                    get_dicom_study(
                        config,
                        {"study_uid": "../not-a-uid"},
                        "request-id",
                        "CT",
                        ViewerStub(),
                        AgentState(),
                    )

        pacs_client.assert_not_called()

    def test_yandex_is_checked_before_large_pacs_download(self):
        storage = MagicMock()
        storage.check_connection.side_effect = RuntimeError("bucket unavailable")
        with TemporaryDirectory() as directory:
            config = SimpleNamespace(
                pacs_config_path=Path(directory) / "missing.json",
                state_file=Path(directory) / "state.json",
            )
            with patch(
                "hospital_agent.services.yandex.YandexStorage",
                return_value=storage,
            ), patch("hospital_agent.services.pacs.PACSClient") as pacs_client:
                with self.assertRaisesRegex(RuntimeError, "bucket unavailable"):
                    get_dicom_study(
                        config,
                        {"study_uid": "1.2.3"},
                        "request-id",
                        "CT",
                        ViewerStub(),
                        AgentState(),
                    )

        storage.check_connection.assert_called_once()
        pacs_client.assert_not_called()

    def test_each_get_attempt_uses_an_isolated_yandex_folder(self):
        download = {
            "ok": True,
            "study_uid": "1.2.3",
            "study_dir": "download",
            "received_files": 1,
            "patient": "Иванов^Иван",
            "study_date": "20260726",
            "modalities": ["CT"],
            "yandex_folder": "Иванов_26.07.2026_1.2.3",
        }
        pacs_client = MagicMock()
        pacs_client.download_study.return_value = download
        pacs_client.retry_attempts = 1
        pacs_client.retry_delay = 0
        storage = MagicMock()

        def upload(_source, folder, *_args):
            return {
                "yandex_folder": folder,
                "uploaded_files": 1,
                "uploaded_bytes": 10,
                "failed_files": [],
                "files": [
                    {"name": "1.dcm", "size": 10, "url": "https://example/1"},
                ],
                "dicom_link": f"s3://bucket/{folder}",
            }

        storage.upload_folder.side_effect = upload
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
                first = get_dicom_study(
                    config,
                    {"study_uid": "1.2.3"},
                    "first-request",
                    "CT",
                    ViewerStub(),
                    state,
                )
                second = get_dicom_study(
                    config,
                    {"study_uid": "1.2.3"},
                    "second-request",
                    "CT",
                    ViewerStub(),
                    state,
                )

        self.assertNotEqual(first["dicom_link"], second["dicom_link"])
        self.assertEqual(len(state.yandex_cleanup), 2)


if __name__ == "__main__":
    unittest.main()
