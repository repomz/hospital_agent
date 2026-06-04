#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
DICOM PACS CLI Tool with Yandex Cloud Integration
PowerShell-friendly command-line interface for DICOM operations
"""

import argparse
import json
import logging
import sys
import os
import time
import threading
import queue
import signal
import mimetypes
from pathlib import Path
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed

# DICOM imports
from pynetdicom import AE, evt
from pynetdicom.sop_class import (
    StudyRootQueryRetrieveInformationModelFind,
    StudyRootQueryRetrieveInformationModelGet,
    XRayAngiographicImageStorage,
    CTImageStorage,
)
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian, JPEG2000Lossless, JPEGLossless
from pynetdicom.presentation import build_role
from pydicom.dataset import Dataset
from pydicom.multival import MultiValue

# S3 imports
import boto3
from botocore.exceptions import ConnectionError, ReadTimeoutError

# ==============================
# YANDEX CLOUD CONFIGURATION
# ==============================
YANDEX_ACCESS_KEY_ID = os.getenv("YANDEX_ACCESS_KEY_ID")
YANDEX_SECRET_ACCESS_KEY = os.getenv("YANDEX_SECRET_ACCESS_KEY")
YANDEX_ACCESS_KEY_ID = os.getenv("YANDEX_BUCKET")
YANDEX_SECRET_ACCESS_KEY = os.getenv("YANDEX_ENDPOINT")

# ==============================
# GLOBAL FLAGS FOR GRACEFUL SHUTDOWN
# ==============================
shutdown_requested = False
current_operation = None
operation_lock = threading.Lock()
cleanup_done = False
force_exit = False

# ==============================
# LOGGING CONFIGURATION
# ==============================
logging.getLogger("pynetdicom").setLevel(logging.CRITICAL)
logging.getLogger("pydicom").setLevel(logging.CRITICAL)
logging.getLogger("boto3").setLevel(logging.WARNING)
logging.getLogger("botocore").setLevel(logging.WARNING)

logger = logging.getLogger("dicom-cli")
logger.setLevel(logging.INFO)
console_handler = logging.StreamHandler()
console_handler.setFormatter(logging.Formatter("%(message)s"))
logger.addHandler(console_handler)

# ==============================
# SIGNAL HANDLERS
# ==============================
def signal_handler(signum, frame):
    """Handle Ctrl+C gracefully"""
    global shutdown_requested, force_exit
    
    if force_exit:
        print("\n\n⚠️ Force exit...")
        sys.exit(1)
    
    if shutdown_requested:
        print("\n\n⚠️ Force exit...")
        force_exit = True
        sys.exit(1)
    
    print("\n\n⚠️ Shutdown signal received. Finishing current operation...")
    shutdown_requested = True
    
    with operation_lock:
        if current_operation:
            current_operation.cancel()

def cleanup_and_exit():
    """Perform cleanup and exit"""
    global cleanup_done, force_exit
    
    if cleanup_done or force_exit:
        return
    
    cleanup_done = True
    print("\n✓ Exiting...")
    sys.exit(0)

signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# ==============================
# OPERATION CONTEXT
# ==============================
class OperationContext:
    """Контекст текущей операции для корректной отмены при завершении."""
    def __init__(self, name):
        """Инициализирует объект и его рабочее состояние."""
        self.name = name
        self.cancelled = False
    
    def __enter__(self):
        """Регистрирует операцию как текущую при входе в контекст."""
        global current_operation
        with operation_lock:
            current_operation = self
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Снимает текущую операцию и завершает процесс при запрошенной остановке."""
        global current_operation
        with operation_lock:
            current_operation = None
        
        if shutdown_requested and not force_exit:
            cleanup_and_exit()
    
    def cancel(self):
        """Помечает текущую операцию как отмененную."""
        self.cancelled = True
        logger.info(f"Cancelling: {self.name}")

# ==============================
# STATUS MESSAGES
# ==============================
STATUS_MESSAGES = {
    0xA700: "PACS refused: insufficient resources",
    0xA701: "PACS refused: cannot calculate matches",
    0xA702: "PACS refused: sub-operations failed",
    0xA801: "PACS refused: unknown AE recipient",
    0xA900: "PACS refused: invalid request identifier",
}

