import os
import requests


SLACK_WEBHOOK = os.environ["SLACK_WEBHOOK"]


def send_to_slack(message: str):

    payload = {
        "text": message
    }

    response = requests.post(
        SLACK_WEBHOOK,
        json=payload,
        timeout=30,
    )

    response.raise_for_status()

    print("Slack message sent.")