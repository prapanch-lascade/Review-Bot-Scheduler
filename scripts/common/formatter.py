from common.utils import (
    utc_to_ist,
    current_ist,
    stars,
)


APP_NAME = "Airlines70 - All Flights Ticket"


def format_review(review: dict) -> str:

    attr = review["attributes"]

    rating = attr.get("rating", 0)

    title = attr.get("title", "")

    body = attr.get("body", "")

    reviewer = attr.get("reviewerNickname", "Anonymous")

    reviewed_on = utc_to_ist(
        attr["createdDate"]
    )

    territory = attr.get(
        "territory",
        "Unknown",
    )

    version = attr.get(
        "appVersionString",
        "Unknown",
    )

    message = f"""
⭐ *New App Review Received*

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

📱 *Platform*      : iOS
📦 *App*           : {APP_NAME}
🌍 *Country*       : {territory}
⭐ *Rating*        : {stars(rating)} ({rating}/5)

👤 *Reviewer*      : {reviewer}
📅 *Reviewed On*   : {reviewed_on}

📝 *Title*
{title}

💬 *Review*
> {body}

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

🆔 *Review ID*     : {review["id"]}
🔄 *Version*       : {version}
📢 *Source*        : Apple App Store

⏰ *Detected At*   : {current_ist()}
"""

    return message.strip()