STORAGE_SOPS = [
    str(CTImageStorage),
    "1.2.840.10008.5.1.4.1.1.2.1",
    str(XRayAngiographicImageStorage),
    "1.2.840.10008.5.1.4.1.1.12.1.1",
    "1.2.840.10008.5.1.4.1.1.12.2",
    "1.2.840.10008.5.1.4.1.1.7",
]

# ==============================
# PROGRESS TRACKERS
# ==============================
class RetrievalProgress:
    """Отслеживает прогресс скачивания DICOM-исследования."""
    def __init__(self, expected_instances=None):
        """Инициализирует объект и его рабочее состояние."""
        self.expected_instances = expected_instances
        self.received_instances = 0
        self.received_size = 0
        self.lock = threading.Lock()
        self.start_time = time.time()
        self.cancelled = False
        
    def update(self, file_size):
        """Обновляет счетчики прогресса после обработки очередного файла."""
        if self.cancelled:
            return
        with self.lock:
            self.received_instances += 1
            self.received_size += file_size
            self._print_progress()
    
    def _print_progress(self):
        """Печатает текущий прогресс операции в консоль."""
        elapsed = time.time() - self.start_time
        speed = self.received_size / elapsed if elapsed > 0 else 0
        speed_mb = speed / (1024 * 1024)
        received_mb = self.received_size / (1024 * 1024)
        
        if self.expected_instances and self.expected_instances > 0:
            bar_length = 40
            filled = int(bar_length * self.received_instances / self.expected_instances)
            bar = '█' * filled + '░' * (bar_length - filled)
            print(f"\r[DOWNLOAD {bar}] {self.received_instances}/{self.expected_instances} "
                  f"({received_mb:.1f} MB) [{speed_mb:.1f} MB/s]", end='', flush=True)
        else:
            print(f"\r[DOWNLOAD] {self.received_instances} images ({received_mb:.1f} MB) "
                  f"[{speed_mb:.1f} MB/s]", end='', flush=True)
    
    def final_summary(self):
        """Печатает итоговую статистику операции."""
        print()
        elapsed = time.time() - self.start_time
        received_mb = self.received_size / (1024 * 1024)
        logger.info(f"Downloaded: {self.received_instances} images, {received_mb:.2f} MB, {elapsed:.2f}s")

class UploadProgress:
    """Отслеживает прогресс загрузки DICOM-файлов в облако."""
    def __init__(self):
        """Инициализирует объект и его рабочее состояние."""
        self.total_files = 0
        self.total_size = 0
        self.completed_files = 0
        self.completed_size = 0
        self.lock = threading.Lock()
        self.start_time = None
        self.failed_files = []
        
    def start(self):
        """Фиксирует время начала операции."""
        self.start_time = time.time()
    
    def add_file(self, file_size):
        """Добавляет файл в общий объем ожидаемой загрузки."""
        with self.lock:
            self.total_files += 1
            self.total_size += file_size
    
    def update(self, file_size, success=True, file_path=None):
        """Обновляет счетчики прогресса после обработки очередного файла."""
        with self.lock:
            self.completed_files += 1
            if success:
                self.completed_size += file_size
            else:
                self.failed_files.append(file_path)
            self._print_progress()
    
    def _print_progress(self):
        """Печатает текущий прогресс операции в консоль."""
        if self.start_time is None or self.total_files == 0:
            return
        elapsed = time.time() - self.start_time
        speed = self.completed_size / elapsed if elapsed > 0 else 0
        speed_mb = speed / (1024 * 1024)
        completed_mb = self.completed_size / (1024 * 1024)
        total_mb = self.total_size / (1024 * 1024)
        
        bar_length = 40
        filled = int(bar_length * self.completed_files / self.total_files)
        bar = '█' * filled + '░' * (bar_length - filled)
        
        print(f"\r[UPLOAD   {bar}] {self.completed_files}/{self.total_files} "
              f"({completed_mb:.1f}/{total_mb:.1f} MB) [{speed_mb:.1f} MB/s]", end='', flush=True)
    
    def final_summary(self):
        """Печатает итоговую статистику операции."""
        print()
        if self.completed_files > 0:
            elapsed = time.time() - self.start_time if self.start_time else 0
            completed_mb = self.completed_size / (1024 * 1024)
            logger.info(f"Uploaded: {self.completed_files} images, {completed_mb:.2f} MB, {elapsed:.2f}s")
        if self.failed_files:
            logger.warning(f"Failed uploads: {len(self.failed_files)} files")

