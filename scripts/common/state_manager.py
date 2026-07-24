from pathlib import Path
from datetime import datetime, timezone
import json


ROOT_DIR = Path(__file__).resolve().parents[2]

STATE_DIR = ROOT_DIR / "state"


def _state_file(provider: str) -> Path:
    """
    Returns the JSON state file for a provider.

    Example:
        appstore -> state/appstore.json
        playstore -> state/playstore.json
    """

    STATE_DIR.mkdir(exist_ok=True)

    return STATE_DIR / f"{provider}.json"


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

        return {
            "last_review_id": None,
            "last_checked": None,
        }

    with open(file, "r", encoding="utf-8") as f:

        return json.load(f)


def save_state(
    provider: str,
    review_id: str,
):
    """
    Save latest processed review.
    """

    file = _state_file(provider)

    state = {
        "last_review_id": review_id,
        "last_checked": datetime.now(
            timezone.utc
        ).isoformat(),
    }

    with open(
        file,
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            state,
            f,
            indent=4,
        )


def get_last_review_id(
    provider: str,
):
    """
    Returns latest processed review id.
    """

    return load_state(provider).get(
        "last_review_id"
    )