from __future__ import annotations

import secrets
import time

_ULID_ALPHABET = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def ulid() -> str:
    """26-char ULID (Crockford base32). Lex-sortable by time."""
    ts_ms = int(time.time() * 1000)
    rand = secrets.token_bytes(10)
    out = []
    n = ts_ms
    for _ in range(10):
        out.append(_ULID_ALPHABET[n & 0x1F])
        n >>= 5
    ts_part = "".join(reversed(out))
    rb = int.from_bytes(rand, "big")
    rand_chars = []
    for _ in range(16):
        rand_chars.append(_ULID_ALPHABET[rb & 0x1F])
        rb >>= 5
    return ts_part + "".join(reversed(rand_chars))


def now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