# ==============================
# CONFIG LOADER
# ==============================
def load_config(path="config.json"):
    """Загружает JSON-конфигурацию, необходимую для подключения и путей."""
    default_config = {
        "pacs": {"ip": "127.0.0.1", "port": 4242, "ae_title": "ORTHANC"},
        "local": {
            "ae_title": "DICOM_CLI",
            "output_dir": "./downloaded_studies",
            "dimse_timeout": 30,
            "acse_timeout": 15,
            "network_timeout": 15,
            "retry_attempts": 3,
            "retry_delay": 5
        }
    }
    
    if os.path.exists(path):
        with open(path, "r") as f:
            config = json.load(f)
            for key, value in default_config.items():
                if key not in config:
                    config[key] = value
            return config
    return default_config

# ==============================
# DATE RANGE BUILDER
# ==============================
def build_date_range(period, date_value=None):
    """Преобразует период или точную дату в DICOM-диапазон StudyDate."""
    if date_value:
        try:
            return datetime.strptime(date_value, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            return ""
    
    now = datetime.now()
    if period == "today":
        return now.strftime("%Y%m%d")
    elif period == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y%m%d")
    elif period == "week":
        return (now - timedelta(days=7)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")
    elif period == "month":
        return (now - timedelta(days=30)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")
    return ""

# ==============================
# PACS CLIENT
# ==============================
class PACSClient:
    """Клиент PACS для поиска и скачивания DICOM-исследований."""
    def __init__(self, config):
        """Инициализирует объект и его рабочее состояние."""
        self.pacs_ip = config["pacs"]["ip"]
        self.pacs_port = config["pacs"]["port"]
        self.pacs_ae = config["pacs"]["ae_title"]
        self.local_ae = config["local"]["ae_title"]
        self.output_dir = Path(config["local"]["output_dir"])
        self.output_dir.mkdir(parents=True, exist_ok=True)
        
        self.dimse_timeout = config["local"].get("dimse_timeout", 30)
        self.acse_timeout = config["local"].get("acse_timeout", 15)
        self.network_timeout = config["local"].get("network_timeout", 15)
        self.retry_attempts = config["local"].get("retry_attempts", 3)
        self.retry_delay = config["local"].get("retry_delay", 5)
        
        self.retrieval_cancelled = False
        
        self._transfer_syntaxes = [
            ExplicitVRLittleEndian,
            ImplicitVRLittleEndian,
            JPEG2000Lossless,
            JPEGLossless,
        ]
    
    def cancel(self):
        """Помечает текущую операцию как отмененную."""
        self.retrieval_cancelled = True
    
    def _create_ae(self, include_storage=True):
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
    
    def _sanitize_filename(self, value):
        """Заменяет недопустимые символы в имени файла или папки."""
        safe = []
        for ch in value:
            if ch.isalnum() or ch in (" ", "-", "_", ".", "[", "]"):
                safe.append(ch)
            else:
                safe.append("_")
        return "".join(safe).strip()
    
    def _format_folder_name(self, patient_name, study_date, study_uid):
        """Формирует безопасное имя выходной папки исследования."""
        surname = ""
        if patient_name:
            parts = str(patient_name).split("^")
            surname = parts[0].strip() if len(parts) > 0 else "Unknown"
        
        date_out = study_date
        if study_date and len(study_date) == 8:
            date_out = f"{study_date[6:8]}.{study_date[4:6]}.{study_date[0:4]}"
        
        base = surname if surname else "Unknown"
        if date_out:
            base = f"{base} - {date_out}"
        return self._sanitize_filename(base)
    
    def find_studies(self, modality=None, period=None, date_value=None, patient_name=None):
        """Search for studies"""
        ae = self._create_ae(include_storage=False)
        
        try:
            logger.info(f"Connecting to PACS {self.pacs_ip}:{self.pacs_port}...")
            assoc = ae.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)
            
            if not assoc.is_established:
                logger.error("Cannot connect to PACS")
                return []
            
            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            ds.StudyInstanceUID = ""
            ds.PatientName = ""
            ds.StudyDate = ""
            ds.StudyDescription = ""
            ds.ModalitiesInStudy = ""
            ds.NumberOfStudyRelatedSeries = ""
            ds.NumberOfStudyRelatedInstances = ""
            
            if modality:
                ds.ModalitiesInStudy = modality.upper()
            if patient_name:
                ds.PatientName = patient_name + "*"
            if period or date_value:
                ds.StudyDate = build_date_range(period, date_value)
            
            logger.info("Searching...")
            responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)
            
            studies = []
            for status, identifier in responses:
                if status and status.Status in (0xFF00, 0xFF01) and identifier:
                    uid = identifier.get("StudyInstanceUID", "")
                    if uid:
                        studies.append({
                            "number": len(studies) + 1,
                            "uid": uid,
                            "name": str(identifier.get("PatientName", "Unknown")),
                            "date": str(identifier.get("StudyDate", "")),
                            "modality": str(identifier.get("ModalitiesInStudy", "")),
                            "description": str(identifier.get("StudyDescription", "")),
                            "series": str(identifier.get("NumberOfStudyRelatedSeries", "")),
                            "instances": str(identifier.get("NumberOfStudyRelatedInstances", ""))
                        })
            
            assoc.release()
            return studies
            
        except Exception as e:
            logger.error(f"Search error: {e}")
            return []
    
    def get_study(self, study_uid, upload_to_yandex=False):
        """Retrieve study from PACS"""
        self.retrieval_cancelled = False
        
        # Get study info
        ae = self._create_ae(include_storage=False)
        study_info = {}
        
        try:
            assoc = ae.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)
            if assoc.is_established:
                ds = Dataset()
                ds.QueryRetrieveLevel = "STUDY"
                ds.StudyInstanceUID = study_uid
                
                responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)
                for status, identifier in responses:
                    if status and status.Status in (0xFF00, 0xFF01) and identifier:
                        study_info = {
                            "PatientName": identifier.get("PatientName", ""),
                            "StudyDate": identifier.get("StudyDate", ""),
                        }
                        break
                assoc.release()
        except Exception as e:
            logger.error(f"Error getting study info: {e}")
        
        # Get expected instance count
        expected_instances = None
        try:
            ae2 = self._create_ae(include_storage=False)
            assoc = ae2.associate(self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae)
            if assoc.is_established:
                ds = Dataset()
                ds.QueryRetrieveLevel = "STUDY"
                ds.StudyInstanceUID = study_uid
                ds.NumberOfStudyRelatedInstances = ""
                
                responses = assoc.send_c_find(ds, StudyRootQueryRetrieveInformationModelFind)
                for status, identifier in responses:
                    if status and status.Status in (0xFF00, 0xFF01) and identifier:
                        count = identifier.get("NumberOfStudyRelatedInstances")
                        if count:
                            try:
                                expected_instances = int(count)
                            except:
                                pass
                        break
                assoc.release()
        except:
            pass
        
        # Create output folder
        patient_name = study_info.get("PatientName", "")
        study_date = study_info.get("StudyDate", "")
        folder_name = self._format_folder_name(patient_name, study_date, study_uid)
        study_dir = self.output_dir / folder_name
        study_dir.mkdir(parents=True, exist_ok=True)
        
        logger.info(f"Output: {study_dir}")
        if expected_instances:
            logger.info(f"Expected: {expected_instances} images")
        
        # Setup Yandex Cloud
        upload_queue = None
        upload_thread = None
        yandex_folder = None
        upload_progress = None
        
        if upload_to_yandex:
            try:
                session = boto3.session.Session()
                s3_client = session.client(
                    service_name='s3',
                    endpoint_url=YC_ENDPOINT,
                    aws_access_key_id=YC_ACCESS_KEY,
                    aws_secret_access_key=YC_SECRET_KEY,
                )
                s3_client.list_buckets()
                logger.info("✓ Connected to Yandex Cloud")
                
                # Create folder name
                if patient_name:
                    parts = str(patient_name).split("^")
                    surname = parts[0].strip() if len(parts) > 0 else "Unknown"
                    if study_date and len(study_date) == 8:
                        date_formatted = f"{study_date[6:8]}.{study_date[4:6]}.{study_date[0:4]}"
                    else:
                        date_formatted = datetime.now().strftime("%d.%m.%Y")
                    yandex_folder = f"{surname}_{date_formatted}"
                else:
                    yandex_folder = f"Unknown_{datetime.now().strftime('%d.%m.%Y')}"
                
                logger.info(f"Yandex folder: {yandex_folder}")
                
                upload_queue = queue.Queue(maxsize=100)
                upload_progress = UploadProgress()
                upload_progress.start()
                
                def upload_worker():
                    """Фоново загружает сохраненные DICOM-файлы в Yandex Cloud."""
                    while not shutdown_requested and not force_exit:
                        try:
                            file_info = upload_queue.get(timeout=1)
                            if file_info is None:
                                break
                            
                            file_path = file_info['path']
                            file_size = file_info['size']
                            object_name = f"{yandex_folder}/{Path(file_path).name}"
                            
                            upload_progress.add_file(file_size)
                            
                            for attempt in range(self.retry_attempts):
                                if shutdown_requested or force_exit or self.retrieval_cancelled:
                                    return
                                try:
                                    s3_client.upload_file(
                                        str(file_path), YC_BUCKET, object_name,
                                        ExtraArgs={'ContentType': 'application/dicom'}
                                    )
                                    upload_progress.update(file_size, True)
                                    break
                                except Exception as e:
                                    if attempt < self.retry_attempts - 1:
                                        time.sleep(self.retry_delay)
                                    else:
                                        upload_progress.update(file_size, False, str(file_path))
                        except queue.Empty:
                            continue
                        except Exception as e:
                            logger.error(f"Upload error: {e}")
                
                upload_thread = threading.Thread(target=upload_worker, daemon=True)
                upload_thread.start()
                
            except Exception as e:
                logger.error(f"Yandex Cloud init failed: {e}")
                upload_to_yandex = False
        
        # Retrieve study
        retrieval_progress = RetrievalProgress(expected_instances)
        received_count = 0
        received_bytes = 0
        start_time = time.time()
        
        def handle_store(event):
            """Сохраняет один полученный DICOM-объект и обновляет статистику передачи."""
            if self.retrieval_cancelled or shutdown_requested or force_exit:
                return 0xC000
            
            ds = event.dataset
            ds.file_meta = event.file_meta
            
            filename = study_dir / f"{ds.SOPInstanceUID}.dcm"
            ds.save_as(str(filename), enforce_file_format=True)
            
            try:
                file_size = filename.stat().st_size
                nonlocal received_bytes, received_count
                received_bytes += file_size
                received_count += 1
                retrieval_progress.update(file_size)
            except:
                file_size = 0
            
            if upload_queue is not None:
                upload_queue.put({'path': filename, 'size': file_size})
            
            return 0x0000
        
        # Setup retrieval association
        ae = self._create_ae(include_storage=True)
        role_neg = [build_role(uid, scp_role=True) for uid in STORAGE_SOPS]
        handlers = [(evt.EVT_C_STORE, handle_store)]
        
        try:
            assoc = ae.associate(
                self.pacs_ip, self.pacs_port, ae_title=self.pacs_ae,
                evt_handlers=handlers, ext_neg=role_neg
            )
            
            if not assoc.is_established:
                logger.error("Cannot establish retrieval association")
                if upload_queue:
                    upload_queue.put(None)
                return False
            
            ds = Dataset()
            ds.QueryRetrieveLevel = "STUDY"
            ds.StudyInstanceUID = study_uid
            
            logger.info("Retrieving study...")
            for status, identifier in assoc.send_c_get(ds, StudyRootQueryRetrieveInformationModelGet):
                if self.retrieval_cancelled or shutdown_requested:
                    logger.warning("\nRetrieval cancelled")
                    break
            
            assoc.release()
            
        except Exception as e:
            logger.error(f"Retrieval error: {e}")
            if upload_queue:
                upload_queue.put(None)
            return False
        
        retrieval_progress.final_summary()
        
        # Wait for uploads
        if upload_thread and upload_queue:
            logger.info("Waiting for uploads...")
            upload_queue.put(None)
            upload_thread.join(timeout=60)
            if upload_progress:
                upload_progress.final_summary()
        
        duration = time.time() - start_time
        logger.info("=" * 50)
        logger.info(f"Complete: {received_count} images, {received_bytes/(1024*1024):.2f} MB, {duration:.2f}s")
        if upload_to_yandex and upload_progress and upload_progress.completed_files > 0:
            logger.info(f"Uploaded: {upload_progress.completed_files} images to {yandex_folder}")
        logger.info("=" * 50)
        
        return True

