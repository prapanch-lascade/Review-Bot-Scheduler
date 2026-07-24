from common.utils import utc_to_ist, current_ist, stars

APP_NAME = "Airlines70 - All Flights Ticket"


def format_review(review: dict) -> str:
    attr = review["attributes"]

    rating = attr.get("rating", 0)
    title = attr.get("title", "")
    body = attr.get("body", "")
    reviewer = attr.get("reviewerNickname", "Anonymous")
    reviewed_on = utc_to_ist(attr["createdDate"])
    territory = attr.get("territory", "Unknown")
    version = attr.get("appVersionString", "Unknown")

    return f"""
⭐ *New App Review Received*

{stars(rating)} ({rating}/5)

📝 *Title*

{title}

💬 *Review*

{body}

👤 *Reviewer* : {reviewer}
🌍 *Country*  : {territory}
📱 *Version*  : {version}
📅 *Reviewed* : {reviewed_on}
🏪 *Platform* : Apple App Store

⏰ *Detected* : {current_ist()}

""".strip() + "\n\n"