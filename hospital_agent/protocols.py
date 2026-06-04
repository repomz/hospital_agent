import hashlib
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.report_plan import (
    parse_operation_datetime,
    parse_operation_from_content,
    parse_patient_from_content,
    read_docx_text,
)

from .config import AgentConfig, PollingConfig
from .http_client import ViewerClient
from .state import AgentState, save_state


LOGGER = logging.getLogger("hospital_agent.protocols")
STUDY_NAMESPACE = uuid.UUID("90153e75-8f87-4f1f-a874-6a0ef089cf68")


def protocol_signature(path: Path) -> str:
    """Возвращает подпись файла по размеру и времени изменения."""
    stat = path.stat()
    raw = f"{path.resolve()}:{stat.st_size}:{stat.st_mtime_ns}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def iter_protocol_files(operations_dirs: list[Path]) -> list[Path]:
    """Находит DOCX-протоколы операций во всех настроенных папках."""
    files: list[Path] = []
    for operations_dir in operations_dirs:
        if not operations_dir.exists():
            LOGGER.warning("Operations directory does not exist: %s", operations_dir)
            continue
        files.extend(path for path in operations_dir.rglob("*") if path.suffix.lower() == ".docx")
    return sorted(files)


def _rfc3339(value: datetime) -> str:
    """Возвращает datetime в RFC3339-совместимом формате для Go time.Time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.astimezone(timezone.utc).isoformat()


def _protocol_uuid(path: Path, signature: str) -> uuid.UUID:
    """Создает стабильный UUID протокола по пути и подписи файла."""
    return uuid.uuid5(STUDY_NAMESPACE, f"{path.resolve()}:{signature}")


def parse_protocol(path: Path, agent_id: str) -> dict[str, Any] | None:
    """Парсит DOCX-протокол операции в JSON StudyRequest для /studies."""
    content = read_docx_text(path)
    if not content:
        LOGGER.warning("Cannot read DOCX protocol: %s", path)
        return None

    operation_datetime = parse_operation_datetime(content)
    patient, age = parse_patient_from_content(content)
    operation = parse_operation_from_content(content)
    if not operation_datetime or not patient or not operation:
        LOGGER.warning("Cannot parse required protocol fields: %s", path)
        return None

    now = datetime.now(timezone.utc)
    signature = protocol_signature(path)
    study_uuid = _protocol_uuid(path, signature)
    return {
        "id": str(study_uuid),
        "created_at": _rfc3339(now),
        "updated_at": _rfc3339(now),
        "study_id": str(study_uuid),
        "patient": patient,
        "age": int(age) if str(age).isdigit() else age,
        "department": "",
        "name_operation": operation,
        "descr_operation": "",
        "time_begining": _rfc3339(operation_datetime),
        "time_duration": 0,
        "surgeon": "",
        "dicom_link": "",
    }


def poll_operation_protocols(
    config: AgentConfig,
    polling: PollingConfig,
    viewer: ViewerClient,
    state: AgentState,
) -> int:
    """Ищет новые DOCX-протоколы и отправляет их JSON на viewer /studies."""
    sent_count = 0
    for path in iter_protocol_files(polling.operations_dirs or []):
        signature = protocol_signature(path)
        state_key = str(path.resolve())
        if state.processed_protocols.get(state_key) == signature:
            continue

        payload = parse_protocol(path, config.agent_id)
        if payload is None:
            continue

        if viewer.post_json(polling.endpoint, payload):
            state.processed_protocols[state_key] = signature
            save_state(config.state_file, state)
            sent_count += 1
            LOGGER.info("Sent operation protocol: %s", path)
    return sent_count
