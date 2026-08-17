import os
import unittest
from unittest.mock import Mock, patch

from providers.appstore import INITIAL_SYNC_COUNT, _new_reviews, _reply_candidates, fetch_reviews
from common.slack_client import SlackClient


def review(review_id: str) -> dict:
    return {"id": review_id, "attributes": {"createdDate": review_id, "rating": 5}}


class AppStoreSyncTests(unittest.TestCase):
    def test_initial_sync_is_limited_and_resumes_without_duplicates(self):
        reviews = [review(str(number)) for number in range(10, 0, -1)]
        state = {"last_review_id": None, "reviews": {"10": {}, "9": {}}}

        result = _new_reviews(reviews, state, initial_sync=True)

        self.assertEqual([item["id"] for item in result], ["8", "7", "6"])
        self.assertEqual(INITIAL_SYNC_COUNT, 5)

    def test_incremental_sync_stops_at_last_review(self):
        reviews = [review("12"), review("11"), review("10"), review("9"), review("8")]
        state = {"last_review_id": "10", "reviews": {"10": {}, "9": {}, "8": {}}}

        result = _new_reviews(reviews, state, initial_sync=False)

        self.assertEqual([item["id"] for item in result], ["12", "11"])

    def test_new_reviews_dedup_via_posted_ids(self):
        # "2" was posted earlier and pruned from reviews, but survives in posted_ids,
        # so it must not be re-selected even though it is not in the reviews map.
        reviews = [review("3"), review("2"), review("1")]
        state = {"last_review_id": "1", "reviews": {}, "posted_ids": ["2", "1"]}

        result = _new_reviews(reviews, state, initial_sync=False)

        self.assertEqual([item["id"] for item in result], ["3"])

    @patch.dict(os.environ, {"APPSTORE_APP_ID": "123"})
    @patch("providers.appstore.request_with_retries")
    def test_fetch_reviews_follows_pagination(self, request):
        page1 = Mock()
        page1.raise_for_status.return_value = None
        page1.json.return_value = {"data": [review("2"), review("1")], "links": {"next": "https://api/next"}}
        page2 = Mock()
        page2.raise_for_status.return_value = None
        page2.json.return_value = {"data": [review("0")], "links": {}}
        request.side_effect = [page1, page2]

        result = fetch_reviews("token")

        self.assertEqual(request.call_count, 2)
        self.assertEqual({item["id"] for item in result}, {"2", "1", "0"})

    @patch.dict(os.environ, {"APPSTORE_APP_ID": "123"})
    @patch("providers.appstore.request_with_retries")
    def test_fetch_reviews_stops_at_boundary(self, request):
        page1 = Mock()
        page1.raise_for_status.return_value = None
        page1.json.return_value = {"data": [review("5"), review("4")], "links": {"next": "https://api/next"}}
        request.side_effect = [page1]

        result = fetch_reviews("token", stop_at_id="4")

        self.assertEqual(request.call_count, 1)  # stopped without fetching page 2
        self.assertEqual({item["id"] for item in result}, {"5", "4"})

    def test_reply_candidates_ignore_bot_and_duplicate_messages(self):
        client = SlackClient(token="test-token", channel_id="C123")
        client.bot_user_id = "UBOT"
        messages = [
            {"ts": "1.0", "user": "UBOT", "text": "parent"},
            {"ts": "2.0", "user": "U1", "text": "reply"},
            {"ts": "2.0", "user": "U1", "text": "duplicate"},
            {"ts": "3.0", "bot_id": "BOTHER", "text": "bot"},
        ]

        result = _reply_candidates(messages, {"last_reply_ts": "1.5"}, client)

        self.assertEqual([message["ts"] for message in result], ["2.0"])

    def test_reply_candidates_ignore_system_messages(self):
        client = SlackClient(token="test-token", channel_id="C123")
        client.bot_user_id = "UBOT"
        messages = [
            {"ts": "2.0", "user": "U1", "text": "human"},
            {"ts": "3.0", "user": "U1", "subtype": "message_changed", "text": "edited"},
            {"ts": "4.0", "user": "U1", "subtype": "thread_broadcast", "text": "broadcast"},
        ]

        result = _reply_candidates(messages, {"last_reply_ts": "1.0"}, client)

        self.assertEqual([message["ts"] for message in result], ["2.0"])


if __name__ == "__main__":
    unittest.main()
