import requests

from common.jwt_generator import generate_token
from common.slack_notifier import send_to_slack
from common.formatter import format_review
from common.state_manager import (
    get_last_review_id,
    save_state,
)

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

    response = requests.get(
        url,
        headers=headers,
        timeout=30,
    )

    response.raise_for_status()

    data = response.json()

    reviews = data.get("data", [])

    #
    # Always sort newest first
    #
    reviews.sort(
        key=lambda review: review["attributes"]["createdDate"],
        reverse=True,
    )

    return reviews


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
    # Read previously processed review
    #

    last_review_id = get_last_review_id("appstore")

    print(f"Last Review ID : {last_review_id}")

    new_reviews = []

    #
    # First execution
    #

    if last_review_id is None:

        print("First execution detected.")

        new_reviews = reviews[:5]

    else:

        #
        # Collect reviews until we reach
        # the previously processed review.
        #

        for review in reviews:

            if review["id"] == last_review_id:
                break

            new_reviews.append(review)

    #
    # Nothing new
    #

    if not new_reviews:

        print("No new reviews.")

        return

    print(f"New Reviews : {len(new_reviews)}")

    #
    # Send oldest first
    #

    new_reviews.reverse()

    for review in new_reviews:

        try:

            message = format_review(review)

            send_to_slack(message)

            print(
                f"Sent Review -> {review['id']}"
            )

        except Exception as e:

            print(e)

            #
            # Don't update state if Slack fails.
            #

            return

    #
    # Update state with newest review
    #

    save_state(
        "appstore",
        reviews[0]["id"],
    )

    print(
        f"State Updated -> {reviews[0]['id']}"
    )

    print("Completed.")