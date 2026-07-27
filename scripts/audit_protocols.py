#!/usr/bin/env python3
"""Проверяет каталог протоколов теми же парсерами, что использует агент."""

import argparse
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hospital_agent.polling.protocols import (
    iter_protocol_files,
    parse_protocol,
    protocol_identity,
)
from hospital_agent.services.operation_reports import (
    _deduplicate_operations,
    analyze_operation_file,
    is_operation_docx_candidate,
)


def audit_protocols(root: Path, agent_id: str) -> tuple[Counter, list[Path]]:
    """Возвращает агрегаты проверки и список непригодных непустых DOCX."""
    all_files = [path for path in root.rglob("*") if path.is_file()]
    candidates = iter_protocol_files([root])
    counts = Counter(
        files_total=len(all_files),
        docx_total=sum(path.suffix.lower() == ".docx" for path in all_files),
        word_lock_files=sum(path.name.startswith("~$") for path in all_files),
        empty_docx=sum(
            path.suffix.lower() == ".docx"
            and not path.name.startswith("~$")
            and path.stat().st_size == 0
            for path in all_files
        ),
        candidates=len(candidates),
    )
    failures = []
    operations = []
    protocol_keys = []
    for path in candidates:
        operation = analyze_operation_file(path)
        protocol = parse_protocol(path, agent_id)
        if operation is None or protocol is None:
            failures.append(path)
            continue
        operations.append(operation)
        protocol_keys.append(protocol_identity(protocol))

    counts["report_valid"] = len(operations)
    counts["studies_valid"] = len(protocol_keys)
    counts["unique_report_operations"] = len(_deduplicate_operations(operations))
    counts["unique_study_payloads"] = len(set(protocol_keys))
    counts["duplicate_documents"] = len(protocol_keys) - len(set(protocol_keys))
    counts["filtered_files"] = sum(
        path.suffix.lower() == ".docx" and not is_operation_docx_candidate(path)
        for path in all_files
    )
    return counts, failures


def main() -> int:
    """Запускает аудит каталога из командной строки."""
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("--agent-id", default="2")
    args = parser.parse_args()
    root = args.directory.expanduser().resolve()
    if not root.is_dir():
        parser.error(f"not a directory: {root}")

    logging.disable(logging.CRITICAL)
    counts, failures = audit_protocols(root, args.agent_id)
    for key in sorted(counts):
        print(f"{key}: {counts[key]}")
    if failures:
        print("failed_files:")
        for path in failures:
            print(f"  {path.relative_to(root)}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
