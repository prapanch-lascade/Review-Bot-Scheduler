import unittest
from unittest.mock import patch

from common.slack_client import SlackApiError, SlackClient, SlackPermissionError


class Response:
    def __init__(self, status_code=200, data=None, headers=None):
        self.status_code = status_code
        self._data = data
        self.headers = headers or {}

    def json(self):
        return self._data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class SlackClientTests(unittest.TestCase):
    def test_permission_error_is_actionable_type(self):
        client = SlackClient(token="test-token", channel_id="C123")
        response = Response(data={"ok": False, "error": "not_allowed_token_type"})
        with patch("common.slack_client.request_with_retries", return_value=response):
            with self.assertRaises(SlackPermissionError):
                client.replies("123.456")

    def test_rate_limit_preserves_retry_after(self):
        client = SlackClient(token="test-token", channel_id="C123")
        response = Response(status_code=429, headers={"Retry-After": "60"})
        with patch("common.slack_client.request_with_retries", return_value=response):
            with self.assertRaises(SlackApiError) as context:
                client.replies("123.456")
        self.assertEqual(context.exception.error, "rate_limited")
        self.assertEqual(context.exception.retry_after, 60.0)

    def test_thread_replies_use_get_query_parameters(self):
        client = SlackClient(token="test-token", channel_id="C123")
        response = Response(data={"ok": True, "messages": [], "response_metadata": {}})
        with patch("common.slack_client.request_with_retries", return_value=response) as request:
            client.replies("123.456")

        self.assertEqual(request.call_args.args[0], "GET")
        self.assertEqual(request.call_args.kwargs["params"]["channel"], "C123")
        self.assertEqual(request.call_args.kwargs["params"]["ts"], "123.456")


if __name__ == "__main__":
    unittest.main()
