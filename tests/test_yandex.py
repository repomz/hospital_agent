import unittest
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.services.yandex import YandexStorage


class YandexStorageTests(unittest.TestCase):
    def test_missing_environment_is_reported_before_boto_client_creation(self):
        with patch.dict(os.environ, {}, clear=True), patch(
            "hospital_agent.services.yandex.boto3.session.Session"
        ) as session:
            with self.assertRaisesRegex(RuntimeError, "YANDEX_BUCKET"):
                YandexStorage()

        session.assert_not_called()

    def test_connection_check_only_requires_access_to_configured_bucket(self):
        storage = object.__new__(YandexStorage)
        storage.bucket = "hospital-studies"
        storage.client = MagicMock()

        storage.check_connection()

        storage.client.head_bucket.assert_called_once_with(Bucket="hospital-studies")
        storage.client.list_buckets.assert_not_called()

    def test_upload_folder_orders_series_and_instances(self):
        with TemporaryDirectory() as directory:
            source = Path(directory)
            for name in ("late.dcm", "early.dcm", "middle.dcm"):
                (source / name).write_bytes(b"dicom")

            metadata = {
                "early.dcm": SimpleNamespace(SeriesNumber=1, InstanceNumber=1),
                "middle.dcm": SimpleNamespace(SeriesNumber=1, InstanceNumber=2),
                "late.dcm": SimpleNamespace(SeriesNumber=2, InstanceNumber=1),
            }
            storage = object.__new__(YandexStorage)
            storage.bucket = "bucket"
            storage.client = MagicMock()
            storage.client.generate_presigned_url.return_value = "https://example"
            storage.upload_dicom_with_retries = MagicMock(return_value=True)

            with patch(
                "hospital_agent.services.yandex.pydicom.dcmread",
                side_effect=lambda path, **_: metadata[Path(path).name],
            ):
                result = storage.upload_folder(source, "study", 1, 0)

        uploaded_names = [
            call.args[1] for call in storage.upload_dicom_with_retries.call_args_list
        ]
        self.assertEqual(
            uploaded_names,
            ["study/early.dcm", "study/middle.dcm", "study/late.dcm"],
        )
        self.assertEqual(result["uploaded_files"], 3)


if __name__ == "__main__":
    unittest.main()
