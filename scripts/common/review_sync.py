"""Provider-neutral review selection, posting, and Slack reply polling helpers."""

import logging

from common.slack_client import (
    SlackApiError,
    SlackClient,
    SlackPermissionError,
    SlackThreadNotFoundError,
)
from common.state_manager import save_state, upsert_review


LOG = logging.getLogger(__name__)


def select_new_reviews(
    reviews: list[dict],
    state: dict,
    initial_sync: bool,
    initial_count: int,
    review_id_getter,
) -> list[dict]:
    """Select untracked reviews from a newest-first provider response."""
    known_ids = set(state.get("reviews", {}))
    if initial_sync:
        return [
            review
            for review in reviews[:initial_count]
            if review_id_getter(review) not in known_ids
        ]

    last_review_id = state.get("last_review_id")
    if not last_review_id:
        raise RuntimeError("Incremental sync requires last_review_id")

    new_reviews = []
    boundary_found = False
    for review in reviews:
        review_id = review_id_getter(review)
        if review_id == last_review_id:
            boundary_found = True
            break
        if review_id not in known_ids:
            new_reviews.append(review)
    if not boundary_found:
        LOG.warning("last_review_id %s was not present in the fetched review set", last_review_id)
    return new_reviews


def post_new_reviews(
    provider: str,
    reviews: list[dict],
    state: dict,
    slack: SlackClient,
    initial_sync: bool,
    initial_count: int,
    review_id_getter,
    formatter,
    reply_sent_key: str,
    reply_sent_getter=None,
) -> None:
    """Post selected reviews oldest-first and persist each Slack mapping."""
    new_reviews = select_new_reviews(
        reviews, state, initial_sync, initial_count, review_id_getter
    )
    if not new_reviews:
        LOG.info("No new %s reviews to send", provider)
        return

    LOG.info("Sending %d new %s review(s) to Slack", len(new_reviews), provider)
    for review in reversed(new_reviews):
        review_id = review_id_getter(review)
        slack_ts = slack.post_review(formatter(review))
        upsert_review(
            state,
            review_id,
            slack_ts=slack_ts,
            last_reply_ts=None,
            **{
                reply_sent_key: (
                    bool(reply_sent_getter(review))
                    if reply_sent_getter
                    else False
                )
            },
        )
        save_state(provider, state)
        LOG.info("Posted %s review %s to Slack thread %s", provider, review_id, slack_ts)

    state["last_review_id"] = review_id_getter(reviews[0])
    save_state(provider, state)


def reply_candidates(messages: list[dict], state_entry: dict, slack: SlackClient) -> list[dict]:
    """Return unique, newer, non-bot, non-empty human replies."""
    last_reply_ts = state_entry.get("last_reply_ts")
    candidates = []
    seen_timestamps = set()
    for message in messages:
        if not isinstance(message, dict):
            continue
        ts = message.get("ts")
        if not isinstance(ts, str) or ts in seen_timestamps or (last_reply_ts and ts <= last_reply_ts):
            continue
        seen_timestamps.add(ts)
        if slack.is_bot_message(message):
            continue
        text = (message.get("text") or "").strip()
        if text:
            candidates.append(message)
    return sorted(candidates, key=lambda message: message["ts"])


def sync_slack_replies(
    provider: str,
    state: dict,
    slack: SlackClient,
    reply_sent_key: str,
    send_reply,
    display_name: str | None = None,
) -> None:
    """Poll provider review threads and send each newest human reply once."""
    display_name = display_name or provider
    reviews = state.get("reviews", {})
    if not reviews:
        LOG.info("No %s review threads to poll", display_name)
        return

    slack.identify_bot()
    failures = []
    for review_id, entry in reviews.items():
        if entry.get("slack_thread_disabled") or entry.get(reply_sent_key):
            LOG.info("Skipping %s review %s: already completed or thread disabled", display_name, review_id)
            continue
        if not entry.get("slack_ts"):
            LOG.warning("%s review %s has no Slack timestamp; skipping", display_name, review_id)
            continue

        try:
            LOG.info("Polling Slack thread %s for %s review %s", entry["slack_ts"], display_name, review_id)
            messages = slack.replies(entry["slack_ts"])
            if len(messages) > 1:
                LOG.info(
                    "Slack thread for %s review %s contains %d message(s) including the parent",
                    display_name,
                    review_id,
                    len(messages),
                )
            candidates = reply_candidates(messages, entry, slack)
            if not candidates:
                LOG.debug("No new human Slack reply found for %s review %s", display_name, review_id)
                continue

            message = candidates[-1]
            LOG.info("Found new human Slack reply %s for %s review %s", message["ts"], display_name, review_id)
            LOG.info("Sending Slack reply %s to %s review %s", message["ts"], display_name, review_id)
            send_reply(review_id, message["text"])
            entry["last_reply_ts"] = message["ts"]
            entry[reply_sent_key] = True
            save_state(provider, state)
            LOG.info("Posted Slack reply %s to %s review %s", message["ts"], display_name, review_id)
        except SlackThreadNotFoundError as exc:
            entry["slack_thread_disabled"] = True
            save_state(provider, state)
            LOG.error("Slack thread for %s review %s was deleted or unavailable: %s", display_name, review_id, exc)
            failures.append(review_id)
        except SlackPermissionError:
            LOG.exception("Slack permission failure for %s review %s", display_name, review_id)
            raise
        except SlackApiError as exc:
            LOG.error("Slack API failure for %s review %s: %s", display_name, review_id, exc)
            if exc.error == "rate_limited":
                LOG.error("Slack rate limit was exhausted after Retry-After retries")
                raise
            failures.append(review_id)
        except Exception as exc:
            LOG.exception("Failed processing %s reply for review %s: %s", display_name, review_id, exc)
            failures.append(review_id)

    if failures:
        raise RuntimeError(
            f"{provider.title()} review synchronization failed for {len(failures)} review(s): "
            f"{', '.join(failures)}"
        )
