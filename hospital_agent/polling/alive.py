import logging

from ..config import AgentConfig
from ..http_client import ViewerClient


LOGGER = logging.getLogger("hospital_agent.alive")


def build_alive_payload(config: AgentConfig, errors: list[str] | None = None) -> dict[str, object]:
    """Формирует heartbeat payload для viewer /agent_alive."""
    return {
        "agent_id": config.agent_id,
        "status": "well",
        # "sent_at": datetime.now(timezone.utc).isoformat(),
        # "errors": errors or [],
    }


def send_alive(config: AgentConfig, viewer: ViewerClient) -> bool:
    """Отправляет heartbeat агента на viewer /agent_alive."""
    ok = viewer.post_json("/agent_status", build_alive_payload(config))
    if ok:
        LOGGER.info("Agent alive webhook sent")
    return ok
