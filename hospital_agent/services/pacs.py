import logging
import time
from pathlib import Path
from typing import Any

from pydicom.dataset import Dataset
from pynetdicom import AE, ALL_TRANSFER_SYNTAXES, evt
from pynetdicom.presentation import build_role
from pynetdicom.sop_class import (
    CTImageStorage,
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    XRayAngiographicImageStorage,
)

from ..support.dicom import build_date_range, format_study_folder, format_yandex_folder


LOGGER = logging.getLogger("hospital_agent.services.pacs")

STORAGE_SOPS = [
    str(CTImageStorage),
    "1.2.840.10008.5.1.4.1.1.2.1",
    str(XRayAngiographicImageStorage),
    "1.2.840.10008.5.1.4.1.1.12.1.1",
    "1.2.840.10008.5.1.4.1.1.12.2",
    "1.2.840.10008.5.1.4.1.1.7",
]


class PACSClient:
    """Клиент PACS для поиска и скачивания DICOM-исследований."""

    def __init__(self, config: dict[str, Any]):
        """Инициализирует PACS-клиент из config.json."""
        self.pacs_ip = config["pacs"]["ip"]
        self.pacs_port = config["pacs"]["port"]
        self.pacs_ae = config["pacs"]["ae_title"]
        local_config = config["local"]
        self.local_ae = local_config["ae_title"]
        self.output_dir = Path(local_config["output_dir"])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.dimse_timeout = local_config.get("dimse_timeout", 30)
        self.acse_timeout = local_config.get("acse_timeout", 15)
        self.network_timeout = local_config.get("network_timeout", 15)
        self.retry_attempts = local_config.get("retry_attempts", 3)
        self.retry_delay = local_config.get("retry_delay", 5)
        self.retrieval_cancelled = False
        self._transfer_syntaxes = ALL_TRANSFER_SYNTAXES

    def cancel(self) -> None:
        """Помечает текущую операцию как отмененную."""
        self.retrieval_cancelled = True

    def _create_ae(self, include_storage: bool = True) -> AE:
        """Создает DICOM Application Entity с нужными контекстами передачи."""
        ae = AE(ae_title=self.local_ae)
        ae.dimse_timeout = self.dimse_timeout
        ae.acse_timeout = self.acse_timeout
        ae.network_timeout = self.network_timeout
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)
        if include_storage:
            for uid in STORAGE_SOPS:
                ae.add_requested_context(uid, self._transfer_syntaxes)
                ae.add_supported_context(uid, self._transfer_syntaxes)
        return ae

    def _lookup_study_info(self, study_uid: str) -> dict[str, Any]:
        """Получает имя пациента, дату и число instance по StudyInstanceUID."""
        ae = self._create_ae(include_storage=False)
        info: dict[str, Any] = {}
        assoc = ae.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)
        if not assoc.is_established:
            return info

        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.StudyInstanceUID = study_uid
        ds.PatientName = ""
        ds.StudyDate = ""
        ds.NumberOfStudyRelatedInstances = ""

        responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)
        for status, identifier in responses:
            if status and status.Status in (0xFF00, 0xFF01) and identifier:
                info = {
                    "PatientName": identifier.get("PatientName", ""),
                    "StudyDate": identifier.get("StudyDate", ""),
                    "NumberOfStudyRelatedInstances": identifier.get(
                        "NumberOfStudyRelatedInstances", ""
                    ),
                }
                break
        assoc.release()
        return info

    def find_studies(
        self,
        modality: str | None = None,
        period: str | None = None,
        date_value: str | None = None,
        date_range: str | None = None,
        patient_name: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ищет исследования в PACS по модальности, периоду и пациенту."""
        ae = self._create_ae(include_storage=False)
        try:
            LOGGER.info("Connecting to PACS %s:%s", self.pacs_ip, self.pacs_port)
            assoc = ae.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)
            if not assoc.is_established:
                LOGGER.error("Cannot connect to PACS")
                return []

            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            ds.StudyInstanceUID = ""
            ds.PatientName = ""
            ds.PatientBirthDate = ""
            ds.PatientAge = ""
            ds.StudyDate = ""
            ds.StudyTime = ""
            ds.StudyDescription = ""
            ds.ModalitiesInStudy = ""
            ds.NumberOfStudyRelatedSeries = ""
            ds.NumberOfStudyRelatedInstances = ""
            if modality:
                ds.ModalitiesInStudy = modality.upper()
            if patient_name:
                ds.SpecificCharacterSet = "ISO_IR 192"
                ds.PatientName = patient_name + "*"
            if date_range:
                ds.StudyDate = date_range
            elif period or date_value:
                ds.StudyDate = build_date_range(period, date_value)

            studies = []
            responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)
            for status, identifier in responses:
                if status and status.Status in (0xFF00, 0xFF01) and identifier:
                    uid = identifier.get("StudyInstanceUID", "")
                    if uid:
                        studies.append(
                            {
                                "number": len(studies) + 1,
                                "uid": uid,
                                "name": str(
                                    identifier.get("PatientName", "Unknown")
                                ).replace("^", " ").strip(),
                                "birth_date": str(identifier.get("PatientBirthDate", "")),
                                "age": str(identifier.get("PatientAge", "")),
                                "date": str(identifier.get("StudyDate", "")),
                                "time": str(identifier.get("StudyTime", "")),
                                "modality": str(identifier.get("ModalitiesInStudy", "")),
                                "description": str(identifier.get("StudyDescription", "")),
                                "series": str(identifier.get("NumberOfStudyRelatedSeries", "")),
                                "instances": str(identifier.get("NumberOfStudyRelatedInstances", "")),
                            }
                        )
            assoc.release()
            return studies
        except Exception:
            LOGGER.exception("PACS search error")
            return []

    def download_study(
        self,
        study_uid: str,
        lookup_metadata: bool = True,
    ) -> dict[str, Any]:
        """Скачивает исследование из PACS в локальную папку."""
        self.retrieval_cancelled = False
        study_info = self._lookup_study_info(study_uid) if lookup_metadata else {}
        patient_name = study_info.get("PatientName", "")
        study_date = study_info.get("StudyDate", "")
        expected_instances = _safe_int(study_info.get("NumberOfStudyRelatedInstances"))
        study_dir = self.output_dir / (
            format_study_folder(patient_name, study_date)
            if lookup_metadata
            else sanitize_study_uid(study_uid)
        )
        study_dir.mkdir(parents=True, exist_ok=True)

        received_count = 0
        received_bytes = 0
        patient_age = ""
        patient_birth_date = ""
        study_description = ""
        study_time = ""
        failed_suboperations = 0
        warning_suboperations = 0
        start_time = time.time()

        def handle_store(event: Any) -> int:
            """Сохраняет один полученный DICOM-объект и обновляет статистику передачи."""
            nonlocal patient_name, patient_age, patient_birth_date
            nonlocal study_date, study_description, study_time
            nonlocal received_bytes, received_count
            if self.retrieval_cancelled:
                return 0xC000
            dataset = event.dataset
            dataset.file_meta = event.file_meta
            filename = study_dir / f"{dataset.SOPInstanceUID}.dcm"
            dataset.save_as(str(filename), enforce_file_format=True)
            try:
                file_size = filename.stat().st_size
            except OSError:
                file_size = 0
            if not patient_name:
                patient_name = str(dataset.get("PatientName", ""))
            if not patient_age:
                patient_age = str(dataset.get("PatientAge", ""))
            if not patient_birth_date:
                patient_birth_date = str(dataset.get("PatientBirthDate", ""))
            if not study_date:
                study_date = str(dataset.get("StudyDate", ""))
            if not study_time:
                study_time = str(dataset.get("StudyTime", ""))
            if not study_description:
                study_description = str(dataset.get("StudyDescription", ""))
            received_bytes += file_size
            received_count += 1
            return 0x0000

        ae = self._create_ae(include_storage=True)
        role_neg = [build_role(uid, scp_role=True) for uid in STORAGE_SOPS]
        handlers = [(evt.EVT_C_STORE, handle_store)]

        try:
            assoc = ae.associate(
                self.pacs_ip,
                self.pacs_port,
                ae_title=self.pacs_ae,
                evt_handlers=handlers,
                ext_neg=role_neg,
            )
            if not assoc.is_established:
                LOGGER.error("Cannot establish retrieval association")
                return {"ok": False, "study_dir": str(study_dir)}

            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            ds.StudyInstanceUID = study_uid
            for status, _identifier in assoc.send_c_get(
                ds,
                StudyRootQueryRetrieveInformationModelGet,
            ):
                if status:
                    failed_suboperations = int(
                        getattr(status, "NumberOfFailedSuboperations", 0) or 0
                    )
                    warning_suboperations = int(
                        getattr(status, "NumberOfWarningSuboperations", 0) or 0
                    )
                    completed = int(
                        getattr(status, "NumberOfCompletedSuboperations", 0) or 0
                    )
                    remaining = int(
                        getattr(status, "NumberOfRemainingSuboperations", 0) or 0
                    )
                    total_suboperations = (
                        remaining
                        + completed
                        + failed_suboperations
                        + warning_suboperations
                    )
                    if total_suboperations:
                        expected_instances = max(expected_instances or 0, total_suboperations)
                if self.retrieval_cancelled:
                    LOGGER.warning("Retrieval cancelled")
                    break
            assoc.release()
        except Exception:
            LOGGER.exception("PACS retrieval error")
            return {"ok": False, "study_dir": str(study_dir)}

        duration = time.time() - start_time
        LOGGER.info(
            "PACS download complete: files=%s bytes=%s duration=%.2fs dir=%s",
            received_count,
            received_bytes,
            duration,
            study_dir,
        )
        if expected_instances is not None and received_count != expected_instances:
            LOGGER.warning("Expected %s instances but received %s", expected_instances, received_count)

        yandex_folder = (
            f"{format_yandex_folder(patient_name, study_date)}_"
            f"{sanitize_study_uid(study_uid)}"
        )
        return {
            "ok": (
                received_count > 0
                and failed_suboperations == 0
                and warning_suboperations == 0
                and (expected_instances is None or received_count == expected_instances)
            ),
            "study_uid": study_uid,
            "study_dir": str(study_dir),
            "received_files": received_count,
            "received_bytes": received_bytes,
            "expected_instances": expected_instances,
            "failed_suboperations": failed_suboperations,
            "warning_suboperations": warning_suboperations,
            "patient": patient_name,
            "age": patient_age,
            "birth_date": patient_birth_date,
            "study_date": study_date,
            "study_time": study_time,
            "description": study_description,
            "duration": duration,
            "yandex_folder": yandex_folder,
        }


def _safe_int(value: Any) -> int | None:
    """Преобразует значение к int, если это возможно."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def sanitize_study_uid(study_uid: str) -> str:
    """Возвращает безопасное имя временной папки из StudyInstanceUID."""
    return "".join(char if char.isalnum() or char in ".-_" else "_" for char in study_uid)
