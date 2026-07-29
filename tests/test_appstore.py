import unittest

from providers.appstore import INITIAL_SYNC_COUNT, _new_reviews, _reply_candidates
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


if __name__ == "__main__":
    unittest.main()
