from datetime import datetime
import logging
import time
from zoneinfo import ZoneInfo

import requests


IST = ZoneInfo("Asia/Kolkata")
LOG = logging.getLogger(__name__)


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


def request_with_retries(method: str, url: str, **kwargs) -> requests.Response:
    """Retry safe transient failures without blindly replaying ambiguous writes."""
    retries = kwargs.pop("retries", 3)
    backoff = kwargs.pop("backoff", 1.0)
    retry_network_errors = kwargs.pop("retry_network_errors", True)
    retry_server_errors = kwargs.pop("retry_server_errors", True)
    operation = kwargs.pop("operation", f"{method} {url}")

    for attempt in range(retries + 1):
        try:
            response = requests.request(method, url, **kwargs)
        except requests.RequestException:
            if not retry_network_errors or attempt == retries:
                raise
            LOG.warning("Transient network error during %s; retry %d/%d", operation, attempt + 1, retries)
            time.sleep(backoff * (2 ** attempt))
            continue

        if response.status_code == 429:
            if attempt == retries:
                return response
            retry_after = response.headers.get("Retry-After")
            try:
                delay = max(0.0, float(retry_after)) if retry_after else backoff * (2 ** attempt)
            except ValueError:
                delay = backoff * (2 ** attempt)
            LOG.warning(
                "Rate limited during %s; retrying in %.1f seconds (%d/%d)",
                operation,
                delay,
                attempt + 1,
                retries,
            )
            time.sleep(delay)
            continue

        if response.status_code >= 500 and retry_server_errors:
            if attempt == retries:
                return response
            LOG.warning("Server error %s during %s; retry %d/%d", response.status_code, operation, attempt + 1, retries)
            time.sleep(backoff * (2 ** attempt))
            continue

        return response

    raise RuntimeError("HTTP request retry loop exited unexpectedly")
