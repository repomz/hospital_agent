import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from hospital_agent.services.yandex import YandexStorage


class YandexStorageTests(unittest.TestCase):
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
