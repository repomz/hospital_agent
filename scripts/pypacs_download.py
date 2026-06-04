

import argparse
import json
import logging
import time
from datetime import datetime, timedelta
from pathlib import Path

from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    XRayAngiographicImageStorage,
    CTImageStorage,
)

# Импортируем стандартные синтаксисы передачи
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, JPEG2000Lossless, JPEGLossless

from pynetdicom.presentation import build_role # DEFAULT_TRANSFER_SYNTAXES, 

#from pynetdicom import AllStoragePresentationContexts

from pydicom.dataset import Dataset
from pydicom.multival import MultiValue


# ==============================
# LOGGING
# ==============================

logging.getLogger("pynetdicom").setLevel(logging.CRITICAL)
logging.getLogger("pydicom").setLevel(logging.CRITICAL)

logger = logging.getLogger("pacs")
logger.setLevel(logging.INFO)
logger.addHandler(logging.StreamHandler())


# ==============================
# CONFIG LOADER
# ==============================

def load_config(path="config.json"):
    """Загружает JSON-конфигурацию, необходимую для подключения и путей."""
    with open(path, "r") as f:
        return json.load(f)


# ==============================
# PERIOD HANDLER
# ==============================

def build_date_range(period, date_value=None):
    """Преобразует период или точную дату в DICOM-диапазон StudyDate."""
    now = datetime.now()

    if date_value:
        try:
            dt = datetime.strptime(date_value, "%Y-%m-%d")
            return dt.strftime("%Y%m%d")
        except ValueError:
            return ""

    if period == "today":
        return now.strftime("%Y%m%d")

    if period == "last_hour":
        return (now - timedelta(hours=1)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")

    if period == "last_day":
        return (now - timedelta(days=1)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")

    if period == "last_three_days":
        return (now - timedelta(days=3)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")

    if period == "week":
        return (now - timedelta(days=7)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")

    if period == "month":
        return (now - timedelta(days=30)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")

    return ""


# ==============================
# PACS CLIENT
# ==============================

STATUS_MESSAGES = {
    0xA700: "PACS отказал: недостаточно ресурсов",
    0xA701: "PACS отказал: недостаточно ресурсов, не удалось вычислить число совпадений",
    0xA702: "PACS отказал: недостаточно ресурсов, не удалось выполнить подоперации (часто из-за блокировки сети или AE)",
    0xA801: "PACS отказал: неизвестный AE-получатель (проверьте настройки AE/порт)",
    0xA900: "PACS отказал: идентификатор запроса некорректен",
    0xC000: "PACS завершил операцию с ошибкой/частично (см. предупреждения)",
}

STORAGE_SOPS = [
    str(CTImageStorage),
    "1.2.840.10008.5.1.4.1.1.2.1",      # Enhanced CT
    str(XRayAngiographicImageStorage),
    "1.2.840.10008.5.1.4.1.1.12.1.1",   # Enhanced XA
    "1.2.840.10008.5.1.4.1.1.12.2",     # X-Ray RF
    "1.2.840.10008.5.1.4.1.1.7",        # Secondary capture
]


class PACSClient:

    """Клиент PACS для поиска и скачивания DICOM-исследований."""
    def __init__(self, config, request_id):

        """Инициализирует объект и его рабочее состояние."""
        self.pacs_ip = config["pacs"]["ip"]
        self.pacs_port = config["pacs"]["port"]
        self.pacs_ae = config["pacs"]["ae_title"]
        local_cfg = config["local"]
        self.local_ae = local_cfg["ae_title"]

        self.output_dir = Path(local_cfg["output_dir"])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = Path(local_cfg.get("log_dir", "logs"))
        self.request_id = request_id
        self.dimse_timeout = local_cfg.get("dimse_timeout", 60)
        self.acse_timeout = local_cfg.get("acse_timeout", 30)
        self.network_timeout = local_cfg.get("network_timeout", 30)

        self.received = 0
        self.received_bytes = 0
        self.study_start_time = 0
        self.last_file_time = 0
        self._transfer_syntaxes = [
            ExplicitVRLittleEndian,
            ImplicitVRLittleEndian,
            JPEG2000Lossless,
            JPEGLossless,
            "1.2.840.10008.1.2.4.50",  # JPEG Baseline (Process 1)
            "1.2.840.10008.1.2.4.70",  # JPEG Lossless, SV1
            "1.2.840.10008.1.2.4.80",  # JPEG-LS Lossless
            "1.2.840.10008.1.2.4.81",  # JPEG-LS Near Lossless
            "1.2.840.10008.1.2.4.91",  # JPEG 2000
            "1.2.840.10008.1.2.5",     # RLE Lossless
            "1.2.840.10008.1.2.4.100", # MPEG2 Main Profile @ Main Level
            "1.2.840.10008.1.2.4.101", # MPEG2 Main Profile @ High Level
            "1.2.840.10008.1.2.4.102", # MPEG-4 AVC/H.264 High Profile / Level 4.1
            "1.2.840.10008.1.2.4.103", # MPEG-4 AVC/H.264 BD-compatible High Profile / Level 4.1
            "1.2.840.10008.1.2.4.104", # MPEG-4 AVC/H.264 High Profile / Level 4.2
            "1.2.840.10008.1.2.4.105", # MPEG-4 AVC/H.264 High Profile / Level 4.2 for 2D
        ]

        self._setup_logging()
        logger.info(
            "PACS target %s:%s (AE=%s) | Local AE=%s",
            self.pacs_ip,
            self.pacs_port,
            self.pacs_ae,
            self.local_ae,
        )

    def _setup_logging(self):
        """Настраивает консольные и файловые обработчики журналирования."""
        class ExactLevelFilter(logging.Filter):
            """Фильтр логов, пропускающий только записи указанного уровня."""
            def __init__(self, level):
                """Инициализирует объект и его рабочее состояние."""
                super().__init__()
                self.level = level

            def filter(self, record):
                """Проверяет, должен ли лог-запись пройти через фильтр."""
                return record.levelno == self.level

        class MinLevelFilter(logging.Filter):
            """Фильтр логов, пропускающий записи не ниже указанного уровня."""
            def __init__(self, level):
                """Инициализирует объект и его рабочее состояние."""
                super().__init__()
                self.level = level

            def filter(self, record):
                """Проверяет, должен ли лог-запись пройти через фильтр."""
                return record.levelno >= self.level

        self.log_dir.mkdir(parents=True, exist_ok=True)
        info_path = self.log_dir / f"{self.request_id} - pacs_download_info.log"
        debug_path = self.log_dir / f"{self.request_id} - pacs_download_debug.log"
        warn_path = self.log_dir / f"{self.request_id} - pacs_download_warning.log"

        logger.handlers.clear()
        logger.setLevel(logging.DEBUG)

        fmt = logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")

        stream_handler = logging.StreamHandler()
        stream_handler.setLevel(logging.INFO)
        stream_handler.setFormatter(fmt)

        info_handler = logging.FileHandler(info_path, encoding="utf-8")
        info_handler.setLevel(logging.INFO)
        info_handler.addFilter(ExactLevelFilter(logging.INFO))
        info_handler.setFormatter(fmt)

        debug_handler = logging.FileHandler(debug_path, encoding="utf-8")
        debug_handler.setLevel(logging.DEBUG)
        debug_handler.setFormatter(fmt)

        warn_handler = logging.FileHandler(warn_path, encoding="utf-8")
        warn_handler.setLevel(logging.WARNING)
        warn_handler.addFilter(MinLevelFilter(logging.WARNING))
        warn_handler.setFormatter(fmt)

        logger.addHandler(stream_handler)
        logger.addHandler(info_handler)
        logger.addHandler(debug_handler)
        logger.addHandler(warn_handler)

        for lib_name in ("pynetdicom", "pydicom"):
            lib_logger = logging.getLogger(lib_name)
            lib_logger.handlers.clear()
            lib_logger.setLevel(logging.DEBUG)
            lib_logger.addHandler(debug_handler)
            lib_logger.addHandler(warn_handler)
            lib_logger.propagate = False

    # ==========================
    # AE
    # ==========================


    def _create_ae(self, include_storage=True):
        """Создает DICOM Application Entity с нужными контекстами передачи."""
        ae = AE(ae_title=self.local_ae)
        ae.dimse_timeout = self.dimse_timeout
        ae.acse_timeout = self.acse_timeout
        ae.network_timeout = self.network_timeout

        logging.getLogger("pynetdicom").setLevel(logging.DEBUG)

    # -------------------------
    # Query / Retrieve
    # -------------------------
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelGet)

    # -------------------------
    # STORAGE (C-GET: we must act as SCP for storage)
    # -------------------------
        if include_storage:
            for uid in STORAGE_SOPS:
                ae.add_requested_context(uid, self._transfer_syntaxes)
                ae.add_supported_context(uid, self._transfer_syntaxes)

        return ae

    def _create_find_ae(self):
        """Создает DICOM Application Entity только для C-FIND запросов."""
        ae = AE(ae_title=self.local_ae)
        ae.dimse_timeout = self.dimse_timeout
        ae.acse_timeout = self.acse_timeout
        ae.network_timeout = self.network_timeout
        ae.add_requested_context(StudyRootQueryRetrieveInformationModelFind)
        return ae

    def _format_patient_folder(self, patient_name, study_date):
        """Формирует безопасное имя папки пациента по DICOM-имени и дате."""
        surname = ""
        initials = ""

        if patient_name:
            parts = str(patient_name).split("^")
            surname = parts[0].strip() if len(parts) > 0 else ""
            given = parts[1].strip() if len(parts) > 1 else ""
            middle = parts[2].strip() if len(parts) > 2 else ""
            letters = "".join(ch for ch in (given + middle) if ch.isalpha())
            initials = letters.upper()

        date_out = study_date
        if study_date and len(study_date) == 8:
            date_out = f"{study_date[6:8]}.{study_date[4:6]}.{study_date[0:4]}"

        base = surname if surname else "Unknown"
        if initials:
            base = f"{base} {initials}"
        if date_out:
            base = f"{base} - {date_out}"
        return self._sanitize_filename(base)

    def _sanitize_filename(self, value):
        """Заменяет недопустимые символы в имени файла или папки."""
        safe = []
        for ch in value:
            if ch.isalnum() or ch in (" ", "-", "_"):
                safe.append(ch)
            else:
                safe.append("_")
        return "".join(safe).strip()

    def _format_value(self, value):
        """Преобразует DICOM-значение или MultiValue в строку для вывода."""
        if value is None:
            return ""
        if isinstance(value, MultiValue):
            return "\\".join(str(v) for v in value if v is not None)
        return str(value)

    def _normalize_modalities(self, value):
        """
        Returns a tuple of (list_of_tokens, printable_value)
        """
        if value is None:
            return [], ""

        if isinstance(value, MultiValue):
            raw_tokens = [str(v) for v in value]
        else:
            raw_tokens = str(value).replace("/", "\\").split("\\")

        tokens = []
        for token in raw_tokens:
            text = token.strip()
            if not text:
                continue
            tokens.append(text.upper())

        return tokens, "/".join(tokens)

    def _describe_status(self, code):
        """Возвращает текстовое описание DICOM-статуса PACS."""
        if code is None:
            return ""
        return STATUS_MESSAGES.get(code, "")

    def _check_connection(self, label):
        """Проверяет доступность PACS через ассоциацию и C-ECHO."""
        ae = AE(ae_title=self.local_ae)
        ae.dimse_timeout = self.dimse_timeout
        ae.acse_timeout = self.acse_timeout
        ae.network_timeout = self.network_timeout
        ae.add_requested_context("1.2.840.10008.1.1")

        assoc = ae.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)
        if not assoc.is_established:
            logger.warning(f"{label}: PACS unreachable or association failed")
            return False
        try:
            status = assoc.send_c_echo()
            if status and hasattr(status, "Status") and status.Status == 0x0000:
                logger.info(f"{label}: PACS доступен (C-ECHO OK)")
            else:
                logger.warning(f"{label}: PACS доступен, но C-ECHO статус {getattr(status, 'Status', status)}")
        except Exception as e:
            logger.warning(f"{label}: C-ECHO error: {e}")
        finally:
            assoc.release()
        return True

    def _lookup_study_info(self, study_uid):
        """Запрашивает краткие сведения об исследовании по StudyInstanceUID."""
        ae = self._create_find_ae()
        assoc = ae.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)

        if not assoc.is_established:
            logger.error("Association failed (lookup study info)")
            return {}

        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.StudyInstanceUID = study_uid
        ds.PatientName = ""
        ds.StudyDate = ""
        ds.Modality = ""
        ds.NumberOfStudyRelatedSeries = ""
        ds.NumberOfStudyRelatedInstances = ""

        responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)

        info = {}
        for status, identifier in responses:
            if status and status.Status in (0xFF00, 0xFF01) and identifier:
                info = {
                    "PatientName": identifier.get("PatientName", ""),
                    "StudyDate": identifier.get("StudyDate", ""),
                    "Modality": identifier.get("Modality", ""),
                    "NumberOfStudyRelatedSeries": identifier.get("NumberOfStudyRelatedSeries", ""),
                    "NumberOfStudyRelatedInstances": identifier.get("NumberOfStudyRelatedInstances", ""),
                }
                break

        assoc.release()
        return info

    def _lookup_series_uids(self, study_uid):
        """Получает список SeriesInstanceUID для исследования."""
        ae = self._create_find_ae()
        assoc = ae.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)

        if not assoc.is_established:
            logger.error("Association failed (lookup series UIDs)")
            return []

        ds = Dataset()
        ds.QueryRetrieveLevel = "SERIES"
        ds.StudyInstanceUID = study_uid
        ds.SeriesInstanceUID = ""

        responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)

        series_uids = set()
        for status, identifier in responses:
            if status and status.Status in (0xFF00, 0xFF01) and identifier:
                uid = identifier.get("SeriesInstanceUID", "")
                if uid:
                    series_uids.add(str(uid))

        assoc.release()
        return sorted(series_uids)

    def _reset_transfer_stats(self):
        """Сбрасывает счетчики скачивания перед новой попыткой C-GET."""
        self.received = 0
        self.received_bytes = 0
        self.study_start_time = time.time()
        self.last_file_time = self.study_start_time

    def _build_store_handler(self, study_dir, expected_instances):
        """Создает обработчик C-STORE для сохранения полученных DICOM-файлов."""
        def handle_store(event):
            """Сохраняет один полученный DICOM-объект и обновляет статистику передачи."""
            ds = event.dataset
            ds.file_meta = event.file_meta

            filename = study_dir / f"{ds.SOPInstanceUID}.dcm"
            ds.save_as(str(filename), enforce_file_format=True)

            try:
                self.received_bytes += filename.stat().st_size
            except OSError:
                pass

            self.received += 1
            now = time.time()
            file_duration = now - self.last_file_time
            self.last_file_time = now

            if expected_instances is not None:
                logger.info(
                    "Received %s/%s (%.1fs) SOPClass=%s",
                    self.received,
                    expected_instances,
                    file_duration,
                    ds.SOPClassUID,
                )
            else:
                logger.info(
                    "Received %s instances (%.1fs) SOPClass=%s",
                    self.received,
                    file_duration,
                    ds.SOPClassUID,
                )
            return 0x0000

        return handle_store

    def _build_study_dataset(self, study_uid):
        """Создает DICOM Dataset для C-GET на уровне исследования."""
        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.StudyInstanceUID = study_uid
        return ("STUDY", ds)

    def _build_series_datasets(self, study_uid, series_uids):
        """Создает DICOM Dataset-ы для C-GET по отдельным сериям."""
        datasets = []
        for index, series_uid in enumerate(series_uids, 1):
            ds = Dataset()
            ds.QueryRetrieveLevel = "SERIES"
            ds.StudyInstanceUID = study_uid
            ds.SeriesInstanceUID = series_uid
            label = f"SERIES #{index} ({series_uid})"
            datasets.append((label, ds))
        return datasets

    def _status_to_int(self, status):
        """Преобразует объект статуса pynetdicom в целочисленный код."""
        if status is None:
            return None
        if hasattr(status, "Status"):
            return status.Status
        if isinstance(status, int):
            return status
        return None

    def _status_to_hex(self, status):
        """Преобразует статус PACS в hex-строку для логов."""
        value = self._status_to_int(status)
        return hex(value) if value is not None else "None"

    def _status_is_success(self, status):
        """Определяет, считается ли статус C-GET успешным или предупреждением."""
        code = self._status_to_int(status)
        if code is None:
            return False
        # 0x0000 success, 0xB000/0xB006/0xB007 partial success warnings
        return code == 0x0000 or (0xB000 <= code <= 0xBFFF)

    def _log_suboperation_counts(self, op, label, identifier):
        """Записывает в лог счетчики подопераций C-GET."""
        if not identifier:
            return
        remaining = identifier.get("NumberOfRemainingSuboperations")
        completed = identifier.get("NumberOfCompletedSuboperations")
        failed = identifier.get("NumberOfFailedSuboperations")
        warning = identifier.get("NumberOfWarningSuboperations")
        parts = []
        if remaining is not None:
            parts.append(f"remaining={remaining}")
        if completed is not None:
            parts.append(f"completed={completed}")
        if failed is not None:
            parts.append(f"failed={failed}")
        if warning is not None:
            parts.append(f"warning={warning}")
        if parts:
            logger.debug("%s %s progress: %s", op, label, " ".join(parts))

    def _log_status_failure(self, op, label, status):
        """Записывает в лог расшифрованную ошибку статуса C-GET."""
        hex_code = self._status_to_hex(status)
        code = self._status_to_int(status)
        desc = self._describe_status(code)
        if desc:
            logger.error("%s %s failed with status %s (%s)", op, label, hex_code, desc)
        else:
            logger.error("%s %s failed with status %s", op, label, hex_code)

    def _perform_c_get(self, datasets, handlers):
        """Выполняет C-GET по подготовленным Dataset-ам и обработчикам."""
        if not datasets:
            logger.error("C-GET datasets list is empty")
            return False, None

        ae = self._create_ae()
        role_neg = [build_role(uid, scp_role=True) for uid in STORAGE_SOPS]
        assoc = ae.associate(
            self.pacs_ip,
            self.pacs_port,
            ae_title=self.pacs_ae,
            evt_handlers=handlers,
            ext_neg=role_neg,
        )
        if not assoc.is_established:
            logger.error("C-GET association failed")
            return False, None

        last_status = None
        status_code = None
        try:
            for label, req_ds in datasets:
                logger.info("Starting C-GET for %s", label)
                for status, identifier in assoc.send_c_get(req_ds, StudyRootQueryRetrieveInformationModelGet):
                    self._log_suboperation_counts("C-GET", label, identifier)
                    logger.debug("C-GET %s status %s", label, self._status_to_hex(status))
                    last_status = status
                status_code = self._status_to_int(last_status)
                if not self._status_is_success(last_status):
                    self._log_status_failure("C-GET", label, last_status)
                    assoc.release()
                    return False, status_code
        except Exception as e:
            logger.error("C-GET exception: %s", e)
            assoc.release()
            return False, status_code

        assoc.release()
        if last_status:
            logger.info("C-GET completed with status %s", self._status_to_hex(last_status))
        if self.received == 0:
            logger.warning("C-GET completed but no instances were stored")
            return False, status_code
        return True, status_code


    # ==========================
    # FIND
    # ==========================

    def find(self, modality=None, period=None, date_value=None, patient=None, ct_flag=False, xa_flag=False, descr=None):

        """Ищет исследования в PACS по модальности, периоду, пациенту и описанию."""
        self._check_connection("FIND")
        ae = self._create_ae(include_storage=False)

        logger.info("Starting C-FIND...")

        assoc = ae.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)

        if not assoc.is_established:
            logger.error("Association failed")
            return
        
        logger.info("Association established (FIND)")

        selected_modality = None
        if ct_flag:
            selected_modality = "CT"
        elif xa_flag:
            selected_modality = "XA"
        elif modality:
            selected_modality = modality.upper()

        if descr:
            if selected_modality and selected_modality != "CT":
                logger.warning("--descr используется только для КТ, модальность принудительно установлена в CT")
            selected_modality = "CT"

        ds = Dataset()
        ds.QueryRetrieveLevel = "STUDY"
        ds.Modality = selected_modality or ""
        ds.ModalitiesInStudy = ""
        ds.StudyDate = build_date_range(period, date_value)
        ds.PatientName = (patient + "*") if patient else ""
        ds.StudyInstanceUID = ""
        ds.StudyDescription = ""
        ds.NumberOfStudyRelatedSeries = ""
        ds.NumberOfStudyRelatedInstances = ""
        ds.NumberOfStudyRelatedSeries = ""
        ds.NumberOfStudyRelatedInstances = ""

        responses = assoc.send_c_find(
            ds,
            StudyRootQueryRetrieveInformationModelFind
        )

        studies = {}

        for status, identifier in responses:
            if status and status.Status in (0xFF00, 0xFF01):

                uid = identifier.get("StudyInstanceUID", "")
                name = self._format_value(identifier.get("PatientName", ""))
                date = self._format_value(identifier.get("StudyDate", ""))
                desc = self._format_value(identifier.get("StudyDescription", ""))
                modality_value = identifier.get("ModalitiesInStudy")
                if modality_value is None:
                    modality_value = identifier.get("Modality")
                mod_tokens, mod_display = self._normalize_modalities(modality_value)
                series_n = self._format_value(identifier.get("NumberOfStudyRelatedSeries", ""))
                inst_n = self._format_value(identifier.get("NumberOfStudyRelatedInstances", ""))

                if uid:
                    studies[uid] = {
                        "name": name,
                        "date": date,
                        "mod_display": mod_display,
                        "mod_tokens": mod_tokens,
                        "desc": desc,
                        "series": series_n,
                        "instances": inst_n,
                    }

        assoc.release()

        if selected_modality:
            wanted = selected_modality.upper()
            filtered = {}
            for uid, data in studies.items():
                if wanted in data["mod_tokens"]:
                    filtered[uid] = data
            logger.info("Фильтр modality=%s: найдено %s исследований (из %s)", wanted, len(filtered), len(studies))
            studies = filtered

        if descr:
            keyword = descr.lower()
            filtered = {}
            for uid, data in studies.items():
                mod_tokens = data["mod_tokens"]
                if "CT" not in mod_tokens:
                    continue
                desc = data["desc"] or ""
                if keyword in desc.lower():
                    filtered[uid] = data
            logger.info("Фильтр descr=%s: найдено %s исследований (из %s)", descr, len(filtered), len(studies))
            studies = filtered

        print("\nFOUND STUDIES:\n")
        header = f"{'#':>3}  {'Patient':<24}  {'Date':<8}  {'Mod':<7}  {'S':>3}  {'I':>3}  {'Description':<40}  {'StudyInstanceUID'}"
        print(header)
        print("-" * len(header))
        for i, (uid, data) in enumerate(studies.items(), 1):
            name = data["name"]
            date = data["date"]
            mod = data["mod_display"]
            desc = data["desc"]
            series_n = data["series"]
            inst_n = data["instances"]
            series_str = str(series_n) if series_n else "-"
            inst_str = str(inst_n) if inst_n else "-"
            name_fmt = (name[:24]) if len(name) > 24 else name
            desc_fmt = (desc[:40]) if len(desc) > 40 else desc
            print(
                f"{i:>3}  {name_fmt:<24}  {date:<8}  {mod:<7}  {series_str:>3}  {inst_str:>3}  {desc_fmt:<40}  {uid}"
            )

    # ==========================
    # GET
    # ==========================
    def get(self, study_uid, debug=False):

        """Скачивает исследование из PACS и при необходимости выполняет дополнительные действия."""
        if debug:
            logger.setLevel(logging.DEBUG)
            logging.getLogger("pynetdicom").setLevel(logging.DEBUG)
            logging.getLogger("pydicom").setLevel(logging.DEBUG)

        self._check_connection("GET")
        study_info = self._lookup_study_info(study_uid)
        modality = study_info.get("Modality", "")

        series_uids = self._lookup_series_uids(study_uid)
        if series_uids:
            logger.info(f"Series UIDs: {len(series_uids)}")

        patient_name = study_info.get("PatientName", "")
        study_date = study_info.get("StudyDate", "")
        study_folder = self._format_patient_folder(patient_name, study_date)
        study_dir = self.output_dir / study_folder
        study_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Study modality: {modality or 'UNKNOWN'}")
        logger.info(f"Output folder: {study_dir}")
        series_count = study_info.get("NumberOfStudyRelatedSeries", "")
        instance_count = study_info.get("NumberOfStudyRelatedInstances", "")
        expected_instances = None
        try:
            expected_instances = int(instance_count)
        except (TypeError, ValueError):
            expected_instances = None
        if series_count or instance_count:
            logger.info(f"Study counts: series={series_count or 'n/a'} instances={instance_count or 'n/a'}")

        study_dataset = [self._build_study_dataset(study_uid)]
        series_datasets = self._build_series_datasets(study_uid, series_uids)

        handlers = [
            (evt.EVT_C_STORE, self._build_store_handler(study_dir, expected_instances))
        ]

        def run_attempt(label, datasets):
            """Запускает одну попытку C-GET и возвращает ее статус."""
            self._reset_transfer_stats()
            success_attempt, status_attempt = self._perform_c_get(datasets, handlers)
            total_duration = time.time() - self.study_start_time
            logger.info(
                "C-GET (%s) finished in %.1fs, received=%s instances",
                label,
                total_duration,
                self.received,
            )
            return success_attempt, status_attempt

        success, last_status_code = run_attempt("study", study_dataset)

        need_series_retry = False
        if series_datasets:
            if not success and last_status_code == 0xA702:
                meaning = self._describe_status(last_status_code)
                logger.warning(
                    "C-GET failed with 0xA702 at study level (%s), retrying по сериям (%s)",
                    meaning or "см. журнал PACS",
                    len(series_datasets),
                )
                need_series_retry = True
            elif expected_instances is not None and self.received < expected_instances:
                logger.warning(
                    "C-GET received only %s/%s instances, retrying по сериям (%s)",
                    self.received,
                    expected_instances,
                    len(series_datasets),
                )
                need_series_retry = True

        if need_series_retry:
            success, last_status_code = run_attempt("series", series_datasets)

        if not success:
            meaning = self._describe_status(last_status_code)
            if meaning:
                logger.error(
                    "Study retrieval failed (последний статус %s: %s)",
                    hex(last_status_code) if last_status_code is not None else "None",
                    meaning,
                )
            else:
                logger.error("Study retrieval failed")
            return

        logger.info("Total received: %s files (%.2f MB)", self.received, self.received_bytes / (1024 * 1024))

        if expected_instances is not None and self.received != expected_instances:
            logger.warning(
                "Expected %s instances but received %s",
                expected_instances,
                self.received,
            )


