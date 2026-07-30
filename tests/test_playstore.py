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

    def test_duplicate_reply_state_prevents_second_reply(self):
        from common.review_sync import sync_slack_replies

        slack = Mock()
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

        sync_slack_replies("playstore", state, slack, "google_reply_sent", send_reply)

        slack.replies.assert_not_called()
        send_reply.assert_not_called()


if __name__ == "__main__":
    unittest.main()
