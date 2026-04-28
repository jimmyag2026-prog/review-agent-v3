"""Lark webhook signature verification + AES decrypt.

Spec: https://open.feishu.cn/document/server-side/event-subscription-guide/event-subscription-configure-/encrypt-key-encryption-configuration-case
- v2 signature = sha256_hex( timestamp + nonce + encrypt_key + raw_body_bytes )
- Body MUST be the raw bytes received over the wire — never re-serialize.
"""
from __future__ import annotations

import base64
import hashlib
import json
from typing import Mapping

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


def verify_v2_signature(
    headers: Mapping[str, str],
    raw_body: bytes,
    encrypt_key: str,
) -> bool:
    timestamp = headers.get("X-Lark-Request-Timestamp") or headers.get("x-lark-request-timestamp")
    nonce = headers.get("X-Lark-Request-Nonce") or headers.get("x-lark-request-nonce")
    sig = headers.get("X-Lark-Signature") or headers.get("x-lark-signature")
    if not (timestamp and nonce and sig and encrypt_key):
        return False
    h = hashlib.sha256()
    h.update(timestamp.encode())
    h.update(nonce.encode())
    h.update(encrypt_key.encode())
    h.update(raw_body)
    return _consttime_eq(h.hexdigest(), sig)


def _consttime_eq(a: str, b: str) -> bool:
    if len(a) != len(b):
        return False
    out = 0
    for x, y in zip(a, b):
        out |= ord(x) ^ ord(y)
    return out == 0


def decrypt_aes(encrypted_b64: str, encrypt_key: str) -> dict:
    """Lark AES-256-CBC: key = sha256(encrypt_key); iv = first 16 bytes of ciphertext."""
    key = hashlib.sha256(encrypt_key.encode()).digest()
    cipher_bytes = base64.b64decode(encrypted_b64)
    iv = cipher_bytes[:16]
    body = cipher_bytes[16:]
    decryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).decryptor()
    plain = decryptor.update(body) + decryptor.finalize()
    pad = plain[-1]
    plain = plain[:-pad]
    return json.loads(plain.decode("utf-8"))
