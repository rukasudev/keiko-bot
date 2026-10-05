"""Files only the holder of Keiko's age private key can open."""
from typing import Optional

import pyrage
from pyrage import x25519

from app.constants import DBConfigs


def age_recipient(public_key: Optional[str]) -> Optional[x25519.Recipient]:
    """Who a file is sealed to; None without a valid age public key."""
    try:
        return x25519.Recipient.from_str((public_key or "").strip())
    except pyrage.RecipientError:
        return None


def seal(payload: bytes, recipient: x25519.Recipient) -> bytes:
    """`payload` encrypted to `recipient`, so only the matching private key opens it."""
    return pyrage.encrypt(payload, [recipient])


def plaintext_limit(limit: int) -> int:
    """The largest payload whose sealed file still fits in `limit` bytes."""
    chunks = -(-limit // DBConfigs.AGE_CHUNK_BYTES)
    return limit - DBConfigs.AGE_HEADER_BYTES - DBConfigs.AGE_TAG_BYTES * chunks
