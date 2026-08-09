#!/usr/bin/env python3
"""Безопасно загружает DOCX-протоколы выбранного года в viewer backend."""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Укажите только каталог с операциями. Остальные параметры обычно менять не нужно.
OPERATIONS_DIR = Path(r"C:\Users\Angio_hir1\Desktop\Операции 2026")
YEAR = 2026
BACKEND_URL = "https://135.106.130.37/api"
BATCH_SIZE = 25
PAUSE_BETWEEN_BATCHES_SECONDS = 1.0
REQUEST_TIMEOUT_SECONDS = 30
MAX_RETRIES = 4

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hospital_agent.polling.protocols import (  # noqa: E402
    iter_protocol_files,
    parse_protocol,
    protocol_identity,
)


def request_json(path: str, *, method: str = "GET", payload: dict | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    request = Request(
        f"{BACKEND_URL.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
        raw = response.read()
        return json.loads(raw.decode("utf-8")) if raw else None


def existing_protocol_keys() -> set[str]:
    result: set[str] = set()
    page = 1
    while True:
        query = urlencode({"page": page, "page_size": 100, "scope": "all"})
        rows = request_json(f"/studies?{query}") or []
        for row in rows:
            result.add(protocol_identity(row))
        if len(rows) < 100:
            return result
        page += 1


def operation_year(payload: dict) -> int | None:
    try:
        return datetime.fromisoformat(
            str(payload["time_beginning"]).replace("Z", "+00:00")
        ).year
    except (KeyError, TypeError, ValueError):
        return None


def send_protocol(payload: dict) -> bool:
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            request_json("/studies", method="POST", payload=payload)
            return True
        except HTTPError as error:
            if error.code < 500 or attempt == MAX_RETRIES:
                print(f"ERROR study_id={payload.get('study_id')}: HTTP {error.code}")
                return False
        except (URLError, TimeoutError) as error:
            if attempt == MAX_RETRIES:
                print(f"ERROR study_id={payload.get('study_id')}: {error}")
                return False
        time.sleep(min(8, 2 ** (attempt - 1)))
    return False


def main() -> int:
    if not OPERATIONS_DIR.is_dir():
        print(f"Каталог не найден: {OPERATIONS_DIR}")
        return 2

    known = existing_protocol_keys()
    queued: list[dict] = []
    skipped_invalid = 0
    duplicate_keys = set(known)
    for path in iter_protocol_files([OPERATIONS_DIR]):
        payload = parse_protocol(path, "bulk-2026")
        if payload is None or operation_year(payload) != YEAR:
            skipped_invalid += 1
            continue
        identity = protocol_identity(payload)
        if identity in duplicate_keys:
            continue
        duplicate_keys.add(identity)
        queued.append(payload)

    print(
        f"Найдено новых протоколов: {len(queued)}; "
        f"уже на backend: {len(known)}; пропущено: {skipped_invalid}"
    )
    sent = failed = 0
    for index, payload in enumerate(queued, start=1):
        if send_protocol(payload):
            sent += 1
        else:
            failed += 1
        if index % BATCH_SIZE == 0:
            print(f"Обработано {index}/{len(queued)}, отправлено {sent}, ошибок {failed}")
            time.sleep(PAUSE_BETWEEN_BATCHES_SECONDS)

    print(f"Готово: отправлено {sent}, ошибок {failed}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
