"""Google Play review synchronization provider."""

import copy
import json
import logging
import os
from datetime import datetime, timezone

from google.auth.transport.requests import Request
from google.oauth2 import service_account

from common.review_sync import post_new_reviews, sync_slack_replies
from common.slack_client import SlackClient
from common.state_manager import load_state, save_if_changed
from common.utils import IST, current_ist, request_with_retries, stars


LOG = logging.getLogger(__name__)
GOOGLE_PLAY_API = "https://androidpublisher.googleapis.com/androidpublisher/v3"
GOOGLE_PLAY_SCOPE = "https://www.googleapis.com/auth/androidpublisher"
INITIAL_SYNC_COUNT = 5
MAX_RESULTS = 100
MAX_REPLY_LENGTH = 350


def _package_name() -> str:
    package_name = os.environ.get("GOOGLE_PLAY_PACKAGE_NAME", "").strip()
    if not package_name:
        raise RuntimeError("GOOGLE_PLAY_PACKAGE_NAME is required")
    return package_name


def _credentials() -> service_account.Credentials:
    raw_json = os.environ.get("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON", "")
    if not raw_json:
        raise RuntimeError("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON is required")
    try:
        info = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON is not valid JSON") from exc
    if not isinstance(info, dict):
        raise RuntimeError("GOOGLE_PLAY_SERVICE_ACCOUNT_JSON must contain a JSON object")
    try:
        credentials = service_account.Credentials.from_service_account_info(
            info, scopes=[GOOGLE_PLAY_SCOPE]
        )
        credentials.refresh(Request())
    except Exception as exc:
        raise RuntimeError("Could not authenticate with the Google Play service account") from exc
    if not credentials.token:
        raise RuntimeError("Google authentication returned no access token")
    return credentials


def _headers(credentials: service_account.Credentials) -> dict:
    return {
        "Authorization": f"Bearer {credentials.token}",
        "Content-Type": "application/json",
    }


def _timestamp_value(timestamp: object) -> float:
    if not isinstance(timestamp, dict):
        return 0.0
    try:
        seconds = float(timestamp.get("seconds", 0))
        nanos = float(timestamp.get("nanos", 0))
    except (TypeError, ValueError):
        return 0.0
    return seconds + nanos / 1_000_000_000


def _user_comments(review: dict) -> list[dict]:
    comments = review.get("comments")
    if not isinstance(comments, list):
        raise RuntimeError(f"Google Play review {review.get('reviewId')} has no comments list")
    return [
        comment["userComment"]
        for comment in comments
        if isinstance(comment, dict) and isinstance(comment.get("userComment"), dict)
    ]


def _user_comment(review: dict) -> dict:
    user_comments = _user_comments(review)
    if not user_comments:
        raise RuntimeError(f"Google Play review {review.get('reviewId')} has no user comment")
    return max(user_comments, key=lambda comment: _timestamp_value(comment.get("lastModified")))


def _has_developer_reply(review: dict) -> bool:
    comments = review.get("comments")
    if not isinstance(comments, list):
        return False
    return any(
        isinstance(comment, dict)
        and isinstance(comment.get("developerComment"), dict)
        and bool((comment["developerComment"].get("text") or "").strip())
        for comment in comments
    )


def _validate_review(review: object) -> None:
    if (
        not isinstance(review, dict)
        or not isinstance(review.get("reviewId"), str)
        or not review["reviewId"].strip()
    ):
        raise RuntimeError("Google Play review has an invalid resource shape")
    comment = _user_comment(review)
    rating = comment.get("starRating")
    if rating is not None and (isinstance(rating, bool) or not isinstance(rating, int) or not 1 <= rating <= 5):
        raise RuntimeError(f"Google Play review {review['reviewId']} has an invalid rating")
    if comment.get("text") is not None and not isinstance(comment.get("text"), str):
        raise RuntimeError(f"Google Play review {review['reviewId']} has invalid text")