# ==============================
# INTERACTIVE MODE
# ==============================
def interactive_mode():
    """Запускает интерактивный режим поиска и скачивания исследований."""
    config = load_config()
    client = PACSClient(config)
    
    print("\n" + "=" * 60)
    print("DICOM PACS CLI Tool with Yandex Cloud Integration")
    print("=" * 60)
    print(f"PACS: {config['pacs']['ip']}:{config['pacs']['port']} (AE: {config['pacs']['ae_title']})")
    print("Press Ctrl+C to exit")
    print("=" * 60)
    
    current_studies = []
    
    while not shutdown_requested and not force_exit:
        print("\nCommands:")
        print("  1. Search studies (by modality/period)")
        print("  2. Retrieve selected study")
        print("  3. Exit")
        
        choice = input("\nChoice (1/2/3): ").strip()
        
        if choice == "1":
            print("\nSearch filters (empty = all):")
            modality = input("Modality (CT/XA/MR): ").strip() or None
            period = input("Period (today/yesterday/week/month): ").strip() or None
            date = input("Exact date (YYYY-MM-DD): ").strip() or None
            
            print("\nSearching...")
            with OperationContext("search"):
                current_studies = client.find_studies(
                    modality=modality,
                    period=period,
                    date_value=date
                )
            
            if not current_studies:
                print("No studies found.")
                continue
            
            print(f"\nFound {len(current_studies)} studies:\n")
            print(f"{'#':<4} {'Patient':<30} {'Date':<12} {'Modality':<15} {'Images':<8}")
            print("-" * 75)
            for s in current_studies:
                images = s['instances'] if s['instances'] else '?'
                print(f"{s['number']:<4} {s['name'][:30]:<30} {s['date']:<12} "
                      f"{s['modality'][:15]:<15} {images:<8}")
            
            # After search, ask if user wants to filter by patient name
            if current_studies:
                filter_choice = input("\nFilter by patient name? (y/n): ").strip().lower()
                if filter_choice == 'y':
                    name_filter = input("Enter patient name (partial): ").strip()
                    if name_filter:
                        filtered = [s for s in current_studies if name_filter.lower() in s['name'].lower()]
                        if filtered:
                            # Group by patient
                            patients = {}
                            for s in filtered:
                                name = s['name'].strip()
                                if name not in patients:
                                    patients[name] = []
                                patients[name].append(s)
                            
                            print(f"\nPatients found:\n")
                            patient_list = list(patients.keys())
                            for i, name in enumerate(patient_list, 1):
                                count = len(patients[name])
                                print(f"  [{i}] {name} ({count} study{'s' if count > 1 else ''})")
                            
                            try:
                                p_num = int(input("\nSelect patient: "))
                                if p_num < 1 or p_num > len(patient_list):
                                    print("Invalid selection")
                                else:
                                    selected_patient = patient_list[p_num - 1]
                                    current_studies = patients[selected_patient]
                                    
                                    print(f"\nStudies for {selected_patient}:\n")
                                    print(f"{'#':<4} {'Date':<12} {'Modality':<15} {'Images':<8} {'Description'}")
                                    print("-" * 70)
                                    for i, s in enumerate(current_studies, 1):
                                        images = s['instances'] if s['instances'] else '?'
                                        print(f"{i:<4} {s['date']:<12} {s['modality'][:15]:<15} "
                                              f"{images:<8} {s['description'][:40]}")
                            except ValueError:
                                print("Invalid input")
                        else:
                            print("No matching patients found.")
        
        elif choice == "2":
            if not current_studies:
                print("No studies available. Run search first (option 1).")
                continue
            
            try:
                num = int(input(f"Select study (1-{len(current_studies)}): "))
                if num < 1 or num > len(current_studies):
                    print("Invalid selection")
                    continue
                
                study = current_studies[num - 1]
                upload = input("Upload to Yandex Cloud? (y/n): ").strip().lower() == 'y'
                
                print(f"\nRetrieving: {study['name']} - {study['description']}")
                with OperationContext(f"retrieve_{study['uid']}"):
                    client.get_study(study['uid'], upload_to_yandex=upload)
                
                # Clear current studies after retrieval
                current_studies = []
                
            except ValueError:
                print("Invalid input")
        
        elif choice == "3":
            print("Goodbye!")
            break
        
        else:
            print("Invalid choice. Please enter 1, 3, or 4.")
