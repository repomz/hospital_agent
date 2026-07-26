import re
import tempfile
from datetime import datetime, timedelta, timezone
from typing import Any

from ..config import DICOM_IMPORT_TIMEOUT_SECONDS, AgentConfig, update_polling_state
from ..http_client import ViewerClient
from ..state import AgentState, save_state
from .operation_reports import generate_operations_report


GET_REPORT = "get_report"
FIND_STUDY = "find_study"
FIND_XA = "find_xa"
FIND_CT = "find_ct"
GET_XA = "get_xa"
GET_CT = "get_ct"
XA_POLLING_ON = "xa_polling_on"
XA_POLLING_OFF = "xa_polling_off"
CT_POLLING_ON = "ct_polling_on"
CT_POLLING_OFF = "ct_polling_off"


def execute_user_command(
    config: AgentConfig,
    command: str,
    payload: dict[str, Any],
    request_id: str,
    viewer: ViewerClient | None = None,
    state: AgentState | None = None,
) -> dict[str, Any] | None:
    """Выполняет команду backend по каноническому полю command."""
    if command in {FIND_XA, FIND_CT}:
        return find_dicom_studies(config, payload, modality=command.removeprefix("find_").upper())
    if command in {GET_XA, GET_CT}:
        if viewer is None or state is None:
            raise RuntimeError(f"{command} requires viewer and state")
        return get_dicom_study(
            config,
            payload,
            request_id,
            command.removeprefix("get_").upper(),
            viewer,
            state,
        )
    if command == FIND_STUDY:
        return find_operation_protocols(config, payload)
    if command == GET_REPORT:
        if viewer is None:
            raise RuntimeError("get_report requires viewer")
        return generate_report_from_payload(config, payload, viewer)
    if command in {XA_POLLING_ON, XA_POLLING_OFF, CT_POLLING_ON, CT_POLLING_OFF}:
        if state is None:
            raise RuntimeError(f"{command} requires state")
        modality = command.split("_", 1)[0]
        enabled = command.endswith("_on")
        update_polling_state(config, modality, enabled)
        with state.lock:
            if enabled:
                state.polling_enabled_at[modality.upper()] = datetime.now(timezone.utc).isoformat()
            else:
                state.polling_enabled_at.pop(modality.upper(), None)
            save_state(config.state_file, state)
        return {"modality": modality.upper(), "state": enabled}
    return None


def find_dicom_studies(
    config: AgentConfig,
    payload: dict[str, Any],
    modality: str,
) -> dict[str, Any]:
    """Ищет CT/XA исследования в локальном PACS по фамилии и периоду."""
    from .pacs import PACSClient
    from ..support.dicom import load_pacs_config

    patient = str(payload.get("patient") or payload.get("patient_name") or "").strip()
    if not patient:
        raise ValueError(f"find_{modality.lower()} requires patient")
    period = str(payload.get("period") or "today").strip().lower()
    date_value = str(payload.get("date") or "").strip() or None
    if period not in {"today", "yesterday", "week", "month"}:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", period):
            date_value = period
            period = ""
        else:
            raise ValueError("period must be today, yesterday, week, month, or YYYY-MM-DD")

    pacs_config = load_pacs_config(str(config.pacs_config_path))
    studies = PACSClient(pacs_config).find_studies(
        modality=modality,
        period=period or None,
        date_value=date_value,
        patient_name=patient,
    )
    return {
        "modality": modality,
        "patient": patient,
        "period": date_value or period,
        "studies": studies,
    }


