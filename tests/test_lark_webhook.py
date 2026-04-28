import hashlib

from review_agent.lark.webhook import verify_v2_signature


def test_sig_happy_path():
    enc = "test_secret_xxxxxxx"
    body = b'{"foo":"bar"}'
    timestamp = "1714200000"
    nonce = "n123"
    h = hashlib.sha256()
    h.update(timestamp.encode())
    h.update(nonce.encode())
    h.update(enc.encode())
    h.update(body)
    sig = h.hexdigest()
    headers = {
        "X-Lark-Request-Timestamp": timestamp,
        "X-Lark-Request-Nonce": nonce,
        "X-Lark-Signature": sig,
    }
    assert verify_v2_signature(headers, body, enc)


def test_sig_bad():
    headers = {
        "X-Lark-Request-Timestamp": "1",
        "X-Lark-Request-Nonce": "n",
        "X-Lark-Signature": "0" * 64,
    }
    assert not verify_v2_signature(headers, b"{}", "key")


def test_sig_missing_headers():
    assert not verify_v2_signature({}, b"{}", "key")
