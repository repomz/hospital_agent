"""Resumable, bounded first-run year reconciliation, independent of weekly polling."""

import csv
import logging
import os
import time
from datetime import datetime

from .polling.protocols import (
    PROTOCOL_SETTLE_SECONDS,
    iter_protocol_files,
    parse_protocol,
    protocol_identity,
    protocol_signature,
)
from .state import save_state

LOGGER = logging.getLogger("hospital_agent.reconciliation")
RECONCILIATION_REVISION = 2


def write_review(config, state):
    target = config.state_file.parent / "protocol_review.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_suffix(".csv.tmp")
    with temporary.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.writer(stream, delimiter=";")
        writer.writerow(["Файл", "Причина / действие"])
        for path, reason in sorted(state.reconciliation.get("review", {}).items()):
            # Avoid formulas when a report is opened in Excel.
            writer.writerow(
                ["'" + v if v.startswith(("=", "+", "-", "@")) else v for v in (path, reason)]
            )
    os.replace(temporary, target)


def reconcile_year(config, viewer, state, stopping=lambda: False):
    year = datetime.now().year
    with state.lock:
        if state.reconciliation.get("year") != year:
            state.reconciliation = {"year": year, "files": {}, "review": {}, "complete": False}
        sync = state.reconciliation
        if sync.get("revision") != RECONCILIATION_REVISION:
            # Revisit unresolved files once after the server matching fix. Keep
            # confirmed receipts and all independent XA/request state intact.
            for key in set(sync.get("review", {})) | set(sync.get("blocked", {})):
                sync["files"].pop(key, None)
            sync["revision"] = RECONCILIATION_REVISION
            sync["complete"] = False
    if sync.get("complete"):
        return True
    dirs = config.study_polling.operations_dirs or []
    if not dirs or any(not p.is_dir() for p in dirs):
        LOGGER.warning("Year reconciliation waiting: check operations_dir directories")
        return False
    files = iter_protocol_files(dirs)
    batch = []
    deferred = False

    def remember(path, signature, payload=None):
        key = str(path.resolve())
        sync["files"][key] = signature
        state.processed_protocols[key] = signature
        if payload:
            identity = protocol_identity(payload)
            if identity not in state.processed_protocol_keys:
                state.processed_protocol_keys.append(identity)

    def flush():
        if not batch:
            return True
        response = viewer.post_json_response(
            "/agent/protocols/reconcile",
            {
                "year": year,
                "items": [
                    {
                        k: p.get(k, 0 if k == "room" else "")
                        for k in ("study_id", "patient", "room", "time_beginning")
                    }
                    for _, _, p in batch
                ],
            },
        )
        statuses = response.get("statuses") if isinstance(response, dict) else None
        if (
            not isinstance(statuses, list)
            or len(statuses) != len(batch)
            or any(s not in {"present", "missing", "review"} for s in statuses)
        ):
            LOGGER.warning("Year reconciliation unavailable; no unverified protocols sent")
            for path, _, _ in batch:
                sync["review"][str(path.resolve())] = "Сверка недоступна; отправка отложена"
            save_state(config.state_file, state)
            write_review(config, state)
            return False
        success = True
        verified = set()
        for (path, signature, payload), status in zip(batch, statuses):
            key = str(path.resolve())
            if stopping():
                return False
            try:
                if protocol_signature(path) != signature:
                    success = False
                    continue
            except OSError:
                success = False
                continue
            if status == "review":
                sync["review"][key] = (
                    "Наличие точного протокола в базе не подтверждено: неоднозначное совпадение "
                    "или неизвестная операционная. Повторная отправка заблокирована; "
                    "это не означает отсутствия протокола в базе"
                )
                sync.setdefault("blocked", {})[key] = signature
                LOGGER.warning(
                    "Protocol requires manual server reconciliation; not sent: file=%s", path
                )
                remember(path, signature)
            elif status == "present":
                sync.get("blocked", {}).pop(key, None)
                sync["review"].pop(key, None)
                remember(path, signature, payload)
                verified.add(protocol_identity(payload))
                LOGGER.info(
                    "Protocol confirmed in server database; upload skipped: study_id=%s file=%s",
                    payload["study_id"],
                    path,
                )
            elif protocol_identity(payload) in verified:
                remember(path, signature, payload)
            elif viewer.post_json("/studies", payload):
                sync.get("blocked", {}).pop(key, None)
                sync["review"].pop(key, None)
                remember(path, signature, payload)
                verified.add(protocol_identity(payload))
                LOGGER.info(
                    "Missing protocol restored: study_id=%s file=%s", payload["study_id"], path
                )
                time.sleep(0.5)
            else:
                sync["review"][key] = "Не отправлен: ошибка сети/сервера; будет повторная попытка"
                success = False
        save_state(config.state_file, state)
        write_review(config, state)
        LOGGER.info(
            "Year reconciliation progress: checked=%s review=%s",
            len(sync["files"]),
            len(sync["review"]),
        )
        batch.clear()
        time.sleep(0.2)
        return success

    for path in files:
        if stopping():
            return False
        key = str(path.resolve())
        try:
            signature = protocol_signature(path)
            if sync["files"].get(key) == signature:
                continue
            if time.time() - path.stat().st_mtime < PROTOCOL_SETTLE_SECONDS:
                deferred = True
                continue
            payload = parse_protocol(path, config.agent_id)
            if protocol_signature(path) != signature:
                deferred = True
                continue
        except OSError as exc:
            sync["review"][key] = f"Файл недоступен: {exc}"
            deferred = True
            continue
        if payload is None:
            sync["review"][key] = "Повреждённый/неполный протокол; подробности в логах"
            remember(path, signature)
            continue
        date = datetime.fromisoformat(payload["time_beginning"].replace("Z", "+00:00")).astimezone()
        if date.year != year:
            remember(path, signature)
            continue
        if date.timestamp() > time.time():
            sync["review"][key] = "Дата операции в будущем; не отправлен"
            deferred = True
            continue
        batch.append((path, signature, payload))
        if len(batch) == 25 and not flush():
            return False
    if not flush():
        return False
    sync["complete"] = not deferred
    save_state(config.state_file, state)
    write_review(config, state)
    LOGGER.info(
        "Year reconciliation finished: year=%s complete=%s files=%s review=%s",
        year,
        sync["complete"],
        len(sync["files"]),
        len(sync["review"]),
    )
    return sync["complete"]
