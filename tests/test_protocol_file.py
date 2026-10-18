import base64
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from hospital_agent.services.protocol_file import send_protocol_file


class ProtocolFileTests(unittest.TestCase):
    def test_original_even_if_broken_and_empty(self):
        with tempfile.TemporaryDirectory() as root:
            config = SimpleNamespace(study_polling=SimpleNamespace(operations_dirs=[root]))
            viewer = Mock()
            viewer.post_json_response.return_value = {"id": "file", "name": "Ошибка.docx"}
            path = Path(root) / "Ошибка.docx"
            for content in [b"", b"not a valid docx"]:
                path.write_bytes(content)
                result = send_protocol_file(config, {"path": str(path), "upload_token": "token"}, "request", viewer)
                args, kwargs = viewer.post_json_response.call_args
                self.assertEqual(args[0], "/agent_files/request")
                self.assertEqual(base64.b64decode(args[1]["content"]), content)
                self.assertEqual(result["size_bytes"], len(content))
                self.assertEqual(kwargs["timeout_seconds"], 180)

    def test_reject_outside_root_extension_size_and_symlink(self):
        with tempfile.TemporaryDirectory() as root, tempfile.TemporaryDirectory() as other:
            config = SimpleNamespace(study_polling=SimpleNamespace(operations_dirs=[root]))
            viewer = Mock()
            outside = Path(other) / "outside.docx"
            outside.write_bytes(b"secret")
            link = Path(root) / "link.docx"
            link.symlink_to(outside)
            secret = Path(root) / "env.txt"
            secret.write_text("secret")
            for path in [outside, link, secret, Path("relative.docx")]:
                with self.assertRaises(ValueError):
                    send_protocol_file(config, {"path": str(path)}, "request", viewer)
            large = Path(root) / "large.docx"
            large.write_bytes(b"12345")
            with patch("hospital_agent.services.protocol_file.MAX_PROTOCOL_BYTES", 4):
                with self.assertRaises(ValueError):
                    send_protocol_file(config, {"path": str(large)}, "request", viewer)
            viewer.post_json_response.assert_not_called()

    def test_failed_upload_is_not_success(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "protocol.docx"
            path.touch()
            config = SimpleNamespace(study_polling=SimpleNamespace(operations_dirs=[root]))
            viewer = Mock()
            viewer.post_json_response.return_value = None
            with self.assertRaises(RuntimeError):
                send_protocol_file(config, {"path": str(path)}, "request", viewer)
