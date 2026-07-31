import logging
from datetime import datetime, time, timedelta, timezone
from typing import Callable

from ..config import AgentConfig, PollingConfig, update_polling_state
from ..http_client import ViewerClient
from ..services.commands import get_dicom_study
from ..state import AgentState, save_state


LOGGER = logging.getLogger("hospital_agent.pacs")


def _study_datetime(study: dict[str, object]) -> datetime | None:
    """Собирает локальные дату и время исследования из ответа PACS."""
    date_value = str(study.get("date") or "")
    time_value = str(study.get("time") or "").split(".", 1)[0]
    if not date_value or not time_value:
        return None
    if len(time_value) < 6:
        time_value = time_value.ljust(6, "0")
    try:
        return datetime.strptime(date_value + time_value[:6], "%Y%m%d%H%M%S").astimezone()
    except ValueError:
        return None


def _duty_end(enabled_at: datetime) -> datetime:
    """Возвращает ближайшие 08:00 после момента включения polling."""
    local_enabled = enabled_at.astimezone()
    cutoff = datetime.combine(local_enabled.date(), time(8, 0), local_enabled.tzinfo)
    if local_enabled >= cutoff:
        cutoff += timedelta(days=1)
    return cutoff


def run_modality_polling(
    config: AgentConfig,
    polling: PollingConfig,
    modality: str,
    viewer: ViewerClient,
    state: AgentState,
    stop_requested: Callable[[], bool] | None = None,
) -> int:
    """Передает CT за дежурство, а XA — автоматически за текущую неделю."""
    if not polling.state or (stop_requested is not None and stop_requested()):
        return 0
    modality = modality.upper()
    now = datetime.now(timezone.utc)
    local_now = now.astimezone()
    if modality == "XA":
        local_start = local_now - timedelta(days=local_now.weekday())
        local_start = local_start.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        enabled_raw = state.polling_enabled_at.get(modality)
        if enabled_raw:
            enabled_at = datetime.fromisoformat(enabled_raw)
        else:
            enabled_at = now
            with state.lock:
                state.polling_enabled_at[modality] = enabled_at.isoformat()
                save_state(config.state_file, state)
        if local_now >= _duty_end(enabled_at):
            update_polling_state(config, modality.lower(), False)
            with state.lock:
                state.polling_enabled_at.pop(modality, None)
                save_state(config.state_file, state)
            LOGGER.info("%s polling automatically disabled at duty end", modality)
            return 0
        local_start = enabled_at.astimezone()

    from ..services.pacs import PACSClient
    from ..support.dicom import load_pacs_config

    date_range = (
        local_start.strftime("%Y%m%d")
        if local_start.date() == local_now.date()
        else f"{local_start:%Y%m%d}-{local_now:%Y%m%d}"
    )
    client = PACSClient(load_pacs_config(str(config.pacs_config_path)))
    studies = client.find_studies(modality=modality, date_range=date_range)
    processed = state.processed_modality_studies.setdefault(modality, [])
    sent = 0
    for study in studies:
        if not polling.state or (stop_requested is not None and stop_requested()):
            break
        study_uid = str(study.get("uid") or "")
        study_datetime = _study_datetime(study)
        if not study_uid or study_uid in processed:
            continue
        if study_datetime is None:
            LOGGER.warning(
                "%s study skipped because PACS returned no valid date/time: %s",
                modality,
                study_uid,
            )
            continue
        if study_datetime < local_start or study_datetime > local_now:
            continue
        try:
            get_dicom_study(
                config,
                {"study_uid": study_uid},
                f"poll-{modality.lower()}-{study_uid}",
                modality,
                viewer,
                state,
            )
        except Exception as exc:
            LOGGER.warning("%s study %s failed: %s", modality, study_uid, exc)
            continue
        with state.lock:
            if study_uid not in processed:
                processed.append(study_uid)
            del processed[:-2000]
            save_state(config.state_file, state)
        sent += 1
        LOGGER.info("%s study sent: %s", modality, study_uid)
    return sent


def disable_expired_polling(config: AgentConfig, state: AgentState) -> int:
    """Выключает дежурный CT polling; недельный XA работает постоянно."""
    disabled = 0
    now = datetime.now(timezone.utc)
    for modality in ("CT",):
        polling = getattr(config, f"{modality.lower()}_polling")
        enabled_raw = state.polling_enabled_at.get(modality)
        if not polling.state or not enabled_raw:
            continue
        try:
            enabled_at = datetime.fromisoformat(enabled_raw)
        except ValueError:
            continue
        if now.astimezone() < _duty_end(enabled_at):
            continue
        update_polling_state(config, modality.lower(), False)
        with state.lock:
            state.polling_enabled_at.pop(modality, None)
        disabled += 1
        LOGGER.info("%s polling automatically disabled at duty end", modality)
    if disabled:
        save_state(config.state_file, state)
    return disabled


def cleanup_expired_yandex_studies(config: AgentConfig, state: AgentState) -> int:
    """Удаляет исследования Yandex после трех суток хранения."""
    if not state.yandex_cleanup:
        return 0
    from ..services.yandex import YandexStorage

    now = datetime.now(timezone.utc)
    storage = None
    remaining = []
    deleted = 0
    for item in state.yandex_cleanup:
        try:
            delete_at = datetime.fromisoformat(str(item["delete_at"]))
        except (KeyError, ValueError):
            continue
        if delete_at > now:
            remaining.append(item)
            continue
        try:
            storage = storage or YandexStorage()
            deleted += storage.delete_folder(str(item["folder"]))
        except Exception as exc:
            LOGGER.warning("Yandex cleanup failed for %s: %s", item.get("folder"), exc)
            remaining.append(item)
    with state.lock:
        state.yandex_cleanup = remaining
        save_state(config.state_file, state)
    if deleted:
        LOGGER.info("Yandex cleanup removed %s objects", deleted)
    return deleted
