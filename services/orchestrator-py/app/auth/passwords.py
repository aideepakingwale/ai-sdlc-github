"""scrypt password hashing — format-compatible with the Node implementation:
`scrypt$N$r$p$<salt-hex>$<key-hex>` so existing local-mode rows keep working."""

from __future__ import annotations

import hashlib
import hmac
import secrets

N, R, P, KEYLEN = 2**15, 8, 1, 32


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    key = hashlib.scrypt(password.encode(), salt=salt, n=N, r=R, p=P, dklen=KEYLEN, maxmem=128 * N * R * 2)
    return f"scrypt${N}${R}${P}${salt.hex()}${key.hex()}"


def verify_password(password: str, stored: str) -> bool:
    parts = stored.split("$")
    if len(parts) != 6 or parts[0] != "scrypt":
        return False
    try:
        n, r, p = int(parts[1]), int(parts[2]), int(parts[3])
        salt, expected = bytes.fromhex(parts[4]), bytes.fromhex(parts[5])
        key = hashlib.scrypt(
            password.encode(), salt=salt, n=n, r=r, p=p, dklen=len(expected), maxmem=128 * n * r * 2
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(key, expected)
