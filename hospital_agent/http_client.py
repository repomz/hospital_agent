import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


LOGGER = logging.getLogger("hospital_agent.http")


class ViewerClient:
    """Минимальный HTTP-клиент для обмена hospital_agent с viewer backend."""

    def __init__(self, base_url: str, timeout_seconds: int):
        """Сохраняет базовый URL viewer и таймаут HTTP-запросов."""
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    def post_json(self, endpoint: str, payload: dict[str, Any]) -> bool:
        """Отправляет JSON payload POST-запросом на endpoint viewer."""
        url = self.base_url + endpoint
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                ok = 200 <= response.status < 300
                if not ok:
                    LOGGER.warning("POST %s returned HTTP %s", url, response.status)
                return ok
        except (HTTPError, URLError, TimeoutError) as exc:
            LOGGER.warning("POST %s failed: %s", url, exc)
            return False

    def get_json(self, endpoint: str) -> Any | None:
        """Получает JSON с endpoint viewer GET-запросом."""
        url = self.base_url + endpoint
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                if not (200 <= response.status < 300):
                    LOGGER.warning("GET %s returned HTTP %s", url, response.status)
                    return None
                raw = response.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            LOGGER.warning("GET %s failed: %s", url, exc)
            return None

        if not raw:
            return None
        try:
            return json.loads(raw.decode("utf-8"))
        except json.JSONDecodeError as exc:
            LOGGER.warning("GET %s returned invalid JSON: %s", url, exc)
            return None
