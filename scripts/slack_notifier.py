import os
import sys
import requests
from datetime import datetime


SLACK_WEBHOOK = os.getenv("SLACK_WEBHOOK")


def send_message(message: str):
    """
    Send a plain text message to Slack.
    """

    if not SLACK_WEBHOOK:
        raise RuntimeError(
            "SLACK_WEBHOOK secret is missing."
        )

    response = requests.post(
        SLACK_WEBHOOK,
        json={"text": message},
        timeout=15,
    )

    response.raise_for_status()


def main():
    current_time = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")

    message = f"""
✅ GitHub Scheduler Triggered

Time:
{current_time}

Status:
Workflow executed successfully.
"""

    send_message(message)

    print("Slack notification sent.")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(e)
        sys.exit(1)