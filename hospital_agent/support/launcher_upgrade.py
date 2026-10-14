"""Upgrade the stable supervisor only after its signed child passed health checks."""

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

LOGGER = logging.getLogger(__name__)


def upgrade_launcher(root: Path, active: Path) -> bool:
    """Schedule a graceful, lock-serialized supervisor restart; never stop active work."""
    if active.parent != root / ".updates" or not re.fullmatch(r"[a-f0-9]{64}", active.name):
        return False
    selection_path = root / ".updates" / "selection.json"
    if not selection_path.exists():
        return False
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    if selection.get("active") != active.name:
        return False  # The old supervisor has not committed its health check yet.
    bundled = active / "hospital_agent" / "support" / "launcher_update.py"
    if not bundled.is_file():
        return False
    target = root / "agent_launcher.py"
    source = bundled.read_bytes()
    previous = target.read_bytes()
    if previous == source:
        return False
    compile(source, str(target), "exec")
    temporary = target.with_suffix(".py.tmp")
    temporary.write_bytes(source)
    os.replace(temporary, target)
    env = {k: v for k, v in os.environ.items() if not k.startswith("VIEWER_AGENT_")}
    options = (
        {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP}
        if os.name == "nt"
        else {"start_new_session": True}
    )
    try:
        subprocess.Popen(
            [sys.executable, str(root / "hospital_agent.py"), "--wait-for-lock"],
            cwd=root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            **options,
        )
    except OSError:
        # The existing supervisor/agent still runs. Permit a later retry.
        temporary.write_bytes(previous)
        os.replace(temporary, target)
        raise
    (root / ".updates" / "stop").touch()
    LOGGER.info("Signed launcher update: graceful restart requested; version checks every 5 min")
    return True
