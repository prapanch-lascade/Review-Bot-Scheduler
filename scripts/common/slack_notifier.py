"""Backward-compatible import surface for the Slack Web API client."""

from common.slack_client import SlackClient


def send_to_slack(message: str) -> str:
    return SlackClient().post_review(message)
