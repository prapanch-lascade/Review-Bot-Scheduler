import logging
import os
import time

import jwt


LOG = logging.getLogger(__name__)

# Apple rejects tokens older than 20 minutes.
APPSTORE_TOKEN_TTL_SECONDS = 20 * 60


def generate_token() -> str:
    """
    Generate a short-lived App Store Connect ES256 JWT.

    All credentials are read from the environment (supplied by the workflow's
    secrets). Nothing is hardcoded, and neither the key nor the token is ever
    logged.
    """

    key_id = os.environ["APPSTORE_API_KEY_ID"]
    issuer_id = os.environ["APPSTORE_ISSUER_ID"]
    private_key = os.environ["APPSTORE_API_PRIVATE_KEY"]

    now = int(time.time())

    payload = {
        "iss": issuer_id,
        "iat": now,
        "exp": now + APPSTORE_TOKEN_TTL_SECONDS,
        "aud": "appstoreconnect-v1",
    }

    headers = {
        "alg": "ES256",
        "kid": key_id,
        "typ": "JWT",
    }

    token = jwt.encode(
        payload,
        private_key,
        algorithm="ES256",
        headers=headers,
    )

    LOG.info(
        "Generated App Store Connect JWT (valid %d minutes)",
        APPSTORE_TOKEN_TTL_SECONDS // 60,
    )

    return token
