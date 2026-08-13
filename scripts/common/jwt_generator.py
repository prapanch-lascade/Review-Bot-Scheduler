import time
import jwt

# ==============================
# Apple App Store Connect Config
# ==============================

KEY_ID = "A6YCMFP8CT"
ISSUER_ID = "0b7d1bed-bcc3-4d32-b7df-d70367f6481f"

PRIVATE_KEY = '''-----BEGIN PRIVATE KEY-----
MIGTAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBHkwdwIBAQQgidLFArpoYHZiX+3J
Zi19Dxx5SymhxPgQULhKvFx/sXGgCgYIKoZIzj0DAQehRANCAAQHau0M5jOHydmD
V3jqJUfWFRRyK6Z4qUBezVoIHdnJ3tukpJq/DLbj7zInd3giLfahjCRH+YfjSFqb
5dafrZG5
-----END PRIVATE KEY-----'''

# ==============================
# Generate JWT
# ==============================

now = int(time.time())

payload = {
    "iss": ISSUER_ID,
    "iat": now,
    "exp": now + (20 * 60),  # Token valid for 20 minutes
    "aud": "appstoreconnect-v1",
}

headers = {
    "alg": "ES256",
    "kid": KEY_ID,
    "typ": "JWT",
}

token = jwt.encode(
    payload,
    PRIVATE_KEY,
    algorithm="ES256",
    headers=headers,
)

print("\nJWT Token:\n")
print(token)



#  key_id = 'A6YCMFP8CT'
#     issuer_id = '0b7d1bed-bcc3-4d32-b7df-d70367f6481f'
#     private_key = '''-----BEGIN PRIVATE KEY-----
# MIGTAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBHkwdwIBAQQgidLFArpoYHZiX+3J
# Zi19Dxx5SymhxPgQULhKvFx/sXGgCgYIKoZIzj0DAQehRANCAAQHau0M5jOHydmD
# V3jqJUfWFRRyK6Z4qUBezVoIHdnJ3tukpJq/DLbj7zInd3giLfahjCRH+YfjSFqb
# 5dafrZG5
# -----END PRIVATE KEY-----'''