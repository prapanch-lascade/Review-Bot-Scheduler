from datetime import datetime
from zoneinfo import ZoneInfo


IST = ZoneInfo("Asia/Kolkata")


def utc_to_ist(date_string: str) -> str:
    """
    Convert App Store UTC datetime to IST.
    """

    utc = datetime.fromisoformat(
        date_string.replace("Z", "+00:00")
    )

    ist = utc.astimezone(IST)

    return ist.strftime("%d %b %Y, %I:%M %p IST")


def current_ist() -> str:
    """
    Current IST.
    """

    return datetime.now(IST).strftime(
        "%d %b %Y, %I:%M %p IST"
    )


def stars(rating: int) -> str:
    """
    Convert 5 -> ⭐⭐⭐⭐⭐
    """

    return "⭐" * rating