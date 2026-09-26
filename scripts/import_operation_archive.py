#!/usr/bin/env python3
"""Проверяет ZIP архива протоколов и безопасно импортирует метаданные в Viewer.

Оригинальные DOC/DOCX остаются в ZIP. Для поиска и просмотра в PostgreSQL
передаются те же поля StudyRequest, что формирует текущий hospital agent.
"""

from __future__ import annotations

import argparse
import hashlib
import http.client
import json
import logging
import os
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


LOGGER = logging.getLogger("operation-archive-import")


def archive_study_id(info: zipfile.ZipInfo) -> str:
    raw = f"{info.filename}|{info.CRC}|{info.file_size}".encode("utf-8")
    return "archive-" + hashlib.sha256(raw).hexdigest()[:24]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def convert_legacy_doc(source: Path, destination: Path) -> bool:
    """Конвертирует редкие старые .doc, если ОС предоставляет конвертер."""
    textutil = shutil.which("textutil")
    soffice = shutil.which("soffice") or shutil.which("libreoffice")
    try:
        if textutil:
            subprocess.run(
                [textutil, "-convert", "docx", "-output", str(destination), str(source)],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return destination.is_file()
        if soffice:
            subprocess.run(
                [
                    soffice,
                    "--headless",
                    "--convert-to",
                    "docx",
                    "--outdir",
                    str(destination.parent),
                    str(source),
                ],
                check=True,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            converted = destination.parent / (source.stem + ".docx")
            if converted != destination and converted.is_file():
                converted.replace(destination)
            return destination.is_file()
    except (OSError, subprocess.CalledProcessError):
        return False
    return False


def build_index(archive: Path, output: Path, report_path: Path) -> dict[str, Any]:
    from hospital_agent.polling.protocols import parse_protocol, protocol_identity

    counters: Counter[str] = Counter()
    per_year: dict[str, Counter[str]] = {}
    failures: list[dict[str, str]] = []
    identities: set[str] = set()

    output.parent.mkdir(parents=True, exist_ok=True)
    with (
        zipfile.ZipFile(archive) as source,
        output.open("w", encoding="utf-8") as destination,
        tempfile.TemporaryDirectory(prefix="viewer-archive-") as temporary,
    ):
        test_path = Path(temporary) / "protocol.docx"
        legacy_path = Path(temporary) / "protocol.doc"
        for info in source.infolist():
            if info.is_dir():
                continue
            counters["files"] += 1
            suffix = Path(info.filename).suffix.casefold()
            parts = info.filename.split("/")
            year = parts[1] if len(parts) > 2 and parts[1].isdigit() else "unknown"
            year_counts = per_year.setdefault(year, Counter())
            year_counts["files"] += 1
            if suffix not in (".doc", ".docx"):
                counters["unsupported_skipped"] += 1
                year_counts["unsupported_skipped"] += 1
                continue

            try:
                if suffix == ".doc":
                    legacy_path.write_bytes(source.read(info))
                    test_path.unlink(missing_ok=True)
                    if not convert_legacy_doc(legacy_path, test_path):
                        counters["legacy_doc_skipped"] += 1
                        year_counts["legacy_doc_skipped"] += 1
                        continue
                    counters["legacy_doc_converted"] += 1
                    year_counts["legacy_doc_converted"] += 1
                else:
                    test_path.write_bytes(source.read(info))
                payload = parse_protocol(
                    test_path,
                    "archive",
                    fallback_study_id=archive_study_id(info),
                )
            except Exception as exc:  # one broken document must not stop 20k files
                payload = None
                reason = f"{type(exc).__name__}: {exc}"
            else:
                reason = "required fields were not recognized"

            if payload is None:
                counters["failed"] += 1
                year_counts["failed"] += 1
                if len(failures) < 500:
                    failures.append({"source_document": info.filename, "reason": reason})
                continue

            operation_year = str(payload["time_beginning"])[:4]
            if year != "unknown" and operation_year != year:
                counters["year_mismatch"] += 1
                year_counts["year_mismatch"] += 1
                if not 2015 <= int(operation_year) <= 2025:
                    counters["out_of_range_skipped"] += 1
                    year_counts["out_of_range_skipped"] += 1
                    failures.append(
                        {
                            "source_document": info.filename,
                            "reason": f"folder year {year}, operation year {operation_year}",
                        }
                    )
                    continue

            identity = protocol_identity(payload)
            if identity in identities:
                counters["duplicates_skipped"] += 1
                year_counts["duplicates_skipped"] += 1
                continue
            identities.add(identity)
            destination.write(
                json.dumps(
                    {
                        "source_document": info.filename,
                        "payload": payload,
                    },
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                + "\n"
            )
            counters["indexed"] += 1
            year_counts["indexed"] += 1

    report = {
        "schema_version": 1,
        "archive": str(archive.resolve()),
        "archive_sha256": sha256_file(archive),
        "index": str(output.resolve()),
        "counts": dict(counters),
        "years": {year: dict(values) for year, values in sorted(per_year.items())},
        "failures": failures,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def read_index(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as source:
        for line_number, line in enumerate(source, 1):
            if not line.strip():
                continue
            document = json.loads(line)
            payload = document.get("payload")
            if not isinstance(payload, dict):
                raise ValueError(f"invalid payload at line {line_number}")
            records.append(document)
    return records


def backend_connection(base_url: str) -> tuple[http.client.HTTPConnection, str]:
    parsed = urlsplit(base_url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError("backend URL must use http or https")
    if parsed.scheme == "https":
        connection: http.client.HTTPConnection = http.client.HTTPSConnection(
            parsed.hostname,
            parsed.port or 443,
            timeout=30,
            context=ssl.create_default_context(),
        )
    else:
        connection = http.client.HTTPConnection(parsed.hostname, parsed.port or 80, timeout=30)
    return connection, parsed.path.rstrip("/") + "/studies"


def upload_partition(
    documents: list[dict[str, Any]],
    backend_url: str,
    progress: Callable[[bool], None],
) -> list[dict[str, str]]:
    failures: list[dict[str, str]] = []
    connection, endpoint = backend_connection(backend_url)
    try:
        for document in documents:
            source_document = str(document.get("source_document") or "")
            body = json.dumps(document["payload"], ensure_ascii=False).encode("utf-8")
            reason = ""
            succeeded = False
            for attempt in range(1, 5):
                try:
                    connection.request(
                        "POST",
                        endpoint,
                        body=body,
                        headers={"Content-Type": "application/json; charset=utf-8"},
                    )
                    response = connection.getresponse()
                    response_body = response.read()
                    succeeded = 200 <= response.status < 300
                    if succeeded:
                        break
                    reason = (
                        f"HTTP {response.status}: {response_body[:500].decode('utf-8', 'replace')}"
                    )
                    if 400 <= response.status < 500:
                        break
                except (OSError, http.client.HTTPException) as exc:
                    reason = str(exc)
                    connection.close()
                    connection, endpoint = backend_connection(backend_url)
                if attempt < 4:
                    time.sleep(min(2 ** (attempt - 1), 8))
            if not succeeded:
                failures.append({"source_document": source_document, "reason": reason})
            progress(succeeded)
    finally:
        connection.close()
    return failures


def upload_index(index: Path, backend_url: str, workers: int, failures_path: Path) -> bool:
    documents = read_index(index)
    uploaded = 0
    processed = 0
    failures: list[dict[str, str]] = []
    lock = threading.Lock()

    def progress(succeeded: bool) -> None:
        nonlocal uploaded, processed
        with lock:
            processed += 1
            uploaded += int(succeeded)
            if processed % 250 == 0 or processed == len(documents):
                LOGGER.info(
                    "Archive upload: processed=%d/%d uploaded=%d failed=%d",
                    processed,
                    len(documents),
                    uploaded,
                    processed - uploaded,
                )

    partitions = [documents[index::workers] for index in range(workers)]
    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = [
            executor.submit(upload_partition, partition, backend_url, progress)
            for partition in partitions
        ]
        for future in as_completed(futures):
            failures.extend(future.result())
    failures_path.write_text(
        json.dumps(failures, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return not failures


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--index", type=Path, default=Path("archive_operations.jsonl"))
    parser.add_argument("--report", type=Path, default=Path("archive_import_report.json"))
    parser.add_argument("--build", action="store_true", help="parse ZIP and create JSONL index")
    parser.add_argument("--upload", action="store_true", help="upload JSONL records to backend")
    parser.add_argument("--backend-url", default=os.environ.get("BACKEND_URL", ""))
    parser.add_argument("--workers", type=int, default=2)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not args.build and not args.upload:
        raise SystemExit("specify --build and/or --upload")
    if args.workers < 1 or args.workers > 4:
        raise SystemExit("--workers must be between 1 and 4")
    if args.build:
        report = build_index(args.archive, args.index, args.report)
        LOGGER.info("Archive index created: %s", json.dumps(report["counts"], ensure_ascii=False))
    if args.upload:
        if not args.backend_url:
            raise SystemExit("--backend-url or BACKEND_URL is required for upload")
        failures_path = args.report.with_name("archive_upload_failures.json")
        if not upload_index(args.index, args.backend_url, args.workers, failures_path):
            LOGGER.error("Archive upload has failures: %s", failures_path)
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
