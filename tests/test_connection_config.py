import ssl
import unittest
from unittest.mock import patch

from hospital_agent.config import canonical_viewer_url
from hospital_agent.support.tls import verified_ssl_context


class ConnectionConfigTests(unittest.TestCase):
    def test_known_production_ip_is_migrated_without_touching_custom_servers(self):
        self.assertEqual(
            canonical_viewer_url("https://135.106.195.161/api/"), "https://angio.su/api"
        )
        self.assertEqual(canonical_viewer_url("http://135.106.130.37/api"), "https://angio.su/api")
        self.assertEqual(canonical_viewer_url("http://127.0.0.1:8080/"), "http://127.0.0.1:8080")
        self.assertEqual(
            canonical_viewer_url("https://custom.example/api"), "https://custom.example/api"
        )

    def test_tls_loads_system_roots_without_disabling_verification(self):
        with patch("hospital_agent.support.tls.ssl.create_default_context") as create:
            context = verified_ssl_context()
            create.assert_called_once()
            context.load_default_certs.assert_called_once_with(ssl.Purpose.SERVER_AUTH)
        real = verified_ssl_context()
        self.assertTrue(real.check_hostname)
        self.assertEqual(real.verify_mode, ssl.CERT_REQUIRED)