def _log_failure(
    review_id: str,
    endpoint: str,
    message: str,
    status: object = "N/A",
    retry_attempt: str = "final_after_shared_retries",
) -> None:
    LOG.error(
        "provider=Google Play review_id=%s http_status=%s endpoint=%s "
        "retry_attempt=%s error=%s",
        review_id,
        status,
        endpoint,
        retry_attempt,
        message,
    )


def fetch_reviews(credentials: service_account.Credentials) -> list[dict]:
    package_name = _package_name()
    endpoint = f"{GOOGLE_PLAY_API}/applications/{package_name}/reviews"
    try:
        response = request_with_retries(
            "GET",
            endpoint,
            headers=_headers(credentials),
            params={"maxResults": MAX_RESULTS},
            timeout=30,
            operation="Google Play list reviews",
        )
    except Exception as exc:
        _log_failure("N/A", endpoint, str(exc))
        raise
    if not response.ok:
        _log_failure("N/A", endpoint, response.text[:500], response.status_code)
        response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        _log_failure("N/A", endpoint, "response was not valid JSON", response.status_code, "not_applicable")
        raise RuntimeError("Google Play reviews response was not valid JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("reviews", []), list):
        _log_failure("N/A", endpoint, "response has no reviews list", response.status_code, "not_applicable")
        raise RuntimeError("Google Play reviews response has no reviews list")

    reviews = data.get("reviews", [])
    pagination = data.get("tokenPagination")
    next_page = pagination.get("nextPageToken") if isinstance(pagination, dict) else None
    if next_page:
        LOG.warning(
            "Google Play returned another review page; pagination is intentionally disabled, "
            "so only the first page will be processed"
        )
    valid_reviews = []
    for review in reviews:
        review_id = review.get("reviewId", "N/A") if isinstance(review, dict) else "N/A"
        try:
            _validate_review(review)
        except RuntimeError as exc:
            _log_failure(str(review_id), endpoint, str(exc), response.status_code)
            continue
        valid_reviews.append(review)
    valid_reviews.sort(
        key=lambda review: _timestamp_value(_user_comment(review).get("lastModified")),
        reverse=True,
    )
    return valid_reviews


def _split_title_body(text: str) -> tuple[str, str]:
    if "\t" in text:
        title, body = text.split("\t", 1)
        return title.strip() or "No Title", body.strip() or "No review text provided."
    return "No Title", text.strip() or "No review text provided."


def _escape_slack(value: object) -> str:
    return str(value or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _display_value(value: object, default: str) -> str:
    if value is None or not str(value).strip():
        return default
    return str(value).strip()


def _rating_value(value: object) -> int:
    if isinstance(value, int) and not isinstance(value, bool) and 1 <= value <= 5:
        return value
    return 0


def _review_date(comment: dict) -> str:
    value = _timestamp_value(comment.get("lastModified"))
    if not value:
        return "Unknown"
    return datetime.fromtimestamp(value, tz=timezone.utc).astimezone(IST).strftime("%d %b %Y, %I:%M %p IST")


def format_review(review: dict) -> str:
    comment = _user_comment(review)
    title, body = _split_title_body(_display_value(comment.get("text"), ""))
    rating = _rating_value(comment.get("starRating"))
    return f"""
⭐ *New App Review Received*

{stars(rating)} *({rating}/5)*

📝 *Title*

{_escape_slack(title)}

💬 *Review*

{_escape_slack(body)}

👤 *Reviewer* : {_escape_slack(_display_value(review.get("authorName"), "Anonymous"))}
🌍 *Language* : {_escape_slack(_display_value(comment.get("reviewerLanguage"), "Unknown"))}
📱 *Version*  : {_escape_slack(_display_value(comment.get("appVersionName"), "Unknown"))}
📅 *Reviewed* : {_escape_slack(_review_date(comment))}
🏪 *Platform* : Google Play

🆔 *Review ID* : {_escape_slack(review["reviewId"])}

⏰ *Detected* : {current_ist()}
""".strip() + "\n\n"


def _review_id(review: dict) -> str:
    return review["reviewId"]


def _prepare_reply(text: str, review_id: str) -> str:
    text = text.strip()
    if not text:
        raise ValueError("Cannot send an empty Google Play review response")
    if len(text) <= MAX_REPLY_LENGTH:
        return text
    truncated = text[:MAX_REPLY_LENGTH].rstrip()
    LOG.warning(
        "provider=Google Play review_id=%s reply_truncated=true original_length=%d final_length=%d limit=%d",
        review_id,
        len(text),
        len(truncated),
        MAX_REPLY_LENGTH,
    )
    return truncated


def reply_to_review(credentials: service_account.Credentials, review_id: str, text: str) -> None:
    text = _prepare_reply(text, review_id)

    package_name = _package_name()
    endpoint = f"{GOOGLE_PLAY_API}/applications/{package_name}/reviews/{review_id}:reply"
    try:
        response = request_with_retries(
            "POST",
            endpoint,
            headers=_headers(credentials),
            json={"replyText": text},
            timeout=30,
            retry_network_errors=False,
            retry_server_errors=False,
            operation=f"Google Play reply to review {review_id}",
        )
    except Exception as exc:
        _log_failure(review_id, endpoint, str(exc))
        raise
    if not response.ok:
        _log_failure(review_id, endpoint, response.text[:500], response.status_code)
        response.raise_for_status()
    try:
        data = response.json()
    except ValueError as exc:
        _log_failure(review_id, endpoint, "response was not valid JSON", response.status_code, "not_retried_for_write_safety")
        raise RuntimeError(f"Google Play reply response for {review_id} was not valid JSON") from exc
    result = data.get("result") if isinstance(data, dict) else None
    if not isinstance(result, dict) or result.get("replyText") != text:
        _log_failure(review_id, endpoint, "response did not contain the accepted reply text", response.status_code, "not_applicable")
        raise RuntimeError(f"Google Play reply response for {review_id} was invalid")


def run_playstore() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    LOG.info("Generating Google Play OAuth access token")
    credentials = _credentials()
    state = load_state("playstore")
    original_state = copy.deepcopy(state)
    initial_sync = not bool(state.get("last_review_id"))
    slack = SlackClient()
    existing_reply_state_changed = False

    LOG.info("Fetching Google Play reviews")
    reviews = fetch_reviews(credentials)
    LOG.info("Fetched %d Google Play review(s)", len(reviews))
    for review in reviews:
        review_id = _review_id(review)
        if _has_developer_reply(review):
            entry = state.get("reviews", {}).get(review_id)
            if entry is not None and not entry.get("google_reply_sent"):
                entry["google_reply_sent"] = True
                existing_reply_state_changed = True
                LOG.warning(
                    "provider=Google Play review_id=%s existing developerComment detected; "
                    "automatic overwrite disabled",
                    review_id,
                )
    if reviews:
        post_new_reviews(
            "playstore",
            reviews,
            state,
            slack,
            initial_sync,
            INITIAL_SYNC_COUNT,
            _review_id,
            format_review,
            "google_reply_sent",
            _has_developer_reply,
        )
    if existing_reply_state_changed:
        save_if_changed("playstore", original_state, state)

    if initial_sync:
        if reviews:
            LOG.info("Google Play initial sync complete; saved %d review mapping(s)", len(state.get("reviews", {})))
        else:
            LOG.info("Google Play initial sync found no reviews")
        return

    sync_slack_replies(
        "playstore",
        state,
        slack,
        "google_reply_sent",
        lambda review_id, text: reply_to_review(credentials, review_id, text),
        "Google Play",
    )
    if state != original_state:
        save_if_changed("playstore", original_state, state)
        LOG.info("Google Play state updated")
    else:
        LOG.info("No Google Play state changes")
