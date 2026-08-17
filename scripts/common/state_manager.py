import json
from datetime import datetime, timezone
import logging
import os
from pathlib import Path
import tempfile


LOG = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]

STATE_DIR = ROOT_DIR / "state"
STATE_VERSION = 2


def _state_file(provider: str) -> Path:
    """
    Returns the JSON state file for an app + provider.

    The app is taken from the PROJECT_SLUG environment variable so multiple apps
    can share this code without sharing state. Each app gets its own folder
    holding one file per provider:

        state/airlines70/appstore.json
        state/airlines70/playstore.json

    Only the providers an app actually uses are created (an Android-only app
    only ever writes playstore.json). When PROJECT_SLUG is unset the legacy
    single-app names are used, keeping older runs and tests working:

        state/appstore_reviews.json
        state/playstore_reviews.json
    """

    project_slug = os.environ.get("PROJECT_SLUG", "").strip()
    if project_slug:
        app_dir = STATE_DIR / project_slug
        app_dir.mkdir(parents=True, exist_ok=True)
        return app_dir / f"{provider}.json"

    STATE_DIR.mkdir(exist_ok=True)
    return STATE_DIR / f"{provider}_reviews.json"


def load_state(provider: str) -> dict:
    """
    Load provider state.

    Returns:
    {
        "last_review_id": "...",
        "last_checked": "..."
    }
    """

    file = _state_file(provider)

    if not file.exists():
        LOG.info("No existing state at %s; starting fresh (initial sync)", file)
        return _empty_state()

    LOG.info("Loading state from %s", file)

    try:
        with open(file, "r", encoding="utf-8") as f:
            state = json.load(f)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"State file is invalid JSON: {file}. Restore it or remove it deliberately.") from exc

    if not isinstance(state, dict):
        raise RuntimeError(f"State file must contain a JSON object: {file}")
    if "reviews" in state and not isinstance(state["reviews"], dict):
        raise RuntimeError(f"State file 'reviews' must be an object: {file}")
    for review_id, entry in state.get("reviews", {}).items():
        if not isinstance(review_id, str) or not isinstance(entry, dict):
            raise RuntimeError(f"State review entry {review_id!r} must be an object: {file}")

    # Keep old state files readable while adding the per-review mapping.
    state.setdefault("last_review_id", None)
    state.setdefault("last_checked", None)
    state.setdefault("reviews", {})
    state.setdefault("posted_ids", [])
    state.setdefault("state_version", STATE_VERSION)

    # v1 -> v2 migration: posted_ids is the permanent dedup source, and each
    # review entry needs a posted_at for the pruning window. Backfill both so an
    # older state file never re-posts or drops a review after this upgrade.
    if not isinstance(state["posted_ids"], list):
        raise RuntimeError(f"State 'posted_ids' must be a list: {file}")
    known = set(state["posted_ids"])
    for review_id in state["reviews"]:
        if review_id not in known:
            state["posted_ids"].append(review_id)
            known.add(review_id)
    fallback_posted_at = state.get("last_checked") or now_iso()
    for entry in state["reviews"].values():
        entry.setdefault("posted_at", fallback_posted_at)
    return state


def _empty_state() -> dict:
    return {
        "state_version": STATE_VERSION,
        "last_review_id": None,
        "last_checked": None,
        "posted_ids": [],
        "reviews": {},
    }


def save_state(provider: str, state: dict):
    """Persist provider state, including review-to-thread mappings."""
    file = _state_file(provider)
    state = dict(state)
    state["state_version"] = STATE_VERSION
    state["last_checked"] = datetime.now(timezone.utc).isoformat()
    state.setdefault("reviews", {})

    file.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{file.name}.", suffix=".tmp", dir=file.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_name, file)
        LOG.debug("Saved %s state to %s", provider, file)
    except Exception:
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass
        raise


def get_last_review_id(
    provider: str,
):
    """
    Returns latest processed review id.
    """

    return load_state(provider).get(
        "last_review_id"
    )


def now_iso() -> str:
    """Current UTC time as an ISO-8601 string (used for posted_at/replied_at)."""
    return datetime.now(timezone.utc).isoformat()


def upsert_review(state: dict, review_id: str, **values) -> None:
    state.setdefault("reviews", {})
    state["reviews"].setdefault(review_id, {})
    state["reviews"][review_id].update(values)


def mark_posted(state: dict, review_id: str) -> None:
    """Record a review id in the permanent dedup set (never pruned)."""
    ids = state.setdefault("posted_ids", [])
    if review_id not in ids:
        ids.append(review_id)


def save_if_changed(provider: str, original: dict, state: dict) -> bool:
    """Save only when meaningful state changed; timestamps don't cause churn."""
    if state == original:
        return False
    save_state(provider, state)
    return True
