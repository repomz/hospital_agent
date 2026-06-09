import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..services.operation_reports import (
    parse_operation_datetime,
    parse_operation_from_content,
    parse_patient_from_content,
    read_docx_text,
)

from ..config import AgentConfig, PollingConfig
from ..http_client import ViewerClient
from ..state import AgentState, save_state


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


def _normalize_text(value: str) -> str:
    """Нормализует пробелы в извлеченном из DOCX тексте."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def parse_study_id(content: str) -> str | None:
    """Извлекает номер операции после поля 'Операция:'."""
    match = re.search(r"Операция\s*:\s*(\d+)", content, flags=re.IGNORECASE)
    return match.group(1) if match else None


def parse_medical_record_number(content: str) -> str | None:
    """Извлекает номер карты стационарного больного."""
    match = re.search(
        r"Карта\s+стационарного\s+больного\s*([0-9]+\s*[-–]\s*[0-9]+)",
        content,
        flags=re.IGNORECASE,
    )
    return re.sub(r"\s+", "", match.group(1)).replace("–", "-") if match else None


def department_from_record_number(record_number: str | None) -> str:
    """Вычисляет отделение по началу номера истории болезни."""
    if not record_number:
        return ""
    if record_number.startswith("44"):
        return "кардиология"
    if record_number.startswith("42"):
        return "рсц"
    if record_number.startswith("26"):
        return "сосудистая хирургия"
    if record_number.startswith("179"):
        return "неврология"
    return ""


def parse_operation_description(content: str) -> str:
    """Извлекает описание операции из раздела 'Описание операции:'."""
    match = re.search(
        r"Описание\s+операции\s*:\s*(.*?)(?=\s+Исход\s*:|\s+Рек-но\s*:|\s+Расходные\s+материалы|\s+Опер\.\s*:|$)",
        content,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return _normalize_text(match.group(1)) if match else ""


def parse_operation_duration_min(content: str) -> int:
    """Извлекает длительность операции в минутах."""
    match = re.search(
        r"Длительность\s+операции\s*:\s*(?:(\d+)\s*час\w*)?\s*(?:(\d+)\s*мин\w*)?",
        content,
        flags=re.IGNORECASE,
    )
    if not match:
        return 0
    hours = int(match.group(1) or 0)
    minutes = int(match.group(2) or 0)
    return hours * 60 + minutes


def parse_operation_datetime_flexible(content: str) -> datetime | None:
    """Извлекает дату и время операции с допуском пробелов внутри даты."""
    parsed = parse_operation_datetime(content)
    if parsed is not None:
        return parsed

    match = re.search(
        r"Дата\s+и\s+время\s+операции\s*:\s*(\d{2})\s*\.\s*(\d{2})\s*\.\s*(\d{4})\s+(\d{2}:\d{2})",
        content,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    day, month, year, time_value = match.groups()
    return datetime.strptime(f"{day}.{month}.{year} {time_value}", "%d.%m.%Y %H:%M")


def parse_surgeon(content: str) -> str:
    """Извлекает хирурга после подписи 'Опер.:_______'."""
    match = re.search(r"Опер\.\s*:\s*_*\s*([^\n\r]+)", content, flags=re.IGNORECASE)
    return _normalize_text(match.group(1)) if match else ""


def parse_protocol(path: Path, agent_id: str) -> dict[str, Any] | None:
    """Парсит DOCX-протокол операции в JSON StudyRequest для /studies."""
    content = read_docx_text(path)
    if not content:
        LOGGER.warning("Cannot read DOCX protocol: %s", path)
        return None

    operation_datetime = parse_operation_datetime_flexible(content)
    patient, age = parse_patient_from_content(content)
    operation = parse_operation_from_content(content)
    study_id = parse_study_id(content)
    if not operation_datetime or not patient or not operation or not study_id:
        LOGGER.warning("Cannot parse required protocol fields: %s", path)
        return None

    now = datetime.now(timezone.utc)
    signature = protocol_signature(path)
    study_uuid = _protocol_uuid(path, signature)
    record_number = parse_medical_record_number(content)
    return {
        "id": str(study_uuid),
        "created_at": _rfc3339(now),
        "updated_at": _rfc3339(now),
        "study_id": study_id,
        "patient": patient,
        "age": int(age) if str(age).isdigit() else 0,
        "department": department_from_record_number(record_number),
        "name_operation": operation,
        "descr_operation": parse_operation_description(content),
        "time_begining": _rfc3339(operation_datetime),
        "time_duration": parse_operation_duration_min(content),
        "surgeon": parse_surgeon(content),
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
