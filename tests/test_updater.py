import base64
import json
import unittest
import zipfile
from pathlib import Path
from tempfile import TemporaryDirectory

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from agent_launcher import unpack, verify_manifest


class UpdateTests(unittest.TestCase):
    def test_signed_manifest_and_tampering(self):
        key = Ed25519PrivateKey.generate()
        public = base64.b64encode(
            key.public_key().public_bytes(
                serialization.Encoding.Raw, serialization.PublicFormat.Raw
            )
        ).decode()
        release = {"version": "0.2.12", "size": 100, "sha256": "a" * 64}
        signature = key.sign(json.dumps(release, sort_keys=True, separators=(",", ":")).encode())
        envelope = {"release": release, "signature": base64.b64encode(signature).decode()}
        self.assertEqual(verify_manifest(envelope, public), release)
        release["size"] = 101
        with self.assertRaises(InvalidSignature):
            verify_manifest(envelope, public)

    def test_archive_cannot_overwrite_config_or_escape(self):
        for name in (
            "../state.json",
            "agent_config.json",
            "C:/secret",
            "hospital_agent/../../secret",
        ):
            with self.subTest(name=name), TemporaryDirectory() as directory:
                root = Path(directory)
                package = root / "release.zip"
                with zipfile.ZipFile(package, "w") as archive:
                    archive.writestr(name, "bad")
                with self.assertRaises(ValueError):
                    unpack(package, root / "out")
