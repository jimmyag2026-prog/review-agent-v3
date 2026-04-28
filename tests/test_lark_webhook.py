import base64
import hashlib
import json
import os

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from fastapi.testclient import TestClient
from fastapi import FastAPI

from review_agent.lark.webhook import decrypt_aes, verify_v2_signature
from review_agent.routers.lark_webhook import make_router
from review_agent.tasks.queue import TaskQueue


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


def _aes_encrypt(plain: bytes, encrypt_key: str) -> str:
    """Lark AES-256-CBC: key=sha256(encrypt_key); iv=random 16; PKCS7 pad."""
    key = hashlib.sha256(encrypt_key.encode()).digest()
    iv = os.urandom(16)
    pad = 16 - (len(plain) % 16)
    plain = plain + bytes([pad]) * pad
    encryptor = Cipher(algorithms.AES(key), modes.CBC(iv)).encryptor()
    ct = encryptor.update(plain) + encryptor.finalize()
    return base64.b64encode(iv + ct).decode()


def test_decrypt_aes_roundtrip():
    enc_key = "test_encrypt_key_xxxxxxxxxxxxxxxxxx"
    payload = {"type": "url_verification", "challenge": "abc"}
    blob = _aes_encrypt(json.dumps(payload).encode(), enc_key)
    out = decrypt_aes(blob, enc_key)
    assert out == payload


def test_url_verification_plain_mode(tmp_storage):
    app = FastAPI()
    queue = TaskQueue(tmp_storage)
    app.include_router(make_router(tmp_storage, queue, encrypt_key="", verification_token=""))
    client = TestClient(app)
    r = client.post("/lark/webhook", json={"type": "url_verification", "challenge": "abc"})
    assert r.status_code == 200
    assert r.json() == {"challenge": "abc"}


def test_url_verification_encrypted_mode(tmp_storage):
    """Round-final missing_test #4: encrypted url_verification path."""
    enc_key = "TESTKEY_NEVER_USE_IN_PROD_xxxxxxx"  # any 32 char string

    plain = json.dumps({"type": "url_verification", "challenge": "encchall"}).encode()
    encrypted = _aes_encrypt(plain, enc_key)
    body = json.dumps({"encrypt": encrypted}).encode()

    timestamp = "1714200000"
    nonce = "n42"
    h = hashlib.sha256()
    h.update(timestamp.encode()); h.update(nonce.encode())
    h.update(enc_key.encode()); h.update(body)
    sig = h.hexdigest()

    app = FastAPI()
    queue = TaskQueue(tmp_storage)
    app.include_router(make_router(tmp_storage, queue,
                                    encrypt_key=enc_key, verification_token=""))
    client = TestClient(app)
    r = client.post("/lark/webhook", content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Lark-Request-Timestamp": timestamp,
                        "X-Lark-Request-Nonce": nonce,
                        "X-Lark-Signature": sig,
                    })
    assert r.status_code == 200, r.text
    assert r.json() == {"challenge": "encchall"}


def test_bad_signature_rejected(tmp_storage):
    enc_key = "TESTKEY_NEVER_USE_IN_PROD_xxxxxxx"
    body = json.dumps({"encrypt": "doesntmatter"}).encode()
    app = FastAPI()
    queue = TaskQueue(tmp_storage)
    app.include_router(make_router(tmp_storage, queue,
                                    encrypt_key=enc_key, verification_token=""))
    client = TestClient(app)
    r = client.post("/lark/webhook", content=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Lark-Request-Timestamp": "1",
                        "X-Lark-Request-Nonce": "n",
                        "X-Lark-Signature": "0" * 64,
                    })
    assert r.status_code == 401
