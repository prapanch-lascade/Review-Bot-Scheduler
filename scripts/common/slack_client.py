import logging
import os

from common.utils import request_with_retries


LOG = logging.getLogger(__name__)
SLACK_API = "https://slack.com/api"


class SlackApiError(RuntimeError):
    def __init__(self, method: str, error: str, retry_after: float | None = None):
        self.method = method
        self.error = error
        self.retry_after = retry_after
        super().__init__(f"Slack {method} failed: {error}")


class SlackPermissionError(SlackApiError):
    pass


class SlackThreadNotFoundError(SlackApiError):
    pass


class SlackClient:
    def __init__(self, token: str | None = None, channel_id: str | None = None):
        self.token = token or os.environ["SLACK_BOT_TOKEN"]
        self.channel_id = channel_id or os.environ["SLACK_CHANNEL_ID"]
        self.bot_user_id = None

    def _call(self, method: str, payload: dict, http_method: str = "POST") -> dict:
        request_kwargs = {
            "headers": {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json; charset=utf-8"},
            "timeout": 30,
            "retry_network_errors": http_method == "GET" or method != "chat.postMessage",
            "retry_server_errors": http_method == "GET" or method != "chat.postMessage",
            "operation": f"Slack {method}",
        }
        if http_method == "GET":
            request_kwargs["params"] = payload
        else:
            request_kwargs["json"] = payload

        response = request_with_retries(
            http_method,
            f"{SLACK_API}/{method}",
            **request_kwargs,
        )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                retry_seconds = float(retry_after) if retry_after else None
            except ValueError:
                retry_seconds = None
            raise SlackApiError(method, "rate_limited", retry_seconds)
        response.raise_for_status()
        try:
            data = response.json()
        except ValueError as exc:
            raise SlackApiError(method, "invalid_json_response") from exc
        if not isinstance(data, dict):
            raise SlackApiError(method, "invalid_response_shape")
        if not data.get("ok"):
            error = str(data.get("error", "unknown_error"))
            if error in {"not_allowed_token_type", "missing_scope", "no_permission"}:
                raise SlackPermissionError(method, error)
            if error == "thread_not_found":
                raise SlackThreadNotFoundError(method, error)
            retry_after = response.headers.get("Retry-After")
            try:
                retry_seconds = float(retry_after) if retry_after else None
            except ValueError:
                retry_seconds = None
            if error in {"ratelimited", "rate_limited"}:
                raise SlackApiError(method, "rate_limited", retry_seconds)
            raise SlackApiError(method, error, retry_seconds)
        return data

    def identify_bot(self) -> None:
        try:
            user_id = self._call("auth.test", {}).get("user_id")
            if not user_id:
                raise SlackApiError("auth.test", "missing_user_id")
            self.bot_user_id = user_id
        except SlackApiError as exc:
            LOG.error("Could not identify Slack bot: %s", exc)
            raise

    def post_review(self, text: str) -> str:
        data = self._call("chat.postMessage", {"channel": self.channel_id, "text": text})
        ts = data.get("ts")
        if not ts:
            raise SlackApiError("chat.postMessage", "missing_ts")
        return ts

    def replies(self, thread_ts: str) -> list[dict]:
        messages = []
        cursor = None
        while True:
            payload = {"channel": self.channel_id, "ts": thread_ts, "limit": 15}
            if cursor:
                payload["cursor"] = cursor
            data = self._call("conversations.replies", payload, http_method="GET")
            page = data.get("messages", [])
            if not isinstance(page, list):
                raise SlackApiError("conversations.replies", "invalid_messages_shape")
            messages.extend(page)
            previous_cursor = cursor
            cursor = data.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                return messages
            if cursor == previous_cursor:
                raise SlackApiError("conversations.replies", "repeated_pagination_cursor")

    def is_bot_message(self, message: dict) -> bool:
        return bool(
            message.get("bot_id")
            or (self.bot_user_id and message.get("user") == self.bot_user_id)
        )

    def is_human_message(self, message: dict) -> bool:
        """Return whether a thread message is an ordinary human message."""
        if self.is_bot_message(message):
            return False
        if message.get("type") not in {None, "message"}:
            return False
        # Slack system and workflow events use a subtype. Ordinary user
        # messages do not, while bot_message is already covered above.
        if message.get("subtype"):
            return False
        return bool(message.get("user"))
