import unittest
import warnings
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from pydicom.dataset import Dataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, generate_uid
from pynetdicom import AE, ALL_TRANSFER_SYNTAXES, evt
from pynetdicom.sop_class import (
    CTImageStorage,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
)

from hospital_agent.services.pacs import PACSClient
from hospital_agent.support.dicom import build_date_range


def _config(output_dir: str) -> dict:
    return {
        "pacs": {
            "ip": "127.0.0.1",
            "port": 11112,
            "ae_title": "PACS",
        },
        "local": {
            "ae_title": "AGENT",
            "output_dir": output_dir,
            "retry_attempts": 1,
            "retry_delay": 0,
        },
    }


class PACSClientTests(unittest.TestCase):
    def test_named_search_periods_cover_requested_calendar_window(self):
        with patch("hospital_agent.support.dicom.datetime") as mocked_datetime:
            mocked_datetime.now.return_value = datetime(2026, 7, 31, 12, 0)
            mocked_datetime.strptime = datetime.strptime
            self.assertEqual(build_date_range("three_days"), "20260729-20260731")
            self.assertEqual(build_date_range("week"), "20260725-20260731")
            self.assertEqual(build_date_range("month"), "20260702-20260731")
            self.assertEqual(build_date_range("six_months"), "20260130-20260731")
            self.assertEqual(build_date_range("year"), "20250801-20260731")

    def test_real_local_association_find_and_get_round_trip(self):
        warning_context = warnings.catch_warnings()
        warning_context.__enter__()
        self.addCleanup(warning_context.__exit__, None, None, None)
        warnings.simplefilter("ignore", ResourceWarning)

        study_uid = generate_uid()
        sop_uid = generate_uid()
        image = Dataset()
        image.SpecificCharacterSet = "ISO_IR 192"
        image.SOPClassUID = CTImageStorage
        image.SOPInstanceUID = sop_uid
        image.StudyInstanceUID = study_uid
        image.PatientName = "TEST^PATIENT"
        image.PatientAge = "055Y"
        image.StudyDate = "20260727"
        image.StudyTime = "120000"
        image.StudyDescription = "Integration CT"
        image.Modality = "CT"
        image.file_meta = FileMetaDataset()
        image.file_meta.MediaStorageSOPClassUID = CTImageStorage
        image.file_meta.MediaStorageSOPInstanceUID = sop_uid
        image.file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        def handle_find(_event):
            result = Dataset()
            result.StudyInstanceUID = study_uid
            result.PatientName = image.PatientName
            result.PatientAge = image.PatientAge
            result.StudyDate = image.StudyDate
            result.StudyTime = image.StudyTime
            result.StudyDescription = image.StudyDescription
            result.ModalitiesInStudy = "CT"
            result.NumberOfStudyRelatedSeries = "1"
            result.NumberOfStudyRelatedInstances = "1"
            yield 0xFF00, result

        def handle_get(_event):
            yield 1
            yield 0xFF00, image

        pacs_ae = AE(ae_title="TESTPACS")
        pacs_ae.add_supported_context(StudyRootQueryRetrieveInformationModelFind)
        pacs_ae.add_supported_context(StudyRootQueryRetrieveInformationModelGet)
        pacs_ae.add_requested_context(CTImageStorage, ALL_TRANSFER_SYNTAXES)
        pacs_ae.add_supported_context(
            CTImageStorage,
            ALL_TRANSFER_SYNTAXES,
            scu_role=False,
            scp_role=True,
        )
        server = pacs_ae.start_server(
            ("127.0.0.1", 0),
            block=False,
            evt_handlers=[
                (evt.EVT_C_FIND, handle_find),
                (evt.EVT_C_GET, handle_get),
            ],
        )
        try:
            with TemporaryDirectory() as directory:
                config = _config(directory)
                config["pacs"]["port"] = server.server_address[1]
                config["pacs"]["ae_title"] = "TESTPACS"
                client = PACSClient(config)

                studies = client.find_studies(
                    modality="CT",
                    patient_name="TEST",
                    date_value="2026-07-27",
                )
                download = client.download_study(study_uid, lookup_metadata=False)

                self.assertEqual(len(studies), 1)
                self.assertEqual(str(studies[0]["uid"]), study_uid)
                self.assertTrue(download["ok"])
                self.assertEqual(download["received_files"], 1)
                self.assertEqual(download["modalities"], ["CT"])
                self.assertEqual(download["c_get_status"], "0x0000")
                self.assertEqual(
                    len(list(Path(download["study_dir"]).glob("*.dcm"))),
                    1,
                )
                stored_file = next(Path(download["study_dir"]).glob("*.dcm"))
                self.assertEqual(len(stored_file.name), 36)
                self.assertNotIn(sop_uid, stored_file.name)
        finally:
            server.shutdown()
            server.server_close()

    def test_find_connection_failure_is_retryable_instead_of_empty_success(self):
        with TemporaryDirectory() as directory:
            client = PACSClient(_config(directory))
            ae = MagicMock()
            ae.associate.return_value = SimpleNamespace(is_established=False)
            with patch.object(client, "_create_ae", return_value=ae):
                with self.assertRaisesRegex(RuntimeError, "cannot establish PACS"):
                    client.find_studies(
                        modality="CT",
                        patient_name="Иванов",
                        period="today",
                    )

    def test_find_filters_a_wrong_modality_returned_by_pacs(self):
        ct = Dataset()
        ct.StudyInstanceUID = "1.2.3"
        ct.PatientName = "Иванов^Иван"
        ct.ModalitiesInStudy = ["CT", "SR"]
        xa = Dataset()
        xa.StudyInstanceUID = "1.2.4"
        xa.PatientName = "Иванов^Иван"
        xa.ModalitiesInStudy = "XA"
        pending = SimpleNamespace(Status=0xFF00)
        success = SimpleNamespace(Status=0x0000)
        assoc = MagicMock()
        assoc.is_established = True
        assoc.send_c_find.return_value = iter(
            [(pending, ct), (pending, xa), (success, None)]
        )
        ae = MagicMock()
        ae.associate.return_value = assoc

        with TemporaryDirectory() as directory:
            client = PACSClient(_config(directory))
            with patch.object(client, "_create_ae", return_value=ae):
                studies = client.find_studies(modality="CT", patient_name="Иванов")

        self.assertEqual([str(item["uid"]) for item in studies], ["1.2.3"])
        assoc.release.assert_called_once()

    def test_download_requires_successful_final_c_get_status(self):
        dataset = Dataset()
        dataset.SOPClassUID = "1.2.840.10008.5.1.4.1.1.2"
        dataset.SOPInstanceUID = "1.2.3.4"
        dataset.StudyInstanceUID = "1.2.3"
        dataset.SpecificCharacterSet = "ISO_IR 192"
        dataset.PatientName = "Иванов^Иван"
        dataset.StudyDate = "20260726"
        dataset.StudyTime = "101500"
        dataset.Modality = "CT"
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = dataset.SOPClassUID
        file_meta.MediaStorageSOPInstanceUID = dataset.SOPInstanceUID
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian

        failed_status = SimpleNamespace(
            Status=0xA702,
            NumberOfFailedSuboperations=0,
            NumberOfWarningSuboperations=0,
            NumberOfCompletedSuboperations=1,
            NumberOfRemainingSuboperations=0,
        )
        assoc = MagicMock()
        assoc.is_established = True

        def associate(*_args, **kwargs):
            handler = kwargs["evt_handlers"][0][1]
            event = SimpleNamespace(dataset=dataset, file_meta=file_meta)

            def responses():
                handler(event)
                yield failed_status, None

            assoc.send_c_get.side_effect = lambda *_a, **_kw: responses()
            return assoc

        ae = MagicMock()
        ae.associate.side_effect = associate
        with TemporaryDirectory() as directory:
            client = PACSClient(_config(directory))
            with patch.object(client, "_create_ae", return_value=ae):
                result = client.download_study("1.2.3", lookup_metadata=False)

            self.assertFalse(result["ok"])
            self.assertEqual(result["c_get_status"], "0xA702")
            self.assertEqual(result["modalities"], ["CT"])
            self.assertEqual(
                len(list(Path(result["study_dir"]).glob("*.dcm"))),
                1,
            )

    def test_store_write_error_is_reported_to_pacs_without_escaping_handler(self):
        dataset = Dataset()
        dataset.SOPClassUID = CTImageStorage
        dataset.SOPInstanceUID = "1.2.3.4"
        dataset.StudyInstanceUID = "1.2.3"
        file_meta = FileMetaDataset()
        file_meta.MediaStorageSOPClassUID = dataset.SOPClassUID
        file_meta.MediaStorageSOPInstanceUID = dataset.SOPInstanceUID
        file_meta.TransferSyntaxUID = ExplicitVRLittleEndian
        failed_status = SimpleNamespace(
            Status=0xA702,
            NumberOfFailedSuboperations=1,
            NumberOfWarningSuboperations=0,
            NumberOfCompletedSuboperations=0,
            NumberOfRemainingSuboperations=0,
        )
        assoc = MagicMock(is_established=True)

        def associate(*_args, **kwargs):
            handler = kwargs["evt_handlers"][0][1]

            def responses():
                with patch.object(dataset, "save_as", side_effect=OSError("path too long")):
                    self.assertEqual(
                        handler(SimpleNamespace(dataset=dataset, file_meta=file_meta)),
                        0xC000,
                    )
                yield failed_status, None

            assoc.send_c_get.side_effect = lambda *_a, **_kw: responses()
            return assoc

        ae = MagicMock()
        ae.associate.side_effect = associate
        with TemporaryDirectory() as directory:
            client = PACSClient(_config(directory))
            with patch.object(client, "_create_ae", return_value=ae):
                result = client.download_study("1.2.3", lookup_metadata=False)

        self.assertFalse(result["ok"])
        self.assertEqual(result["received_files"], 0)
        self.assertEqual(result["failed_suboperations"], 1)


if __name__ == "__main__":
    unittest.main()
