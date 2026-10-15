import json
import logging
import ssl
import time
from http.client import HTTPException
from threading import Lock
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from .support.tls import verified_ssl_context

LOGGER = logging.getLogger("hospital_agent.http")


class ViewerClient:
    """Минимальный HTTP-клиент для обмена hospital_agent с viewer backend."""

    def __init__(self, base_url: str, timeout_seconds: int):
        """Сохраняет базовый URL viewer и таймаут HTTP-запросов."""
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self._failures: dict[str, tuple[float, int]] = {}
        self._failure_lock = Lock()

    def _failed(self, endpoint: str, exc: Exception) -> None:
        # Never hide HTTP authorization/validation or TLS certificate failures.
        reason = getattr(exc, "reason", exc)
        if isinstance(exc, (HTTPError, ValueError)) or isinstance(reason, ssl.SSLError):
            LOGGER.warning("Request %s failed: %s", endpoint, exc)
            return
        with self._failure_lock:
            now = time.monotonic()
            last, count = self._failures.get(endpoint, (now - 300, 0))
            count += 1
            if now - last >= 300:
                LOGGER.warning(
                    "Connection unavailable: endpoint=%s failures=%s error=%s", endpoint, count, exc
                )
                last = now
            else:
                LOGGER.debug("Connection retry failed: endpoint=%s error=%s", endpoint, exc)
            self._failures[endpoint] = (last, count)

    def _recovered(self, endpoint: str) -> None:
        with self._failure_lock:
            previous = self._failures.pop(endpoint, None)
        if previous:
            LOGGER.info(
                "Connection restored: endpoint=%s failed_attempts=%s", endpoint, previous[1]
            )

    def post_json(
        self,
        endpoint: str,
        payload: dict[str, Any],
        timeout_seconds: int | None = None,
    ) -> bool:
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
            with urlopen(
                request,
                timeout=timeout_seconds or self.timeout_seconds,
                context=verified_ssl_context(),
            ) as response:
                ok = 200 <= response.status < 300
                if not ok:
                    LOGGER.warning("POST %s returned HTTP %s", url, response.status)
                else:
                    self._recovered(endpoint)
                return ok
        except (OSError, HTTPException) as exc:
            self._failed(endpoint, exc)
            return False

    def get_json(self, endpoint: str) -> Any | None:
        """Получает JSON с endpoint viewer GET-запросом."""
        url = self.base_url + endpoint
        request = Request(url, method="GET", headers={"Accept": "application/json"})
        try:
            with urlopen(
                request,
                timeout=self.timeout_seconds,
                context=verified_ssl_context(),
            ) as response:
                if not (200 <= response.status < 300):
                    LOGGER.warning("GET %s returned HTTP %s", url, response.status)
                    return None
                raw = response.read()
        except (OSError, HTTPException) as exc:
            self._failed(endpoint, exc)
            return None

        if not raw:
            self._recovered(endpoint)
            return None
        try:
            result = json.loads(raw.decode("utf-8"))
            self._recovered(endpoint)
            return result
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            LOGGER.warning("GET %s returned invalid JSON: %s", url, exc)
            return None

    def post_json_response(self, endpoint: str, payload: dict[str, Any]) -> Any | None:
        request = Request(
            self.base_url + endpoint,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(
                request, timeout=self.timeout_seconds, context=verified_ssl_context()
            ) as response:
                result = json.load(response)
                self._recovered(endpoint)
                return result
        except (OSError, HTTPException, ValueError) as exc:
            self._failed(endpoint, exc)
            return None
