import unittest
from unittest.mock import Mock, patch

from providers.playstore import (
    _has_developer_reply,
    _prepare_reply,
    _review_id,
    _split_title_body,
    _timestamp_value,
    fetch_reviews,
    format_review,
    reply_to_review,
)


def review(review_id="play-1"):
    return {
        "reviewId": review_id,
        "authorName": "A reviewer",
        "comments": [
            {
                "userComment": {
                    "text": "Great title\tExcellent app",
                    "starRating": 5,
                    "reviewerLanguage": "en-IN",
                    "appVersionName": "2.3.1",
                    "lastModified": {"seconds": "1700000000", "nanos": 0},
                }
            }
        ],
    }


class PlayStoreTests(unittest.TestCase):
    def test_documented_title_body_separator_is_supported(self):
        self.assertEqual(_split_title_body("Title\tBody"), ("Title", "Body"))
        self.assertIn("Google Play", format_review(review()))

    def test_timestamp_shape_is_supported(self):
        self.assertEqual(_timestamp_value({"seconds": "10", "nanos": 500000000}), 10.5)

    def test_optional_fields_and_empty_review_are_safe(self):
        empty_review = {
            "reviewId": "play-empty",
            "comments": [{"userComment": {"text": None, "starRating": None}}],
        }
        message = format_review(empty_review)

        self.assertIn("No Title", message)
        self.assertIn("No review text provided.", message)
        self.assertIn("(0/5)", message)
        self.assertIn("Anonymous", message)
        self.assertIn("Unknown", message)

    def test_developer_comment_is_not_used_as_review_text(self):
        review_with_reply = review()
        review_with_reply["comments"].append(
            {"developerComment": {"text": "Private developer response"}}
        )

        message = format_review(review_with_reply)

        self.assertTrue(_has_developer_reply(review_with_reply))
        self.assertNotIn("Private developer response", message)

    def test_review_id_is_taken_directly_from_api_resource(self):
        self.assertEqual(_review_id(review("actual-api-id")), "actual-api-id")

    def test_reply_is_truncated_to_documented_limit(self):
        prepared = _prepare_reply("x" * 400, "play-1")

        self.assertEqual(len(prepared), 350)
        self.assertEqual(prepared, "x" * 350)

    @patch("providers.playstore._package_name", return_value="com.example.app")
    @patch("providers.playstore.request_with_retries")
    def test_invalid_review_schema_is_skipped(self, request, package_name):
        response = Mock(ok=True, status_code=200, text="")
        response.json.return_value = {
            "reviews": [
                {"reviewId": "bad", "comments": [{"userComment": {"starRating": 6}}]},
                review("valid"),
            ]
        }
        request.return_value = response

        result = fetch_reviews(Mock(token="access-token"))

        self.assertEqual([item["reviewId"] for item in result], ["valid"])

    @patch("providers.playstore._package_name", return_value="com.example.app")
    @patch("providers.playstore.request_with_retries")
    def test_reply_uses_official_endpoint_and_payload(self, request, package_name):
        response = Mock()
        response.json.return_value = {"result": {"replyText": "Thanks"}}
        request.return_value = response
        credentials = Mock(token="access-token")

        reply_to_review(credentials, "play-1", "Thanks")

        request.assert_called_once()
        args, kwargs = request.call_args
        self.assertEqual(args[0], "POST")
        self.assertTrue(args[1].endswith("/applications/com.example.app/reviews/play-1:reply"))
        self.assertEqual(kwargs["json"], {"replyText": "Thanks"})

    def test_no_new_reply_does_not_call_provider(self):
        from common.review_sync import sync_slack_replies

        slack = Mock()
        slack.is_human_message.return_value = False
        send_reply = Mock()
        state = {
            "reviews": {
                "play-1": {
                    "slack_ts": "123.456",
                    "last_reply_ts": "123.500",
                    "google_reply_sent": True,
                }
            }
        }
        slack.replies.return_value = [
            {"ts": "123.456", "user": "UBOT", "text": "review"},
        ]

        sync_slack_replies("playstore", state, slack, "google_reply_sent", send_reply)

        slack.replies.assert_called_once_with("123.456")
        send_reply.assert_not_called()

    def test_second_reply_replaces_first_and_third_reply_is_latest(self):
        from common.review_sync import sync_slack_replies

        slack = Mock()
        slack.is_human_message.side_effect = lambda message: message.get("user") == "U1"
        send_reply = Mock()
        state = {"reviews": {"play-1": {"slack_ts": "123.456"}}}

        slack.replies.return_value = [
            {"ts": "123.456", "user": "UBOT", "text": "review"},
            {"ts": "123.500", "user": "U1", "text": "Reply 1"},
            {"ts": "123.600", "user": "U1", "text": "Reply 2"},
            {"ts": "123.700", "user": "U1", "text": "Reply 3"},
        ]

        sync_slack_replies("playstore", state, slack, "google_reply_sent", send_reply)

        send_reply.assert_called_once_with("play-1", "Reply 3")
        self.assertEqual(state["reviews"]["play-1"]["last_reply_ts"], "123.700")
        self.assertTrue(state["reviews"]["play-1"]["last_sent_reply_hash"])

    def test_identical_newest_reply_is_skipped_without_provider_call(self):
        from common.review_sync import reply_hash, sync_slack_replies

        slack = Mock()
        slack.is_human_message.side_effect = lambda message: message.get("user") == "U1"
        send_reply = Mock()
        state = {
            "reviews": {
                "play-1": {
                    "slack_ts": "123.456",
                    "last_reply_ts": "123.500",
                    "last_sent_reply_hash": reply_hash("Already sent"),
                    "google_reply_sent": True,
                }
            }
        }
        slack.replies.return_value = [
            {"ts": "123.456", "user": "UBOT", "text": "review"},
            {"ts": "123.500", "user": "U1", "text": "older"},
            {"ts": "123.600", "user": "U1", "text": "Already sent"},
        ]

        sync_slack_replies("playstore", state, slack, "google_reply_sent", send_reply)

        send_reply.assert_not_called()
        self.assertEqual(state["reviews"]["play-1"]["last_reply_ts"], "123.600")

    def test_failed_provider_update_does_not_change_state(self):
        from common.review_sync import sync_slack_replies

        slack = Mock()
        slack.is_human_message.side_effect = lambda message: message.get("user") == "U1"
        send_reply = Mock(side_effect=RuntimeError("provider unavailable"))
        state = {"reviews": {"play-1": {"slack_ts": "123.456"}}}
        slack.replies.return_value = [
            {"ts": "123.456", "user": "UBOT", "text": "review"},
            {"ts": "123.600", "user": "U1", "text": "Reply"},
        ]

        with self.assertRaises(RuntimeError):
            sync_slack_replies("playstore", state, slack, "google_reply_sent", send_reply)

        self.assertNotIn("last_reply_ts", state["reviews"]["play-1"])
        self.assertNotIn("last_sent_reply_hash", state["reviews"]["play-1"])
        self.assertNotIn("google_reply_sent", state["reviews"]["play-1"])


if __name__ == "__main__":
    unittest.main()
