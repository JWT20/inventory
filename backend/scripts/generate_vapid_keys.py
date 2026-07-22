"""Generate one standards-based VAPID key pair for Web Push configuration."""

import base64

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


private_key = ec.generate_private_key(ec.SECP256R1())
private_value = private_key.private_numbers().private_value.to_bytes(32, "big")
public_value = private_key.public_key().public_bytes(
    serialization.Encoding.X962,
    serialization.PublicFormat.UncompressedPoint,
)

print(f"VAPID_PUBLIC_KEY={_base64url(public_value)}")
print(f"VAPID_PRIVATE_KEY={_base64url(private_value)}")
