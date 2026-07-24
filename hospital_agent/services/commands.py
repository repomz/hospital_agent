import tempfile
from pathlib import Path
from typing import Any

from ..config import AgentConfig
from ..http_client import ViewerClient
from .operation_reports import (
    DEFAULT_PERIOD,
    DEFAULT_PLAN_DIR,
    DEFAULT_REPORT_DIR,
    DEFAULT_TARGET_DIR_1,
    DEFAULT_TARGET_DIR_2,
    DEFAULT_TIME,
)
from ..support.dicom import load_pacs_config
from .operation_reports import generate_operations_report


SEND_STUDY_TO_YANDEX = "send_study_to_yandex"
SEND_DICOM_TO_MAPDR = "send_dicom_to_mapdr"
GENERATE_OPERATIONS_REPORT = "generate_operations_report"


def execute_user_command(
    config: AgentConfig,
    command: str,
    payload: dict[str, Any],
    request_id: str,
    viewer: ViewerClient | None = None,
) -> dict[str, Any] | None:
    """Выполняет поддержанную команду backend-запроса."""
    if command == SEND_STUDY_TO_YANDEX:
        return send_study_to_yandex(config, payload, request_id)
    if command == SEND_DICOM_TO_MAPDR:
        return send_path_to_mapdr(payload)
    if command == GENERATE_OPERATIONS_REPORT:
        return generate_report_from_payload(payload, viewer)
    return None


def send_study_to_yandex(
    config: AgentConfig,
    payload: dict[str, Any],
    request_id: str,
) -> dict[str, Any]:
    """Скачивает исследование из PACS во временную локальную папку и отправляет в Yandex."""
    from .pacs import PACSClient
    from .yandex import YandexStorage

    study_uid = payload.get("study_uid")
    if not study_uid:
        raise ValueError("send_study_to_yandex requires study_uid")

    pacs_config = load_pacs_config(str(config.pacs_config_path))
    local_config = pacs_config.setdefault("local", {})
    original_output_dir = local_config.get("output_dir")

    def run_with_download_dir(download_dir: Path) -> dict[str, Any]:
        local_config["output_dir"] = str(download_dir)
        client = PACSClient(pacs_config)
        download_result = client.download_study(str(study_uid))
        storage = YandexStorage()
        storage.check_connection()
        upload_result = storage.upload_folder(
            download_result["study_dir"],
            download_result["yandex_folder"],
            client.retry_attempts,
            client.retry_delay,
        )
        download_result.update(upload_result)
        return download_result

    try:
        with tempfile.TemporaryDirectory(prefix=f"hospital-agent-{request_id}-") as tmp_dir:
            download_dir = Path(tmp_dir)
            result = run_with_download_dir(download_dir)
    finally:
        if original_output_dir is not None:
            local_config["output_dir"] = original_output_dir

    result.update(
        {
            "study_uid": str(study_uid),
            "download_dir": str(download_dir),
            "temporary": True,
        }
    )
    return result


def send_path_to_mapdr(payload: dict[str, Any]) -> dict[str, Any]:
    """Отправляет локальный DICOM файл или папку в MAPDR/Orthanc."""
    from .mapdr import upload_path_to_mapdr

    dicom_path = payload.get("dicom_path")
    if not dicom_path:
        raise ValueError("send_dicom_to_mapdr requires dicom_path")
    hostname = str(payload.get("mapdr_host") or "localhost")
    port = int(payload.get("mapdr_port") or 8042)
    username = payload.get("mapdr_username")
    password = payload.get("mapdr_password")
    return upload_path_to_mapdr(hostname, port, dicom_path, username, password)


def generate_report_from_payload(
    payload: dict[str, Any],
    viewer: ViewerClient | None = None,
) -> dict[str, Any]:
    """Генерирует отчет; JSON вернется backend в результате user_request."""
    result = generate_operations_report(
        period=int(payload.get("period", DEFAULT_PERIOD)),
        time_value=str(payload.get("time", DEFAULT_TIME)),
        dir1=str(payload.get("dir1", payload.get("operations_dir1", DEFAULT_TARGET_DIR_1))),
        dir2=str(payload.get("dir2", payload.get("operations_dir2", DEFAULT_TARGET_DIR_2))),
        plan_dir=str(payload.get("plan_dir", payload.get("plandir", DEFAULT_PLAN_DIR))),
        report_dir=str(payload.get("report_dir", payload.get("reportdir", DEFAULT_REPORT_DIR))),
    )
    return {
        "report": result["report"],
        "json_report_file": result["json_report_file"],
        "text_report_file": result["text_report_file"],
    }
