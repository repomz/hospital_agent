"""Explicit administrator requests for original protocol files (not parsed studies)."""

import base64
from pathlib import Path
from typing import Any

from ..config import AgentConfig
from ..http_client import ViewerClient

MAX_PROTOCOL_BYTES = 20 * 1024 * 1024


def send_protocol_file(
    config: AgentConfig,
    payload: dict[str, Any],
    request_id: str,
    viewer: ViewerClient,
) -> dict[str, Any]:
    path = Path(str(payload.get("path", "")))
    if not path.is_absolute():
        raise ValueError("Protocol path must be absolute")
    path = path.resolve(strict=True)
    roots = [Path(root).resolve() for root in config.study_polling.operations_dirs or []]
    if not any(path.is_relative_to(root) for root in roots):
        raise ValueError("Protocol must be inside configured operations directories")
    if path.suffix.lower() not in {".docx", ".doc", ".rtf", ".pdf"} or not path.is_file():
        raise ValueError("Only DOCX, DOC, RTF and PDF protocol files are allowed")
    with path.open("rb") as source:
        content = source.read(MAX_PROTOCOL_BYTES + 1)
    if len(content) > MAX_PROTOCOL_BYTES:
        raise ValueError("Protocol file exceeds 20 MiB")
    # Empty/broken documents are intentionally allowed: they need investigation too.
    result = viewer.post_json_response(
        f"/agent_files/{request_id}",
        {"token": payload.get("upload_token"), "content": base64.b64encode(content).decode()},
        timeout_seconds=180,
    )
    if not isinstance(result, dict) or not result.get("id"):
        raise RuntimeError("Could not save original protocol to administrator drive")
    return {"file_id": result["id"], "name": result["name"], "size_bytes": len(content)}
