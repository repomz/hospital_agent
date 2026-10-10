#!/usr/bin/env python3
"""Create a local review list and ZIP of excluded originals, without extracting paths."""
import argparse
import json
import zipfile
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    report = json.loads(args.report.read_text(encoding="utf-8"))
    failures = report["failures"]
    reasons = {
        "required fields were not recognized": "Не распознаны обязательные поля протокола — требуется ручная проверка.",
        "urgency recognized instead of operation name; manual review required": "Вместо названия операции указана только срочность (плановая/экстренная).",
        "duplicate protocol identity (not uploaded twice)": "Дубликат уже включённого протокола — повторно не загружался.",
        "operation year 2014 outside 2015-2025": "Операция датирована 2014 годом, вне запрошенного периода 2015–2025.",
    }
    prefix = args.report.with_suffix("")
    with prefix.with_suffix(".txt").open("w", encoding="utf-8") as out:
        out.write("Файлы, не включённые в импорт. Дубликаты также перечислены с отдельной причиной.\n\n")
        for index, entry in enumerate(failures, 1):
            out.write(f"{index}. {entry['source_document']}\n   {reasons.get(entry['reason'], entry['reason'])}\n\n")
    with zipfile.ZipFile(report["archive"]) as source, zipfile.ZipFile(str(prefix) + "_manual_review.zip", "w", zipfile.ZIP_DEFLATED) as target:
        included = set()
        for entry in failures:
            name = entry["source_document"]
            if name not in included and not entry["reason"].startswith("duplicate"):
                target.writestr(name, source.read(name))
                included.add(name)
        target.writestr("review_reasons.json", json.dumps(failures, ensure_ascii=False, indent=2))
    print(f"Exclusions: {len(failures)}; originals for manual review: {len(included)}")


if __name__ == "__main__":
    main()
