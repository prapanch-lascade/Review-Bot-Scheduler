import copy
import logging
import os

from common.jwt_generator import generate_token
from common.review_sync import reply_candidates as common_reply_candidates
from common.review_sync import sync_slack_replies
from common.slack_client import SlackClient
from common.state_manager import load_state, save_if_changed, save_state, upsert_review
from common.utils import current_ist, request_with_retries, stars, utc_to_ist


LOG = logging.getLogger(__name__)
APPLE_API = "https://api.appstoreconnect.apple.com/v1"
INITIAL_SYNC_COUNT = 5


def _apple_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}


def _escape_slack(value: object) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def format_review(review: dict) -> str:
    """Format an Apple review without allowing review content to alter Slack markup."""
    attr = review["attributes"]
    rating = attr.get("rating", 0)
    title = _escape_slack(str(attr.get("title") or "").strip() or "No Title")
    body = _escape_slack(str(attr.get("body") or "").strip() or "No review text provided.")
    reviewer = _escape_slack(attr.get("reviewerNickname", "Anonymous"))
    territory = _escape_slack(attr.get("territory", "Unknown"))
    version = _escape_slack(attr.get("appVersionString", "Unknown"))
    reviewed_on = utc_to_ist(attr["createdDate"])
    review_id = _escape_slack(review["id"])

    return f"""
⭐ *New App Review Received*

{stars(rating)} *({rating}/5)*

📝 *Title*

{title}

💬 *Review*

{body}

👤 *Reviewer* : {reviewer}
🌍 *Country*  : {territory}
📱 *Version*  : {version}
📅 *Reviewed* : {reviewed_on}
🏪 *Platform* : Apple App Store

🆔 *Review ID* : {review_id}

⏰ *Detected* : {current_ist()}
""".strip() + "\n\n"


def _validate_review(review: object) -> None:
    if not isinstance(review, dict) or not isinstance(review.get("id"), str):
        raise RuntimeError("Apple review has an invalid resource shape")
    attributes = review.get("attributes")
    if not isinstance(attributes, dict) or not isinstance(attributes.get("createdDate"), str):
        raise RuntimeError(f"Apple review {review['id']} has invalid attributes")
    rating = attributes.get("rating", 0)
    if not isinstance(rating, int) or not 0 <= rating <= 5:
        raise RuntimeError(f"Apple review {review['id']} has an invalid rating")


def fetch_reviews(token: str) -> list[dict]:
    response = request_with_retries(
        "GET",
        f"{APPLE_API}/apps/{os.environ['APPSTORE_APP_ID']}/customerReviews",
        headers=_apple_headers(token),
        params={"limit": 200, "sort": "-createdDate"},
        timeout=30,
        operation="Apple list customer reviews",
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError("Apple customer reviews response was not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise RuntimeError("Apple customer reviews response has no data list")

    reviews = data["data"]
    for review in reviews:
        _validate_review(review)
    reviews.sort(key=lambda review: review["attributes"]["createdDate"], reverse=True)
    return reviews


def reply_to_review(token: str, review_id: str, text: str) -> None:
    """Create or replace Apple's single response for a customer review."""
    text = text.strip()
    if not text:
        raise ValueError("Cannot send an empty Apple review response")

    payload = {
        "data": {
            "type": "customerReviewResponses",
            "attributes": {"responseBody": text},
            "relationships": {
                "review": {"data": {"type": "customerReviews", "id": review_id}}
            },
        }
    }
    response = request_with_retries(
        "POST",
        f"{APPLE_API}/customerReviewResponses",
        headers=_apple_headers(token),
        json=payload,
        timeout=30,
        retry_network_errors=False,
        retry_server_errors=False,
        operation=f"Apple reply to review {review_id}",
    )
    response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        raise RuntimeError(f"Apple reply response for {review_id} was not valid JSON") from exc
    resource = data.get("data") if isinstance(data, dict) else None
    if not isinstance(resource, dict) or resource.get("type") != "customerReviewResponses":
        raise RuntimeError(f"Apple reply response for {review_id} was invalid")


def _new_reviews(reviews: list[dict], state: dict, initial_sync: bool) -> list[dict]:
    known_ids = set(state.get("reviews", {}))
    if initial_sync:
        # Also makes an interrupted initial sync safely resumable.
        return [review for review in reviews[:INITIAL_SYNC_COUNT] if review["id"] not in known_ids]

    last_review_id = state.get("last_review_id")
    if not last_review_id:
        raise RuntimeError("Incremental sync requires last_review_id")

    new_reviews = []
    boundary_found = False
    for review in reviews:
        if review["id"] == last_review_id:
            boundary_found = True
            break
        if review["id"] not in known_ids:
            new_reviews.append(review)
    if not boundary_found:
        LOG.warning("last_review_id %s was not present in the fetched review set", last_review_id)
    return new_reviews


def sync_reviews_to_slack(reviews: list[dict], state: dict, slack: SlackClient, initial_sync: bool) -> None:
    new_reviews = _new_reviews(reviews, state, initial_sync)
    if not new_reviews:
        LOG.info("No new reviews to send")
        return

    LOG.info("Sending %d new review(s) to Slack", len(new_reviews))
    for review in reversed(new_reviews):
        review_id = review["id"]
        slack_ts = slack.post_review(format_review(review))
        upsert_review(state, review_id, slack_ts=slack_ts, last_reply_ts=None, apple_reply_sent=False)
        save_state("appstore", state)
        LOG.info("Posted review %s to Slack thread %s", review_id, slack_ts)

    state["last_review_id"] = reviews[0]["id"]
    save_state("appstore", state)


def _reply_candidates(messages: list[dict], state_entry: dict, slack: SlackClient) -> list[dict]:
    return common_reply_candidates(messages, state_entry, slack)


REQUIRED_APPSTORE_ENV = (
    "APPSTORE_API_KEY_ID",
    "APPSTORE_ISSUER_ID",
    "APPSTORE_API_PRIVATE_KEY",
    "APPSTORE_APP_ID",
)


def run_appstore() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not all(os.environ.get(name) for name in REQUIRED_APPSTORE_ENV):
        LOG.info("App Store not configured for this app; skipping")
        return
    LOG.info("Generating App Store Connect JWT")
    token = generate_token()
    state = load_state("appstore")
    original_state = copy.deepcopy(state)
    initial_sync = not bool(state.get("last_review_id"))
    slack = SlackClient()

    LOG.info("Fetching App Store reviews")
    reviews = fetch_reviews(token)
    LOG.info("Fetched %d review(s)", len(reviews))
    if reviews:
        sync_reviews_to_slack(reviews, state, slack, initial_sync)

    if initial_sync:
        if reviews:
            LOG.info("Initial sync complete; saved %d review mapping(s)", len(state.get("reviews", {})))
        else:
            LOG.info("Initial sync found no reviews")
        return

    sync_slack_replies(
        "appstore",
        state,
        slack,
        "apple_reply_sent",
        lambda review_id, text: reply_to_review(token, review_id, text),
        "Apple App Store",
    )

    if state != original_state:
        save_if_changed("appstore", original_state, state)
        LOG.info("State updated")
    else:
        LOG.info("No state changes")
