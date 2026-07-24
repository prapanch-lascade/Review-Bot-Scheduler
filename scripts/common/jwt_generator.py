import os
import time
import jwt


def generate_token() -> str:
    """
    Generate App Store Connect JWT.
    """

    key_id = os.environ["APPSTORE_API_KEY_ID"]
    issuer_id = os.environ["APPSTORE_ISSUER_ID"]
    private_key = os.environ["APPSTORE_API_PRIVATE_KEY"]

    now = int(time.time())

    payload = {
        "iss": issuer_id,
        "iat": now,
        "exp": now + (20 * 60),  # 20 minutes
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

    return token