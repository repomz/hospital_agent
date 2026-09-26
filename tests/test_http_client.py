import unittest
from unittest.mock import patch

from hospital_agent.http_client import ViewerClient


class ViewerClientTests(unittest.TestCase):
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
