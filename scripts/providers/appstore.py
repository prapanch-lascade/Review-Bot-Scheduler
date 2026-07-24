import requests

from common.jwt_generator import generate_token
from common.slack_notifier import send_to_slack
from common.formatter import format_review


APPSTORE_APP_ID = __import__("os").environ["APPSTORE_APP_ID"]


def fetch_reviews(token: str) -> list:
    """
    Fetch customer reviews from App Store Connect API.
    """

    url = (
        f"https://api.appstoreconnect.apple.com/v1/"
        f"apps/{APPSTORE_APP_ID}/customerReviews"
    )

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    response = requests.get(url, headers=headers, timeout=30)

    response.raise_for_status()

    data = response.json()

    return data.get("data", [])


def run_appstore():

    print("Generating JWT...")

    token = generate_token()

    print("Fetching App Store reviews...")

    reviews = fetch_reviews(token)

    if not reviews:
        print("No reviews found.")
        return

    print(f"Found {len(reviews)} review(s).")

    #
    # Latest five reviews
    #

    latest_reviews = reviews[:5]

    for review in latest_reviews:

        message = format_review(review)

        send_to_slack(message)

        print(
            f"Sent Review -> "
            f"{review.get('id')}"
        )

    print("Completed.")