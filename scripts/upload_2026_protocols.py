#!/usr/bin/env python3
"""Безопасно загружает DOCX-протоколы выбранного года в viewer backend."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# Укажите оба каталога параметрами --operations-dir; допустимо повторять параметр.
YEAR = 2026
BACKEND_URL = os.environ.get("VIEWER_BACKEND_URL", "https://angio.su/api")
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
from hospital_agent.support.tls import verified_ssl_context  # noqa: E402


def request_json(path: str, *, method: str = "GET", payload: dict | None = None):
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload else None
    request = Request(
        f"{BACKEND_URL.rstrip('/')}{path}",
        data=body,
        method=method,
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(
        request,
        timeout=REQUEST_TIMEOUT_SECONDS,
        context=verified_ssl_context(),
    ) as response:
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
        return datetime.fromisoformat(str(payload["time_beginning"]).replace("Z", "+00:00")).year
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


def main(argv: list[str] | None = None) -> int:
    global BACKEND_URL
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--operations-dir",
        action="append",
        type=Path,
        required=True,
        help="Каталог с протоколами DOCX (параметр можно указать несколько раз)",
    )
    parser.add_argument(
        "--backend-url",
        default=BACKEND_URL,
        help="API backend (по умолчанию https://angio.su/api)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Только посчитать файлы и дубликаты, ничего не отправлять",
    )
    args = parser.parse_args(argv)
    BACKEND_URL = args.backend_url
    missing = [path for path in args.operations_dir if not path.is_dir()]
    if missing:
        for path in missing:
            print(f"Каталог не найден: {path}")
        return 2

    known = existing_protocol_keys()
    queued: list[dict] = []
    skipped_invalid = 0
    skipped_duplicates = 0
    duplicate_keys = set(known)
    types: Counter[str] = Counter()
    for path in iter_protocol_files(args.operations_dir):
        payload = parse_protocol(path, "bulk-2026")
        if payload is None or operation_year(payload) != YEAR:
            skipped_invalid += 1
            continue
        identity = protocol_identity(payload)
        if identity in duplicate_keys:
            skipped_duplicates += 1
            continue
        duplicate_keys.add(identity)
        queued.append(payload)
        types[str(payload.get("study_type") or "не указано")] += 1

    print(
        f"Найдено новых протоколов: {len(queued)}; "
        f"уже на backend: {len(known)}; дубликаты: {skipped_duplicates}; "
        f"непрочитано/не 2026: {skipped_invalid}"
    )
    print(f"Распределение по типам: {dict(sorted(types.items()))}")
    if args.dry_run:
        return 0
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