# ==============================
# MAIN
# ==============================
def main():
    """Точка входа CLI: разбирает аргументы и запускает сценарий скрипта."""
    parser = argparse.ArgumentParser(description="DICOM PACS CLI Tool")
    parser.add_argument("--interactive", "-i", action="store_true", help="Interactive mode")
    parser.add_argument("--config", default="config.json", help="Config file")
    
    subparsers = parser.add_subparsers(dest="command")
    
    find_parser = subparsers.add_parser("find", help="Search studies")
    find_parser.add_argument("--modality", help="Modality (CT/XA/MR)")
    find_parser.add_argument("--period", choices=["today", "yesterday", "week", "month"])
    find_parser.add_argument("--date", help="Exact date (YYYY-MM-DD)")
    find_parser.add_argument("--patient", help="Patient name (partial)")
    
    get_parser = subparsers.add_parser("get", help="Retrieve study")
    get_parser.add_argument("--study", required=True, help="Study UID")
    get_parser.add_argument("--yandex", action="store_true", help="Upload to Yandex")
    
    args = parser.parse_args()
    
    # Если нет аргументов - запускаем интерактивный режим
    if len(sys.argv) == 1:
        try:
            interactive_mode()
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        finally:
            cleanup_and_exit()
        return
    
    # Если есть аргумент --interactive
    if args.interactive:
        try:
            interactive_mode()
        except KeyboardInterrupt:
            print("\n\nInterrupted")
        finally:
            cleanup_and_exit()
        return
    
    config = load_config(args.config)
    client = PACSClient(config)
    
    if args.command == "find":
        studies = client.find_studies(
            modality=args.modality,
            period=args.period,
            date_value=args.date,
            patient_name=args.patient
        )
        
        if studies:
            print(f"\n{'#':<4} {'Patient':<35} {'Date':<12} {'Modality':<15} {'Images':<8}")
            print("-" * 80)
            for s in studies:
                images = s['instances'] if s['instances'] else '?'
                print(f"{s['number']:<4} {s['name'][:35]:<35} {s['date']:<12} "
                      f"{s['modality'][:15]:<15} {images:<8}")
        else:
            print("No studies found")
    
    elif args.command == "get":
        client.get_study(args.study, upload_to_yandex=args.yandex)
    
    else:
        parser.print_help()

if __name__ == "__main__":
    main()