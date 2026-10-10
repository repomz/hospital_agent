#!/usr/bin/env python3
"""Read-only reconciliation of an archive JSONL with the deployed PostgreSQL DB."""
import argparse
import datetime as dt
import json
import subprocess
from collections import Counter
from pathlib import Path


def key(record):
    when = dt.datetime.fromisoformat(record["time_beginning"].replace("Z", "+00:00"))
    if when.tzinfo is None:
        when = when.replace(tzinfo=dt.timezone.utc)
    return (record["patient"].strip().lower(), when.astimezone(dt.timezone.utc).isoformat(), record["name_operation"].strip().lower())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("index", type=Path)
    parser.add_argument("report", type=Path)
    args = parser.parse_args()
    sql = "SELECT row_to_json(s) FROM studies s WHERE NOT deleted AND time_beginning >= '2015-01-01' AND time_beginning < '2026-01-01'"
    raw = subprocess.check_output(["docker", "exec", "viewer-postgres-1", "psql", "-U", "viewer", "-d", "viewer", "-Atc", sql], text=True)
    records = [json.loads(line) for line in raw.splitlines() if line.strip()]
    database = {key(record): record for record in records}
    mismatches = []
    checked = 0
    for line in args.index.read_text(encoding="utf-8").splitlines():
        document = json.loads(line)
        expected = document["payload"]
        actual = database.get(key(expected))
        checked += 1
        fields = []
        if actual is None:
            fields = ["missing"]
        else:
            for name in ("study_id", "patient", "department", "name_operation", "options", "description", "recommendation"):
                if actual.get(name) != expected.get(name):
                    fields.append(name)
            for name in ("study_type", "surgeon"):
                # StudyRequest.Validate canonicalizes these fields to lower case.
                if actual.get(name) != str(expected.get(name, "")).strip().lower():
                    fields.append(name)
            for name in ("age", "time_duration"):
                # Domain maps an unknown zero value to SQL NULL.
                if (actual.get(name) or 0) != (expected.get(name) or 0):
                    fields.append(name)
            if actual.get("descr_operation", "") != expected.get("conclusion", ""):
                fields.append("conclusion")
            if actual.get("room", 0) != expected.get("room", 0):
                fields.append("room")
            if (actual.get("birth_date") or "")[:10] != (expected.get("birth_date") or "")[:10]:
                fields.append("birth_date")
        if fields:
            mismatches.append({"source_document": document["source_document"], "fields": fields})
    report = {"checked": checked, "database_archive_rows": len(records), "unique_database_records": len(database), "mismatches": mismatches,
              "years": dict(sorted(Counter(r["time_beginning"][:4] for r in records).items()))}
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({**report, "mismatches": len(mismatches)}, ensure_ascii=False))
    return int(bool(mismatches) or len(records) != checked)


if __name__ == "__main__":
    raise SystemExit(main())
