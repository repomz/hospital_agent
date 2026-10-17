"""File provenance for edits/moves and conservative, complete source inventories."""

import hashlib
import logging
import os
import uuid
from pathlib import Path

LOGGER = logging.getLogger("hospital_agent.sources")
SOURCE_NAMESPACE = uuid.UUID("67a0c599-68bd-4d7f-9064-f77c78e57115")


def attach_protocol_source(config, state, path, payload):
    path = path.resolve()
    roots = getattr(getattr(config, "study_polling", None), "operations_dirs", None) or []
    root = next((r.resolve() for r in roots if path.is_relative_to(r.resolve())), None)
    if root is None:
        return  # CLI/import callers without a managed source never enable deletion.
    key = str(path)
    identity = hashlib.sha256(
        "|".join(
            str(payload.get(k, "")).strip().casefold().replace("ё", "е")
            for k in ("study_id", "patient", "room", "time_beginning")
        ).encode()
    ).hexdigest()
    source = state.protocol_sources.get(key)
    if source is None:
        # A move may also change the operation's title. Reuse the file identity
        # only when the original path is gone and the clinical key is unique.
        candidates = []
        for p, value in state.protocol_sources.items():
            if value.get("identity") != identity or not Path(value.get("root", "")).is_dir():
                continue
            try:
                Path(p).stat()
            except FileNotFoundError:
                candidates.append(value)
            except OSError:
                pass  # Inaccessible is not the same as deleted.
        ids = {v["file_id"] for v in candidates}
        file_id = (
            next(iter(ids))
            if len(ids) == 1
            else str(uuid.uuid5(SOURCE_NAMESPACE, f"{config.agent_id}:{key.casefold()}"))
        )
        source = {"file_id": file_id}
    source = {**source, "root": str(root), "path": key, "identity": identity}
    state.protocol_sources[key] = source
    payload["source"] = {
        "agent_id": int(config.agent_id),
        **{k: source[k] for k in ("file_id", "root", "path")},
    }


def publish_source_inventory(config, polling, state, viewer):
    """No snapshot after partial access, disabled polling, or while any DOCX is new.

    Inspect the filesystem again after uploads so moving/saving files during the
    processing loop cannot publish a stale destructive inventory.
    """
    if not getattr(polling, "state", False) or not state.protocol_sources:
        return
    import time

    inventories = []
    try:
        for root in polling.operations_dirs or []:
            root = root.resolve()
            if not root.is_dir():
                return
            present = set()

            def fail(error):
                raise error

            for folder, _, names in os.walk(root, onerror=fail):
                for name in names:
                    if name.startswith("~$") or not name.lower().endswith(".docx"):
                        continue
                    path = (Path(folder) / name).resolve()
                    if time.time() - path.stat().st_mtime < 10:
                        return
                    # An unseen DOCX might be a moved source. Wait for its parse
                    # before considering the previous path deleted.
                    if str(path) not in state.protocol_sources:
                        # Pre-existing invalid documents in the baseline never
                        # owned a server record. Newly appearing unreadable
                        # files still block deletion (they may be moved files).
                        from .polling.protocols import protocol_signature

                        if state.reconciliation.get("files", {}).get(
                            str(path)
                        ) == protocol_signature(path) and str(path) in state.reconciliation.get(
                            "review", {}
                        ):
                            continue
                        return
                    present.add(state.protocol_sources[str(path)]["file_id"])
            inventories.append((str(root), sorted(present)))
    except OSError as exc:
        LOGGER.warning("Source inventory incomplete; deletions disabled: %s", exc)
        return
    for root, ids in inventories:
        if not viewer.post_json(
            "/agent/protocols/snapshot",
            {
                "agent_id": int(config.agent_id),
                "root": root,
                "scan_id": str(uuid.uuid4()),
                "file_ids": ids,
            },
        ):
            LOGGER.info("Source inventory not accepted; retry next scan: root=%s", root)
