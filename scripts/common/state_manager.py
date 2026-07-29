import json
from datetime import datetime, timezone
import os
from pathlib import Path
import tempfile


ROOT_DIR = Path(__file__).resolve().parents[2]

STATE_DIR = ROOT_DIR / "state"
STATE_VERSION = 1


def _state_file(provider: str) -> Path:
    """
    Returns the JSON state file for a provider.

    Example:
        appstore -> state/appstore_reviews.json
        playstore -> state/playstore.json
    """

    STATE_DIR.mkdir(exist_ok=True)

    filename = "appstore_reviews.json" if provider == "appstore" else f"{provider}.json"
    return STATE_DIR / filename


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

        return _empty_state()

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
    state.setdefault("state_version", STATE_VERSION)
    return state


def _empty_state() -> dict:
    return {"state_version": STATE_VERSION, "last_review_id": None, "last_checked": None, "reviews": {}}


def save_state(provider: str, state: dict):
    """Persist provider state, including review-to-thread mappings."""
    file = _state_file(provider)
    state = dict(state)
    state["state_version"] = STATE_VERSION
    state["last_checked"] = datetime.now(timezone.utc).isoformat()
    state.setdefault("reviews", {})

    STATE_DIR.mkdir(exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(prefix=f".{file.name}.", suffix=".tmp", dir=STATE_DIR)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=4)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_name, file)
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


def upsert_review(state: dict, review_id: str, **values) -> None:
    state.setdefault("reviews", {})
    state["reviews"].setdefault(review_id, {})
    state["reviews"][review_id].update(values)


def save_if_changed(provider: str, original: dict, state: dict) -> bool:
    """Save only when meaningful state changed; timestamps don't cause churn."""
    if state == original:
        return False
    save_state(provider, state)
    return True
