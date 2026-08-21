#!/usr/bin/env python3
"""Считает операции в архиве DOCX и отправляет многолетнюю статистику viewer backend."""

from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Настройте эти значения перед первым запуском на больничном компьютере.
OPERATIONS_ARCHIVE_DIR = Path(r"C:\Viewer\operations")
START_YEAR = 2020
BACKEND_URL = "https://135.106.195.161/api"
REQUEST_TIMEOUT_SECONDS = 120

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hospital_agent.services.operation_reports import (  # noqa: E402
    analyze_operation_file,
    iter_operation_files,
)
from hospital_agent.support.tls import verified_ssl_context  # noqa: E402


OPERATION_TYPES = (
	"ВСУЗИ",
    "КАГ",
    "ЦАГ",
    "Стент кор",
    "БАП кор",
    "Стент ВСА",
    "Стент в/к",
    "Стент н/к",
    "Аневризма",
    "Инсульт",
    "Голень",
)


def _normalized(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def classify_historical_operation(operation: str, description: str = "") -> str:
    """Классифицирует архивную операцию в одну динамическую колонку таблицы."""
    value = _normalized(f"{operation} {description}")
    has_stent = "стент" in value
    has_bap = bool(re.search(r"\bбап\b|ангиопласт|баллон", value))

    if re.search(r"\bэма\b|эмболизац\w*\s+(?:маточ|миом)", value):
        return ""
    if re.search(r"всузи|внутрисосудист", value):
        return "ВСУЗИ"
    if re.search(r"эмболизац\w*.{0,80}аневризм|аневризм\w*.{0,80}эмболизац", value):
        return "Аневризма"
    if re.search(r"тромб(?:о)?(?:аспирац|экстракц)|\bт[аэ]\b|механическ\w*\s+реканализац", value):
        return "Инсульт"
    if has_stent and re.search(r"\bвса\b|внутренн\w*\s+сонн", value):
        return "Стент ВСА"
    if has_stent and re.search(r"верхн\w*\s+конеч|подключ", value):
        return "Стент в/к"
    if has_stent and re.search(r"нижн\w*\s+конеч|\b[он]па\b|подвздош|бедрен", value):
        return "Стент н/к"
    if has_bap and re.search(r"голен|берцов|подколенн", value):
        return "Голень"
    if has_stent and re.search(r"\bкаг\b|коронар|\bпна\b|\bпка\b|\bоа\b|стлка", value):
        return "Стент кор"
    if has_bap and re.search(r"\bкаг\b|коронар|\bпна\b|\bпка\b|\bоа\b|стлка", value):
        return "БАП кор"
    if re.search(r"\bцаг\b|церебраль\w*\s+ангиограф", value):
        return "ЦАГ"
    if re.search(r"\bкаг\b|коронарограф", value):
        return "КАГ"
    return ""


def build_statistics(root: Path, start_year: int) -> tuple[dict, int, int]:
    """Парсит архив штатным парсером агента и возвращает payload, успехи и пропуски."""
    counts: dict[int, Counter[str]] = defaultdict(Counter)
    identities: set[tuple[str, str, str]] = set()
    parsed = 0
    skipped = 0
    for path in iter_operation_files([root]):
        operation = analyze_operation_file(path)
        if not operation:
            skipped += 1
            continue
        year = operation["datetime"].year
        if year < start_year:
            continue
        identity = (
            _normalized(operation["patient"]),
            operation["datetime"].isoformat(),
            _normalized(operation["operation"]),
        )
        if identity in identities:
            continue
        identities.add(identity)
        operation_type = classify_historical_operation(
            operation["operation"], operation.get("description", "")
        )
        if not operation_type:
            skipped += 1
            continue
        counts[year][operation_type] += 1
        parsed += 1

    end_year = max(counts, default=datetime.now().year)
    years = []
    for year in range(start_year, end_year + 1):
        row = {operation_type: counts[year].get(operation_type, 0) for operation_type in OPERATION_TYPES}
        years.append({"year": year, "counts": row, "total": sum(row.values())})
    payload = {
        "schema_version": 2,
        "source": str(root),
        "start_year": start_year,
        "end_year": end_year,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "operation_types": list(OPERATION_TYPES),
        "years": years,
    }
    return payload, parsed, skipped


def upload_statistics(payload: dict, backend_url: str) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = Request(
        f"{backend_url.rstrip('/')}/statistics/history",
        data=body,
        method="PUT",
        headers={"Content-Type": "application/json; charset=utf-8"},
    )
    try:
        with urlopen(
            request,
            timeout=REQUEST_TIMEOUT_SECONDS,
            context=verified_ssl_context(),
        ) as response:
            if not 200 <= response.status < 300:
                raise RuntimeError(f"backend returned HTTP {response.status}")
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"backend returned HTTP {error.code}: {details}") from error
    except URLError as error:
        raise RuntimeError(f"backend is unavailable: {error.reason}") from error


def main() -> int:
    if not OPERATIONS_ARCHIVE_DIR.is_dir():
        print(f"Каталог архива не найден: {OPERATIONS_ARCHIVE_DIR}", file=sys.stderr)
        return 2
    payload, parsed, skipped = build_statistics(OPERATIONS_ARCHIVE_DIR, START_YEAR)
    upload_statistics(payload, BACKEND_URL)
    print(
        f"Статистика отправлена: {parsed} уникальных операций, "
        f"{len(payload['years'])} лет, пропущено неподходящих DOCX: {skipped}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