# ==============================
# MAIN
# ==============================

def main():

    """Точка входа CLI: разбирает аргументы и запускает сценарий скрипта."""
    parser = argparse.ArgumentParser(description="DICOM PACS CLI")

    subparsers = parser.add_subparsers(dest="command")

    # FIND
    find_parser = subparsers.add_parser("find")
    mod_group = find_parser.add_mutually_exclusive_group()
    mod_group.add_argument("--modality")
    mod_group.add_argument("--xa", action="store_true", help="Быстрый фильтр по XA")
    mod_group.add_argument("--ct", action="store_true", help="Быстрый фильтр по CT")
    find_parser.add_argument("--period",
                             choices=["today", "last_hour", "last_day", "last_three_days", "week", "month"])
    find_parser.add_argument("--date", help="Exact date in YYYY-MM-DD")
    find_parser.add_argument("--patient")
    find_parser.add_argument("--descr", choices=["brain", "limb"], help="Фильтрация КТ по StudyDescription")

    # GET
    get_parser = subparsers.add_parser("get")
    get_parser.add_argument("--study", required=True)
    get_parser.add_argument("--debug", action="store_true")

    args = parser.parse_args()

    request_id = datetime.now().strftime("%Y%m%d-%H%M%S")
    config = load_config()
    client = PACSClient(config, request_id=request_id)

    if args.command == "find":
        client.find(
            modality=args.modality,
            period=args.period,
            date_value=args.date,
            patient=args.patient,
            ct_flag=args.ct,
            xa_flag=args.xa,
            descr=args.descr,
        )

    elif args.command == "get":
        client.get(
            study_uid=args.study,
            debug=args.debug,
        )


if __name__ == "__main__":
    main()
