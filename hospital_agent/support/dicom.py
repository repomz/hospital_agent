import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any


def load_pacs_config(path: str | Path = "config.json") -> dict[str, Any]:
    """Загружает JSON-конфигурацию PACS и локальных путей."""
    default_config: dict[str, Any] = {
        "pacs": {"ip": "127.0.0.1", "port": 4242, "ae_title": "ORTHANC"},
        "local": {
            "ae_title": "DICOM_CLI",
            "output_dir": "./downloaded_studies",
            "dimse_timeout": 30,
            "acse_timeout": 15,
            "network_timeout": 15,
            "retry_attempts": 3,
            "retry_delay": 5,
        },
    }

    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as file:
            config: dict[str, Any] = json.load(file)
        for key, value in default_config.items():
            if key not in config:
                config[key] = value
        return config
    return default_config


def build_date_range(period: str | None, date_value: str | None = None) -> str:
    """Преобразует период или точную дату в DICOM-диапазон StudyDate."""
    if date_value:
        try:
            return datetime.strptime(date_value, "%Y-%m-%d").strftime("%Y%m%d")
        except ValueError:
            return ""

    now = datetime.now()
    if period == "today":
        return now.strftime("%Y%m%d")
    if period == "yesterday":
        return (now - timedelta(days=1)).strftime("%Y%m%d")
    if period == "week":
        return (now - timedelta(days=7)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")
    if period == "month":
        return (now - timedelta(days=30)).strftime("%Y%m%d") + "-" + now.strftime("%Y%m%d")
    return ""


def sanitize_filename(value: object) -> str:
    """Заменяет недопустимые символы в имени файла или папки."""
    safe = []
    for char in str(value):
        if char.isalnum() or char in (" ", "-", "_", ".", "[", "]"):
            safe.append(char)
        else:
            safe.append("_")
    return "".join(safe).strip()


def format_study_folder(patient_name: object, study_date: object) -> str:
    """Формирует безопасное имя локальной папки исследования."""
    surname = ""
    if patient_name:
        parts = str(patient_name).split("^")
        surname = parts[0].strip() if parts else "Unknown"

    date_out = str(study_date or "")
    if date_out and len(date_out) == 8:
        date_out = f"{date_out[6:8]}.{date_out[4:6]}.{date_out[0:4]}"

    base = surname if surname else "Unknown"
    if date_out:
        base = f"{base} - {date_out}"
    return sanitize_filename(base)


def format_yandex_folder(patient_name: object, study_date: object) -> str:
    """Формирует имя папки исследования в Yandex Object Storage."""
    if patient_name:
        parts = str(patient_name).split("^")
        surname = parts[0].strip() if parts else "Unknown"
    else:
        surname = "Unknown"

    date_value = str(study_date or "")
    if date_value and len(date_value) == 8:
        date_formatted = f"{date_value[6:8]}.{date_value[4:6]}.{date_value[0:4]}"
    else:
        date_formatted = datetime.now().strftime("%d.%m.%Y")
    return f"{surname}_{date_formatted}"
