"""Merge two provider state snapshots without losing either workflow's progress."""

import json
import os
import sys
import tempfile
from pathlib import Path


def _timestamp(value: object) -> str:
    return value if isinstance(value, str) else ""


def merge_states(remote: dict, local: dict) -> dict:
    merged = dict(remote)
    merged.update({key: value for key, value in local.items() if key not in {"reviews", "last_review_id", "last_checked"}})

    remote_reviews = remote.get("reviews", {})
    local_reviews = local.get("reviews", {})
    reviews = {**remote_reviews, **local_reviews}
    for review_id in set(remote_reviews) & set(local_reviews):
        remote_entry = remote_reviews[review_id]
        local_entry = local_reviews[review_id]
        entry = {**remote_entry, **local_entry}
        if remote_entry.get("slack_ts") and not local_entry.get("slack_ts"):
            entry["slack_ts"] = remote_entry["slack_ts"]
        reply_timestamps = [remote_entry.get("last_reply_ts"), local_entry.get("last_reply_ts")]
        reply_timestamps = [value for value in reply_timestamps if isinstance(value, str)]
        if reply_timestamps:
            entry["last_reply_ts"] = max(reply_timestamps)
        entry["apple_reply_sent"] = bool(
            remote_entry.get("apple_reply_sent") or local_entry.get("apple_reply_sent")
        )
        entry["slack_thread_disabled"] = bool(
            remote_entry.get("slack_thread_disabled") or local_entry.get("slack_thread_disabled")
        )
        reviews[review_id] = entry
    merged["reviews"] = reviews

    if _timestamp(local.get("last_checked")) >= _timestamp(remote.get("last_checked")):
        merged["last_review_id"] = local.get("last_review_id", remote.get("last_review_id"))
        merged["last_checked"] = local.get("last_checked", remote.get("last_checked"))
    else:
        merged["last_review_id"] = remote.get("last_review_id", local.get("last_review_id"))
        merged["last_checked"] = remote.get("last_checked", local.get("last_checked"))
    return merged


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("Usage: merge_state.py REMOTE_STATE LOCAL_STATE")

    remote_path = Path(sys.argv[1])
    local_path = Path(sys.argv[2])
    remote = json.loads(remote_path.read_text(encoding="utf-8"))
    local = json.loads(local_path.read_text(encoding="utf-8"))
    merged = merge_states(remote, local)

    fd, temporary_name = tempfile.mkstemp(prefix=f".{local_path.name}.", suffix=".tmp", dir=local_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(merged, handle, indent=4)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, local_path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


if __name__ == "__main__":
    main()
