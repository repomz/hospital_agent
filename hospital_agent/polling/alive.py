import logging

from .. import __version__
from ..config import AgentConfig
from ..http_client import ViewerClient

LOGGER = logging.getLogger("hospital_agent.alive")


def build_alive_payload(config: AgentConfig, errors: list[str] | None = None) -> dict[str, object]:
    """Формирует heartbeat payload для viewer /agent_alive."""
    return {
        "agent_id": config.agent_id,
        "status": "well",
        "configuration": {
            "version": __version__,
            "description": config.description,
            "log_dir": str(config.log_dir),
            "state_file": str(config.state_file),
            "pacs_config_path": str(config.pacs_config_path),
            "heartbeat_interval_min": config.alive_polling_interval_min,
            **{
                name: {
                    "state": polling.state,
                    "interval_min": polling.interval_min,
                    **(
                        {"operations_dir": [str(path) for path in polling.operations_dirs or []]}
                        if name == "study_polling"
                        else {}
                    ),
                }
                for name, polling in (
                    ("study_polling", config.study_polling),
                    ("xa_polling", config.xa_polling),
                    ("ct_polling", config.ct_polling),
                    ("user_requests_polling", config.user_requests_polling),
                )
            },
        },
    }


def send_alive(config: AgentConfig, viewer: ViewerClient) -> bool:
    """Отправляет heartbeat агента на viewer /agent_alive."""
    ok = viewer.post_json("/agent_status", build_alive_payload(config))
    if ok:
        LOGGER.info("Heartbeat sent: endpoint=/agent_status status=well")
    return ok
