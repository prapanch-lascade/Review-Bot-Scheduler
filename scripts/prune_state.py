"""Prune inactive reviews from a state file to bound per-run Slack polling.

Every run polls `conversations.replies` once per review in the `reviews` map, so
that map must stay small. This drops reviews that are no longer worth polling
while keeping their ids in `posted_ids` (the permanent dedup set), so a pruned
review is never re-posted.

Rules (a review is kept only if it is still "active"):
  - replied:   keep for REPLY_EDIT_WINDOW_DAYS after the reply (allow an edit/replacement)
  - un-replied: keep for OPEN_POLL_WINDOW_DAYS after it was posted, then stop polling
  - disabled thread: never poll again

Run in the commit job AFTER the remote merge (running it before the merge would
be undone by the union). No-op if the file does not exist.

Usage: prune_state.py STATE_FILE
"""

import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


OPEN_POLL_WINDOW_DAYS = 30
REPLY_EDIT_WINDOW_DAYS = 2


def _age_days(timestamp: object, now: datetime) -> float:
    """Age in days of an ISO-8601 timestamp. Unparseable/missing → 0 (treat as now → keep)."""
    if not isinstance(timestamp, str) or not timestamp.strip():
        return 0.0
    try:
        value = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (now - value).total_seconds() / 86400.0


def prune_inactive(state: dict, now: datetime) -> dict:
    """Return state with inactive reviews removed from `reviews` (ids kept in `posted_ids`)."""
    posted = set(state.get("posted_ids", []) or [])
    kept = {}
    for review_id, entry in (state.get("reviews", {}) or {}).items():
        posted.add(review_id)  # never lose the id — dedup must be permanent
        if not isinstance(entry, dict):
            continue
        if entry.get("slack_thread_disabled"):
            continue  # dead thread → stop polling
        if entry.get("last_reply_ts"):
            if _age_days(entry.get("replied_at"), now) <= REPLY_EDIT_WINDOW_DAYS:
                kept[review_id] = entry  # replied recently → keep for edits
        else:
            if _age_days(entry.get("posted_at"), now) <= OPEN_POLL_WINDOW_DAYS:
                kept[review_id] = entry  # open & recent → keep polling
    state["reviews"] = kept
    state["posted_ids"] = sorted(posted)
    return state


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("Usage: prune_state.py STATE_FILE")

    path = Path(sys.argv[1])
    if not path.exists():
        return  # nothing to prune (e.g. a provider not configured for this app)

    state = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(state, dict):
        raise SystemExit(f"State file must contain a JSON object: {path}")

    before = len(state.get("reviews", {}) or {})
    prune_inactive(state, datetime.now(timezone.utc))
    after = len(state["reviews"])

    fd, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(state, handle, indent=4)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary_name, path)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise

    print(f"prune_state: {path} reviews {before} -> {after} (posted_ids {len(state['posted_ids'])})")


if __name__ == "__main__":
    main()
