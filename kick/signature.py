"""
Kick webhook signature verification.

Kick signs webhook deliveries with RSA (asymmetric), not a shared HMAC
secret -- you verify using Kick's published PUBLIC key, you never hand
Kick a secret of your own. The public key is published at:
    https://docs.kick.com/apis/public-key

IMPORTANT -- verify before relying on this in production:
The headers below (names) are confirmed from Kick's own Go SDK field
names (glichtv/kick-sdk, which documents the exact header names Kick
uses). What is NOT independently confirmed here is the exact string
that gets signed -- `_signed_payload()` below assumes the common
"{message_id}.{timestamp}.{body}" pattern used by several other
webhook providers. Cross-check this against
https://docs.kick.com/events/webhook-security once you have a Kick
developer account -- if their docs specify a different concatenation
(e.g. body only, or a different separator), fix it in ONE place:
`_signed_payload()`. Everything else in this module is agnostic to
that detail.
"""

import base64
import os

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

HEADER_MESSAGE_ID = "Kick-Event-Message-Id"
HEADER_SUBSCRIPTION_ID = "Kick-Event-Subscription-Id"
HEADER_SIGNATURE = "Kick-Event-Signature"
HEADER_TIMESTAMP = "Kick-Event-Message-Timestamp"
HEADER_EVENT_TYPE = "Kick-Event-Type"
HEADER_EVENT_VERSION = "Kick-Event-Version"

_public_key_cache = None


def _load_public_key():
    """
    Loads Kick's RSA public key from the KICK_WEBHOOK_PUBLIC_KEY env var
    (PEM format, fetched once from https://docs.kick.com/apis/public-key
    and pasted into your Railway variables -- not fetched at runtime, so
    a Kick outage/change doesn't silently disable verification).
    """
    global _public_key_cache
    if _public_key_cache is None:
        pem = os.environ["KICK_WEBHOOK_PUBLIC_KEY"].encode()
        _public_key_cache = serialization.load_pem_public_key(pem)
    return _public_key_cache


def _signed_payload(message_id: str, timestamp: str, raw_body: bytes) -> bytes:
    """
    ASSUMPTION FLAGGED IN MODULE DOCSTRING -- confirm against Kick's docs.
    """
    return f"{message_id}.{timestamp}.".encode() + raw_body


def verify_kick_signature(headers: dict, raw_body: bytes) -> bool:
    """
    Returns True only if the request is authentically from Kick.
    Never process a webhook body before this returns True.
    """
    message_id = headers.get(HEADER_MESSAGE_ID)
    timestamp = headers.get(HEADER_TIMESTAMP)
    signature_b64 = headers.get(HEADER_SIGNATURE)

    if not (message_id and timestamp and signature_b64):
        return False

    try:
        signature = base64.b64decode(signature_b64)
    except (ValueError, TypeError):
        return False

    public_key = _load_public_key()
    payload = _signed_payload(message_id, timestamp, raw_body)

    try:
        public_key.verify(
            signature,
            payload,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return True
    except InvalidSignature:
        return False
