import unittest
from unittest.mock import patch

from hospital_agent.http_client import ViewerClient


class ViewerClientTests(unittest.TestCase):
    def test_network_warnings_are_bounded_and_recovery_logged(self):
        client = ViewerClient("http://viewer.example", 5)
        with (
            patch("hospital_agent.http_client.time.monotonic", side_effect=[0, 60, 301]),
            patch("hospital_agent.http_client.LOGGER.warning") as warning,
            patch("hospital_agent.http_client.LOGGER.info") as info,
        ):
            for _ in range(3):
                client._failed("/agent_status", TimeoutError("timeout"))
            self.assertEqual(warning.call_count, 2)
            client._recovered("/agent_status")
            info.assert_called_once()
            self.assertEqual(info.call_args.args[-1], 3)
            self.assertFalse(client._failures)

    def test_certificate_and_http_errors_are_never_suppressed(self):
        import ssl
        from urllib.error import HTTPError, URLError

        client = ViewerClient("http://viewer.example", 5)
        with patch("hospital_agent.http_client.LOGGER.warning") as warning:
            for _ in range(3):
                client._failed("/studies", HTTPError("url", 401, "denied", {}, None))
                client._failed("/studies", URLError(ssl.SSLCertVerificationError("certificate")))
            self.assertEqual(warning.call_count, 6)

    def test_invalid_utf8_response_is_a_nonfatal_failure(self):
        client = ViewerClient("http://viewer.example", 5)
        with patch("hospital_agent.http_client.urlopen") as request:
            response = request.return_value.__enter__.return_value
            response.status = 200
            response.read.return_value = b"\xff"
            with patch("hospital_agent.http_client.LOGGER.warning") as warning:
                self.assertIsNone(client.get_json("/user_requests?agent_id=2"))
            warning.assert_called_once()

    def test_post_connection_reset_is_a_nonfatal_network_failure(self):
        client = ViewerClient("http://viewer.example", 5)

        with (
            patch(
                "hospital_agent.http_client.urlopen",
                side_effect=ConnectionResetError(10054, "connection reset"),
            ),
            patch("hospital_agent.http_client.LOGGER.warning") as warning,
        ):
            result = client.post_json("/agent_status", {"status": "well"})

        self.assertFalse(result)
        warning.assert_called_once()

    def test_get_connection_reset_is_a_nonfatal_network_failure(self):
        client = ViewerClient("http://viewer.example", 5)

        with (
            patch(
                "hospital_agent.http_client.urlopen",
                side_effect=ConnectionResetError(10054, "connection reset"),
            ),
            patch("hospital_agent.http_client.LOGGER.warning") as warning,
        ):
            result = client.get_json("/user_requests?agent_id=2")

        self.assertIsNone(result)
        warning.assert_called_once()


if __name__ == "__main__":
    unittest.main()
