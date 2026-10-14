"""Stable Windows/Python launcher. Signed updates never replace live application files."""

import base64
import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import urlopen

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

LOG = logging.getLogger("agent_updater")
UPDATE_CHECK_INTERVAL_SECONDS = 300
PUBLIC_KEY = "LeZ7JxvIWyysSI0Mg7lor8aI6WYqu1kk4ptsaiQ80VU="
MAX_PACKAGE = 20 * 1024 * 1024


def version_tuple(value):
    if not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("invalid release version")
    return tuple(map(int, value.split(".")))


def verify_manifest(envelope, public_key=PUBLIC_KEY):
    payload = envelope["release"]
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    Ed25519PublicKey.from_public_bytes(base64.b64decode(public_key)).verify(
        base64.b64decode(envelope["signature"], validate=True), raw
    )
    version_tuple(payload["version"])
    if not re.fullmatch(r"[a-f0-9]{64}", payload["sha256"]):
        raise ValueError("invalid release hash")
    if not 0 < payload["size"] <= MAX_PACKAGE:
        raise ValueError("invalid release size")
    return payload


def unpack(package, target):
    with zipfile.ZipFile(package) as archive:
        if (
            len(archive.infolist()) > 2000
            or sum(i.file_size for i in archive.infolist()) > 80 * 1024 * 1024
        ):
            raise ValueError("oversized release")
        for item in archive.infolist():
            path = Path(item.filename)
            if (
                path.is_absolute()
                or ".." in path.parts
                or "\\" in item.filename
                or ":" in item.filename
            ):
                raise ValueError("unsafe archive path")
            if (item.external_attr >> 16) & 0o170000 == 0o120000:
                raise ValueError("symlink in release")
            if not (
                item.filename.startswith("hospital_agent/")
                or item.filename in {"hospital_agent.py", "pyproject.toml"}
            ):
                raise ValueError("unexpected release file")
        archive.extractall(target)


def code_version(folder):
    text = (folder / "hospital_agent" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([\d.]+)"', text)[1]


def runtime_hash(root):
    metadata = (root / "pyproject.toml").read_text(encoding="utf-8")
    metadata = re.sub(r'^version = ".*"$', "", metadata, flags=re.M)
    return hashlib.sha256(metadata.encode()).hexdigest()


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value), encoding="utf-8")
    os.replace(temporary, path)


def prepare_update(root, active, directory, selection):
    # Use the stable root's verified TLS helper, not downloaded Python code.
    sys.path.insert(0, str(root))
    from hospital_agent.config import canonical_viewer_url
    from hospital_agent.support.tls import verified_ssl_context

    config = json.loads((root / "agent_config.json").read_text(encoding="utf-8"))
    base = canonical_viewer_url(config["viewer_url"])
    if not base.startswith("https://"):
        raise ValueError("automatic updates require HTTPS")
    agent_id = str(int(config["agent_id"]))
    context = verified_ssl_context()
    try:
        response = urlopen(
            base + f"/agent/releases/agent-{agent_id}.json", timeout=30, context=context
        )
    except HTTPError as exc:
        if exc.code != 404:
            raise
        response = urlopen(base + "/agent/releases/default.json", timeout=30, context=context)
    with response:
        envelope = json.loads(response.read(64 * 1024))
    release = verify_manifest(envelope)
    if version_tuple(release["version"]) <= version_tuple(code_version(active)) or release[
        "sha256"
    ] in selection.get("failed", []):
        return None
    # Dependency changes require a tested manual installer, never pip on a live agent.
    if release["runtime_sha256"] != runtime_hash(root):
        raise ValueError("release requires manual dependency update")
    package = directory / (release["sha256"] + ".zip")
    with urlopen(base + "/agent/releases/" + package.name, timeout=60, context=context) as response:
        data = response.read(MAX_PACKAGE + 1)
    if len(data) != release["size"] or hashlib.sha256(data).hexdigest() != release["sha256"]:
        raise ValueError("release checksum mismatch")
    package.write_bytes(data)
    target = directory / release["sha256"]
    target.mkdir(exist_ok=True)
    unpack(package, target)
    if code_version(target) != release["version"]:
        raise ValueError("release version mismatch")
    subprocess.run(
        [sys.executable, "-m", "compileall", "-q", str(target / "hospital_agent")],
        check=True,
        timeout=60,
    )
    return target


def main():
    root = Path(__file__).resolve().parent
    directory = root / ".updates"
    directory.mkdir(exist_ok=True)
    logging.basicConfig(
        filename=directory / "updater.log",
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | updater | %(message)s",
    )
    # Lifetime process lock prevents duplicate agents started by shortcut + scheduler.
    lock = (directory / "launcher.lock").open("a+b")
    lock.seek(0)
    lock.write(b"0")
    lock.flush()
    lock.seek(0)
    while True:
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except OSError:
            if "--wait-for-lock" not in sys.argv:
                return 0
            time.sleep(1)
    if "--reconcile" in sys.argv:
        from hospital_agent.config import load_agent_config
        from hospital_agent.state import load_state, save_state

        config = load_agent_config(root / "agent_config.json")
        state = load_state(config.state_file)
        state.reconciliation = {}
        save_state(config.state_file, state)
    selection_path = directory / "selection.json"
    selection = json.loads(selection_path.read_text()) if selection_path.exists() else {}
    selected = selection.get("active", "")
    active = directory / selected if re.fullmatch(r"[a-f0-9]{64}", selected) else root
    if not (active / "hospital_agent.py").exists():
        active = root
    elif version_tuple(code_version(root)) > version_tuple(code_version(active)):
        active = root  # A newer manual installation supersedes the stored selection.
    stop, ready = directory / "stop", directory / "ready"

    def launch(folder):
        stop.unlink(missing_ok=True)
        ready.unlink(missing_ok=True)
        env = {
            **os.environ,
            "VIEWER_AGENT_CHILD": "1",
            "VIEWER_AGENT_STOP": str(stop),
            "VIEWER_AGENT_READY": str(ready),
        }
        return subprocess.Popen(
            [sys.executable, str(folder / "hospital_agent.py")], cwd=root, env=env
        )

    process = launch(active)
    next_check = 0.0
    while process.poll() is None:
        if time.monotonic() >= next_check:
            next_check = time.monotonic() + UPDATE_CHECK_INTERVAL_SECONDS
            try:
                candidate = prepare_update(root, active, directory, selection)
            except Exception:
                LOG.exception("Update check failed; existing agent continues")
                candidate = None
            if candidate:
                LOG.info("Waiting for active tasks before update to %s", code_version(candidate))
                stop.touch()
                process.wait()  # Never kill an active DICOM/protocol transfer.
                process = launch(candidate)
                deadline = time.monotonic() + 60
                while time.monotonic() < deadline and process.poll() is None:
                    time.sleep(1)
                if process.poll() is None and ready.exists():
                    active = candidate
                    selection["active"] = candidate.name
                    LOG.info("Update healthy: %s", code_version(candidate))
                else:
                    if process.poll() is None:
                        stop.touch()
                        process.wait()
                    selection.setdefault("failed", []).append(candidate.name)
                    process = launch(active)
                    LOG.error("Update rolled back")
                atomic_json(selection_path, selection)
        time.sleep(1)
    return process.returncode


if __name__ == "__main__":
    raise SystemExit(main())
