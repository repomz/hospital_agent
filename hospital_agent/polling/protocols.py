import hashlib
import logging
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..services.operation_reports import (
    parse_operation_datetime,
    parse_operation_description,
    parse_operation_from_content,
    parse_patient_from_content,
    read_docx_text,
    is_operation_docx_candidate,
)

from ..config import AgentConfig, PollingConfig
from ..http_client import ViewerClient
from ..state import AgentState, save_state


LOGGER = logging.getLogger("hospital_agent.protocols")
STUDY_NAMESPACE = uuid.UUID("90153e75-8f87-4f1f-a874-6a0ef089cf68")
MAX_PROCESSED_PROTOCOL_KEYS = 10000


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
        files.extend(
            path
            for path in operations_dir.rglob("*")
            if is_operation_docx_candidate(path)
        )
    return sorted(files)


def _rfc3339(value: datetime) -> str:
    """Возвращает datetime в RFC3339-совместимом формате для Go time.Time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=datetime.now().astimezone().tzinfo)
    return value.astimezone(timezone.utc).isoformat()


def _protocol_uuid(path: Path, signature: str) -> uuid.UUID:
    """Создает стабильный UUID протокола по пути и подписи файла."""
    return uuid.uuid5(STUDY_NAMESPACE, f"{path.resolve()}:{signature}")


def protocol_identity(payload: dict[str, Any]) -> str:
    """Возвращает ключ одной операции независимо от имени и расположения DOCX."""
    raw = "|".join(
        (
            str(payload.get("study_id") or "").strip(),
            str(payload.get("time_beginning") or "").strip(),
            str(payload.get("patient") or "").casefold().replace("ё", "е").strip(),
        )
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _normalize_text(value: str) -> str:
    """Нормализует пробелы в извлеченном из DOCX тексте."""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def parse_study_id(content: str) -> str | None:
    """Извлекает номер операции после поля 'Операция:'."""
    match = re.search(
        r"О[ \t]*п[ \t]*е[ \t]*р[ \t]*а[ \t]*ц[ \t]*и[ \t]*я\s*:\s*(\d+)",
        content,
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def parse_full_operation_name(content: str) -> str | None:
    """Извлекает несокращённое название из строки «Операция»."""
    match = re.search(
        r"О[ \t]*п[ \t]*е[ \t]*р[ \t]*а[ \t]*ц[ \t]*и[ \t]*я\s*:\s*"
        r"\d+\s*([^\n\r]+)",
        content,
        flags=re.IGNORECASE,
    )
    return _normalize_text(match.group(1)).strip(" .") if match else None


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


def normalize_surgeon(value: str) -> str:
    """Извлекает фамилию хирурга без проверки по фиксированному справочнику."""
    normalized = _normalize_text(value).lower().replace("ё", "е")
    match = re.search(r"[а-яa-z][а-яa-z-]*", normalized, flags=re.IGNORECASE)
    return match.group(0) if match else ""


def classify_study_type(operation: str) -> str:
    """Нормализует известный тип или возвращает сокращенное название операции."""
    value = _normalize_text(operation).lower().replace("ё", "е")
    is_carotid = any(token in value for token in ("вса", "сонн", "каротид"))
    is_peripheral = any(
        token in value
        for token in (
            "перифер",
            "нижн",
            "пба",
            "нпа",
            "подвздош",
            "бедрен",
            "большеберц",
            "малоберц",
            "берцов",
            "подколен",
            "голен",
        )
    ) or re.search(r"\bнк\b", value) is not None

    if "тромбаспир" in value or re.search(r"\bта\b", value):
        return "тромбаспирация"
    if "стент" in value:
        if is_carotid:
            return "стент_вса"
        if is_peripheral:
            return "стент_периферии"
        return "стент_кор"
    if any(
        token in value
        for token in ("бап", "ангиопласт", "ангилопласт", "баллон", "балон")
    ):
        if is_carotid:
            return "бап_вса"
        if is_peripheral:
            return "бап_периферии"
        return "бап_кор"
    if any(token in value for token in ("цаг", "церебраль")):
        return "цаг"
    if any(token in value for token in ("каг", "коронарограф")):
        return "каг"
    return value.rstrip(" .")


def parse_protocol(path: Path, agent_id: str) -> dict[str, Any] | None:
    """Парсит DOCX-протокол операции в JSON StudyRequest для /studies."""
    content = read_docx_text(path)
    if not content:
        LOGGER.warning("Cannot read DOCX protocol: %s", path)
        return None

    operation_datetime = parse_operation_datetime_flexible(content)
    patient, age = parse_patient_from_content(content)
    operation = parse_operation_from_content(content)
    full_operation = parse_full_operation_name(content) or operation
    study_id = parse_study_id(content)
    required_fields = {
        "operation_datetime": operation_datetime,
        "patient": patient,
        "operation": operation,
        "study_id": study_id,
    }
    missing_fields = [name for name, value in required_fields.items() if not value]
    if missing_fields:
        LOGGER.warning(
            "Cannot parse required protocol fields for %s: missing=%s",
            path,
            ",".join(missing_fields),
        )
        return None

    study_type = classify_study_type(operation)
    raw_surgeon = parse_surgeon(content)
    surgeon = normalize_surgeon(raw_surgeon) or "не указано"

    record_number = parse_medical_record_number(content)
    description = parse_operation_description(content)
    return {
        "study_id": study_id,
        "patient": patient,
        "age": int(age) if str(age).isdigit() else 0,
        "department": department_from_record_number(record_number) or "не указано",
        "name_operation": full_operation,
        "study_type": study_type,
        "descr_operation": _normalize_text(description) or operation,
        "time_beginning": _rfc3339(operation_datetime),
        "time_duration": parse_operation_duration_min(content),
        "surgeon": surgeon,
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
    known_protocol_keys = set(state.processed_protocol_keys)
    for path in iter_protocol_files(polling.operations_dirs or []):
        signature = protocol_signature(path)
        state_key = str(path.resolve())
        if state.processed_protocols.get(state_key) == signature:
            continue

        payload = parse_protocol(path, config.agent_id)
        if payload is None:
            # Не повторяем ошибку на каждом polling. Если файл исправят,
            # размер или mtime изменится, подпись станет новой и он обработается снова.
            with state.lock:
                state.processed_protocols[state_key] = signature
                save_state(config.state_file, state)
            continue

        identity = protocol_identity(payload)
        if identity in known_protocol_keys:
            with state.lock:
                state.processed_protocols[state_key] = signature
                save_state(config.state_file, state)
            continue

        if viewer.post_json("/studies", payload):
            with state.lock:
                state.processed_protocols[state_key] = signature
                known_protocol_keys.add(identity)
                state.processed_protocol_keys.append(identity)
                state.processed_protocol_keys = state.processed_protocol_keys[
                    -MAX_PROCESSED_PROTOCOL_KEYS:
                ]
                save_state(config.state_file, state)
            sent_count += 1
            LOGGER.info(
                "Protocol sent: endpoint=/studies study_id=%s file=%s",
                payload["study_id"],
                path,
            )
    return sent_count