def find_operation_protocols(
    config: AgentConfig,
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Ищет и возвращает стандартизованные протоколы операций по фамилии."""
    from ..polling.protocols import iter_protocol_files, parse_protocol

    patient = str(payload.get("patient") or payload.get("patient_name") or "").strip()
    if not patient:
        raise ValueError("find_study requires patient")
    needle = patient.casefold().replace("ё", "е")
    protocols = []
    for path in iter_protocol_files(config.study_polling.operations_dirs or []):
        parsed = parse_protocol(path, config.agent_id)
        if parsed is None:
            continue
        parsed_patient = str(parsed.get("patient", "")).casefold().replace("ё", "е")
        if needle not in parsed_patient:
            continue
        protocols.append(parsed)
    return {"patient": patient, "protocols": protocols}


def get_dicom_study(
    config: AgentConfig,
    payload: dict[str, Any],
    request_id: str,
    modality: str,
    viewer: ViewerClient,
    state: AgentState,
) -> dict[str, Any]:
    """Скачивает исследование напрямую по UID, строго загружает его и регистрирует."""
    from .pacs import PACSClient
    from .yandex import YandexStorage
    from ..support.dicom import load_pacs_config

    study_uid = str(payload.get("study_uid") or "").strip()
    if not study_uid:
        raise ValueError(f"get_{modality.lower()} requires study_uid")

    pacs_config = load_pacs_config(str(config.pacs_config_path))
    local_config = pacs_config.setdefault("local", {})
    original_output_dir = local_config.get("output_dir")
    try:
        with tempfile.TemporaryDirectory(prefix=f"hospital-agent-{request_id}-") as tmp_dir:
            local_config["output_dir"] = tmp_dir
            client = PACSClient(pacs_config)
            download = client.download_study(study_uid, lookup_metadata=False)
            if not download.get("ok"):
                raise RuntimeError(
                    "PACS C-GET incomplete: "
                    f"received={download.get('received_files', 0)} "
                    f"expected={download.get('expected_instances')} "
                    f"failed={download.get('failed_suboperations', 0)}"
                )

            storage = YandexStorage()
            storage.check_connection()
            uploaded = storage.upload_folder(
                download["study_dir"],
                download["yandex_folder"],
                client.retry_attempts,
                client.retry_delay,
            )
            if uploaded["failed_files"] or uploaded["uploaded_files"] != download["received_files"]:
                storage.delete_folder(uploaded["yandex_folder"])
                raise RuntimeError(
                    "Yandex upload incomplete: "
                    f"uploaded={uploaded['uploaded_files']} "
                    f"expected={download['received_files']} "
                    f"failed={len(uploaded['failed_files'])}"
                )

            delete_at = datetime.now(timezone.utc) + timedelta(days=3)
            with state.lock:
                state.yandex_cleanup = [
                    item
                    for item in state.yandex_cleanup
                    if item.get("folder") != uploaded["yandex_folder"]
                ]
                state.yandex_cleanup.append(
                    {
                        "folder": uploaded["yandex_folder"],
                        "delete_at": delete_at.isoformat(),
                    }
                )
                save_state(config.state_file, state)
            study_payload = build_modality_study_payload(modality, download, uploaded)
            if not viewer.post_json(
                f"/{modality.lower()}_studies",
                study_payload,
                timeout_seconds=DICOM_IMPORT_TIMEOUT_SECONDS,
            ):
                raise RuntimeError(f"backend rejected {modality} study metadata")
    finally:
        if original_output_dir is not None:
            local_config["output_dir"] = original_output_dir

    return {
        "modality": modality,
        "study_uid": study_uid,
        "patient": study_payload["patient"],
        "study_date": study_payload["study_date"],
        "dicom_link": uploaded["dicom_link"],
        "uploaded_files": uploaded["uploaded_files"],
        "uploaded_bytes": uploaded["uploaded_bytes"],
        "expires_at": delete_at.isoformat(),
    }


def build_modality_study_payload(
    modality: str,
    download: dict[str, Any],
    uploaded: dict[str, Any],
) -> dict[str, Any]:
    """Формирует контракт /ct_studies или /xa_studies."""
    patient_raw = str(download.get("patient") or "Unknown").replace("^", " ").strip()
    age_raw = str(download.get("age") or "")
    age_match = re.search(r"\d+", age_raw)
    study_date = str(download.get("study_date") or "")
    study_time = str(download.get("study_time") or "")
    return {
        "study_uid": download["study_uid"],
        "patient": patient_raw,
        "age": int(age_match.group(0)) if age_match else 0,
        "study_date": study_date,
        "study_time": study_time,
        "description": str(download.get("description") or modality),
        "modality": modality,
        "dicom_link": uploaded["dicom_link"],
        "dicom_files": uploaded["files"],
    }


def generate_report_from_payload(
    config: AgentConfig,
    payload: dict[str, Any],
    viewer: ViewerClient,
) -> dict[str, Any]:
    """Генерирует отчет за 1–4 суток и сохраняет JSON на backend."""
    period = int(payload.get("period", 1))
    if period < 1 or period > 4:
        raise ValueError("get_report period must be between 1 and 4 days")
    operations_dirs = config.study_polling.operations_dirs or []
    dir1 = str(operations_dirs[0]) if operations_dirs else ""
    dir2 = str(operations_dirs[1]) if len(operations_dirs) > 1 else ""
    now = datetime.now()
    duty_end = now.replace(hour=8, minute=0, second=0, microsecond=0)
    if now < duty_end:
        duty_end -= timedelta(days=1)
    result = generate_operations_report(
        period=period,
        time_value="08:00",
        dir1=dir1,
        dir2=dir2,
        plan_dir=str(config.plan_dir),
        report_dir=str(config.report_dir),
        end_period=duty_end,
    )
    report_payload = {
        "agent_id": int(config.agent_id),
        "report": result["report"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    if not viewer.post_json("/reports", report_payload):
        raise RuntimeError("backend rejected operations report")
    return {
        "report": result["report"],
        "text_report_file": result["text_report_file"],
    }
