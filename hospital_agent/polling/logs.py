import hashlib
import logging
from datetime import datetime, timedelta
from pathlib import Path

from ..config import AgentConfig
from ..http_client import ViewerClient
from ..state import AgentState, save_state

LOGGER = logging.getLogger("hospital_agent.logs")
RETENTION_DAYS = 7


def _completed_log_hours(log_dir: Path, now: datetime) -> dict[datetime, str]:
    """Reads daily files and groups complete records by their local wall-clock hour."""
    current_hour = now.replace(minute=0, second=0, microsecond=0)
    earliest_day = (current_hour - timedelta(days=RETENTION_DAYS - 1)).date()
    grouped: dict[datetime, list[str]] = {}

    for day_offset in range(RETENTION_DAYS):
        day = earliest_day + timedelta(days=day_offset)
        path = log_dir / f"{day.isoformat()}.log"
        if not path.is_file():
            continue
        active_hour: datetime | None = None
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            LOGGER.exception("Cannot read agent log file: path=%s", path)
            continue
        for line in lines:
            try:
                parsed_hour = datetime.strptime(line[:13], "%Y-%m-%d %H")
                active_hour = parsed_hour if parsed_hour < current_hour else None
            except ValueError:
                # Traceback and multi-line payload lines belong to the preceding record.
                pass
            if active_hour is not None:
                grouped.setdefault(active_hour, []).append(line)

    return {hour: "\n".join(lines).strip() for hour, lines in grouped.items() if lines}


def upload_agent_logs(
    config: AgentConfig,
    viewer: ViewerClient,
    state: AgentState,
    now: datetime | None = None,
) -> int:
    """Uploads each completed hour once and retries safely after connection failures."""
    now = now or datetime.now().astimezone()
    chunks = _completed_log_hours(config.log_dir, now.replace(tzinfo=None))
    uploaded = 0
    cutoff = now.replace(tzinfo=None) - timedelta(days=RETENTION_DAYS)

    with state.lock:
        state.uploaded_log_hours = {
            key: value
            for key, value in state.uploaded_log_hours.items()
            if _parse_hour_key(key) >= cutoff
        }

    for hour in sorted(chunks):
        content = chunks[hour]
        key = hour.isoformat(timespec="hours")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        if state.uploaded_log_hours.get(key) == digest:
            continue
        local_start = hour.astimezone()
        ok = viewer.post_json(
            "/agent_logs",
            {
                "agent_id": int(config.agent_id),
                "period_start": local_start.isoformat(),
                "period_end": (local_start + timedelta(hours=1)).isoformat(),
                "content": content,
            },
        )
        if not ok:
            continue
        with state.lock:
            state.uploaded_log_hours[key] = digest
        uploaded += 1

    save_state(config.state_file, state)
    if uploaded:
        LOGGER.info("Agent logs uploaded: hourly_chunks=%s", uploaded)
    return uploaded


def _parse_hour_key(value: str) -> datetime:
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return datetime.min
