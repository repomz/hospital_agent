"""Build allowlisted Windows transfer and signed update packages (no credentials/state)."""

import argparse
import base64
import hashlib
import json
import os
import sys
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from agent_launcher import PUBLIC_KEY, code_version, runtime_hash


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--key", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--init-key", action="store_true")
    args = parser.parse_args()
    if args.init_key and not args.key.exists():
        args.key.parent.mkdir(parents=True, exist_ok=True)
        key = Ed25519PrivateKey.generate()
        descriptor = os.open(args.key, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(
                key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
            )
    key = serialization.load_pem_private_key(args.key.read_bytes(), password=None)
    public = base64.b64encode(
        key.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    ).decode()
    if args.init_key:
        print("PUBLIC_KEY=" + public)
        return
    if public != PUBLIC_KEY:
        raise ValueError("signing key does not match pinned launcher key")
    args.output.mkdir(parents=True, exist_ok=True)
    code = sorted((ROOT / "hospital_agent").rglob("*.py")) + [
        ROOT / "hospital_agent.py",
        ROOT / "pyproject.toml",
    ]
    package = args.output / "update.zip"
    with zipfile.ZipFile(package, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in code:
            archive.write(path, path.relative_to(ROOT).as_posix())
    content = package.read_bytes()
    release = {
        "version": code_version(ROOT),
        "sha256": hashlib.sha256(content).hexdigest(),
        "size": len(content),
        "runtime_sha256": runtime_hash(ROOT),
    }
    package.rename(args.output / (release["sha256"] + ".zip"))
    signature = key.sign(json.dumps(release, sort_keys=True, separators=(",", ":")).encode())
    (args.output / "default.json").write_text(
        json.dumps(
            {"release": release, "signature": base64.b64encode(signature).decode()}, indent=2
        ),
        encoding="utf-8",
    )
    manual = args.output / f"hospital_agent_windows_{release['version']}.zip"
    with zipfile.ZipFile(manual, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in code + [
            ROOT / n
            for n in (
                "agent_launcher.py",
                "requirements.txt",
                "agent_config.json.example",
                "config.json.example",
                ".env.example",
                "INSTALL.cmd",
                "RECONCILE.cmd",
                "START.cmd",
                "UPDATE_README.txt",
            )
        ]:
            archive.write(path, path.relative_to(ROOT).as_posix())
    print(
        json.dumps(
            {
                "version": release["version"],
                "manual": str(manual),
                "bytes": manual.stat().st_size,
                "sha256": hashlib.sha256(manual.read_bytes()).hexdigest(),
            }
        )
    )


if __name__ == "__main__":
    main()
