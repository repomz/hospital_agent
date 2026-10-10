#!/usr/bin/env python3
"""Считает операции в архиве DOCX и отправляет многолетнюю статистику viewer backend."""

from __future__ import annotations

import json
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Настройте эти значения перед первым запуском на больничном компьютере.
OPERATIONS_ARCHIVE_DIR = Path(r"C:\Viewer\operations")
START_YEAR = 2020
BACKEND_URL = "https://angio.su/api"
REQUEST_TIMEOUT_SECONDS = 120

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hospital_agent.clinical_terms import (  # noqa: E402
    operation_type,
    performed_assist_option,
    performed_ivus,
)
from hospital_agent.services.operation_reports import (  # noqa: E402
    analyze_operation_file,
    iter_operation_files,
)
from hospital_agent.support.tls import verified_ssl_context  # noqa: E402

OPERATION_LABELS = {
    "каг": "КАГ",
    "цаг": "ЦАГ",
    "стент_кор": "СТЕНТ КОР",
    "бап_кор": "БАП КОР",
    "стент_вса": "СТЕНТ ВСА",
    "стент_па": "СТЕНТ ПА",
    "стент_вк": "СТЕНТ В/К",
    "стент_нк": "СТЕНТ Н/К",
    "стент_почки": "СТЕНТ ПОЧКИ",
    "аневризма": "АНЕВРИЗМА",
    "инсульт": "ИНСУЛЬТ",
    "бап_голень": "Голень",
    "бап_периферии": "БАП ПЕРИФ",
    "бап_вса": "БАП ВСА",
    "бап_фистулы": "БАП ФИСТУЛЫ",
    "эма": "ЭМА",
    "экс_2к": "ЭКС 2к",
    "экс_1к": "ЭКС 1к",
    "вэкс": "ВЭКС",
    "экс_ревизия": "ЭКС РЕВ",
    "экс": "ЭКС ПРОЧ",
    "вабк": "ВАБК",
    "тромбаспирация": "ТА/ТЭ",
    "ангиография": "АНГИО",
    "ангиография_периферии": "АНГИО ПЕРИФЕРИИ",
    "эмболизация": "ЭМБОЛ",
    "эмболизация_периферии": "ЭМБОЛИЗАЦИЯ ПЕРИФЕРИИ",
    "экмо": "ЭКМО",
    "фистулография": "ФИСТУЛОГР",
    "стент_другие": "СТЕНТ ПРОЧ",
    "бап_другие": "БАП ПРОЧ",
    "другие": "ДРУГИЕ",
}
OPTION_TYPES = {"vabk": "ВАБК (доп.)", "ekmo": "ЭКМО (доп.)"}
OPERATION_TYPES = ("ВСУЗИ", *OPERATION_LABELS.values(), *OPTION_TYPES.values())


def _normalized(value: str) -> str:
    return " ".join(value.lower().replace("ё", "е").split())


def classify_historical_operation(operation: str, description: str = "") -> str:
    """Использует тот же основной тип, что и отправляемые агентом протоколы."""
    primary = operation_type(operation, description).lower().replace(" ", "_")
    return OPERATION_LABELS.get(primary, "ДРУГИЕ")


def build_statistics(root: Path, start_year: int) -> tuple[dict, int, int]:
    """Парсит архив штатным парсером агента и возвращает payload, успехи и пропуски."""
    counts: dict[int, Counter[str]] = defaultdict(Counter)
    identities: set[tuple[str, str, str]] = set()
    totals: Counter[int] = Counter()
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
        if performed_ivus(operation["operation"], operation.get("description", "")):
            counts[year]["ВСУЗИ"] += 1
        for option, label in OPTION_TYPES.items():
            if performed_assist_option(
                operation["operation"], operation.get("description", ""), option
            ):
                counts[year][label] += 1
        totals[year] += 1
        parsed += 1

    end_year = max(counts, default=datetime.now().year)
    years = []
    for year in range(start_year, end_year + 1):
        row = {
            operation_type: counts[year].get(operation_type, 0)
            for operation_type in OPERATION_TYPES
        }
        years.append({"year": year, "counts": row, "total": totals[year]})
    payload = {
        "schema_version": 3,
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
