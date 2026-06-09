import logging
import os
import time
from pathlib import Path

import httplib2


LOGGER = logging.getLogger("hospital_agent.services.mapdr")


def format_size(size_bytes: float) -> str:
    """Convert bytes to human-readable format."""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.2f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.2f} TB"


def upload_file(
    url: str,
    path: Path,
    username: str | None = None,
    password: str | None = None,
) -> tuple[bool, int]:
    """Отправляет один DICOM-файл в Orthanc/MAPDR через REST API."""
    file_size = path.stat().st_size
    content = path.read_bytes()

    try:
        start_time = time.time()
        http = httplib2.Http()
        headers = {"content-type": "application/dicom"}
        if username and password:
            http.add_credentials(username, password)
        response, _ = http.request(url, "POST", body=content, headers=headers)
        duration = time.time() - start_time
        if response.status == 200:
            LOGGER.info("MAPDR upload ok: %s %s %.3fs", path, format_size(file_size), duration)
            return True, file_size
        LOGGER.warning("MAPDR upload failed: %s HTTP %s", path, response.status)
        return False, file_size
    except Exception:
        LOGGER.exception("MAPDR upload error: %s", path)
        return False, file_size


def upload_path_to_mapdr(
    hostname: str,
    port: int,
    path: str | Path,
    username: str | None = None,
    password: str | None = None,
) -> dict[str, float | int]:
    """Импортирует файл или папку DICOM в Orthanc/MAPDR через REST API."""
    url = f"http://{hostname}:{int(port)}/instances"
    source_path = Path(path)
    success = 0
    total_size = 0
    total_start_time = time.time()

    if source_path.is_file():
        ok, file_size = upload_file(url, source_path, username, password)
        total_size += file_size
        success += int(ok)
    else:
        for root, _dirs, files in os.walk(source_path):
            for filename in files:
                ok, file_size = upload_file(url, Path(root) / filename, username, password)
                total_size += file_size
                success += int(ok)

    total_duration = time.time() - total_start_time
    LOGGER.info(
        "MAPDR import finished: success=%s size=%s duration=%.3fs",
        success,
        format_size(total_size),
        total_duration,
    )
    return {"success": success, "total_size": total_size, "duration": total_duration}
