"""Read-only parser audit. Prints anonymized examples; never sends to the backend."""

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from hospital_agent.polling.protocols import parse_protocol


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    if not args.directory.is_dir():
        parser.error("directory does not exist")
    # Avoid exposing patient filenames through parser diagnostic logging.
    logging.disable(logging.CRITICAL)
    counts, months, types = Counter(), Counter(), Counter()
    examples = {}
    for path in sorted(args.directory.rglob("*.docx")):
        counts["files"] += 1
        if path.name.startswith("~$"):
            counts["word_lock_files"] += 1
            continue
        try:
            payload = parse_protocol(path, "1")
        except Exception as error:
            counts["error_" + type(error).__name__] += 1
            continue
        if payload is None:
            counts["not_parsed"] += 1
            continue
        counts["parsed"] += 1
        month = payload["time_beginning"][:7]
        months[month] += 1
        types[payload["study_type"]] += 1
        if not payload["conclusion"]:
            counts["empty_conclusion"] += 1
        option_values = set(filter(None, payload["options"].split(",")))
        if "ivus" in option_values:
            counts["ivus"] += 1
        for option in ("vabk", "ekmo"):
            if option in option_values:
                counts[option] += 1
        # One example per clinical category and month, including IVUS separately.
        key = f"{month}/{payload['study_type']}/{','.join(sorted(option_values))}"
        if key not in examples:
            payload["patient"] = "Иванов Иван Иванович"
            examples[key] = payload
    print(
        json.dumps(
            {"counts": counts, "months": months, "types": types, "examples": examples},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
