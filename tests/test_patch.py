"""Tests for the runtime patch, run against an unpatched firebase-messaging 0.4.5.

Uses the fixtures and the VAPID regression scenario from upstream PR #43.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from base64 import urlsafe_b64decode
from pathlib import Path

import pytest
from cryptography.hazmat.backends import default_backend
from cryptography.hazmat.primitives.serialization import load_der_private_key
from google.protobuf.json_format import Parse
from http_ece import encrypt

from firebase_messaging import FcmPushClient, FcmRegisterConfig
from firebase_messaging.proto.mcs_pb2 import DataMessageStanza

FIXTURES = Path(__file__).parent / "fixtures"
PATCH_FILE = (
    Path(__file__).parents[1] / "custom_components" / "ring_push_fix" / "patch.py"
)

spec = importlib.util.spec_from_file_location("ring_push_fix_patch", PATCH_FILE)
patch = importlib.util.module_from_spec(spec)
sys.modules["ring_push_fix_patch"] = patch
spec.loader.exec_module(patch)

SENDER_PUB = "BAGEFtID7WlmwzQ9pbjdRYAhfPe7Z8lA3ZGIPUh0SE3ikoY2PIrWUP0rmhpE4Kl8ImgMUDjKWrz0WmtLxORIHuw"
SENDER_PRI = "MIGHAgEAMBMGByqGSM49AgEGCCqGSM49AwEHBG0wawIBAQQgwSUpDfIqdJG3XVkn7t1GExHuW3gsqD4-J525w-rnCIihRANCAAQBhBbSA-1pZsM0PaW43UWAIXz3u2fJQN2RiD1IdEhN4pKGNjyK1lD9K5oaROCpfCJoDFA4ylq89FprS8TkSB7s"


def _load_msg():
    with open(FIXTURES / "data_message_stanza.json") as f:
        return Parse(f.read(), DataMessageStanza())


def _creds():
    with open(FIXTURES / "credentials.json") as f:
        return json.load(f)


def _get(msg, key):
    for x in msg.app_data:
        if x.key == key:
            return x.value
    return None


def _set(msg, key, value):
    for x in msg.app_data:
        if x.key == key:
            x.value = value


def _vapid_message(crypto_key_header: str):
    """Build a message encrypted for the fixture credentials with a given header."""
    dms = _load_msg()
    creds = _creds()
    salt = urlsafe_b64decode(_get(dms, "encryption")[5:].encode("ascii"))
    sender_priv = load_der_private_key(
        urlsafe_b64decode(SENDER_PRI.encode("ascii") + b"========"),
        password=None,
        backend=default_backend(),
    )
    dms.raw_data = encrypt(
        b'{ "foo" : "bar" }',
        salt=salt,
        private_key=sender_priv,
        dh=urlsafe_b64decode(creds["keys"]["public"].encode("ascii") + b"="),
        version="aesgcm",
        auth_secret=urlsafe_b64decode(creds["keys"]["secret"].encode("ascii") + b"========"),
    )
    _set(dms, "crypto-key", crypto_key_header)
    return dms, creds


def _client(creds, received):
    return FcmPushClient(
        lambda n, pid, obj: received.append(n),
        FcmRegisterConfig("project-1234", "bar", "foobar", "foobar"),
        creds,
        None,
    )


@pytest.fixture(autouse=True)
def _restore():
    yield
    patch.remove_patch()


async def test_unpatched_library_fails_on_vapid_header():
    """Reproduce the bug: 0.4.5 crashes on dh=...; p256ecdsa=... headers."""
    dms, creds = _vapid_message(f"dh={SENDER_PUB}; p256ecdsa={SENDER_PUB}")
    received = []
    with pytest.raises(ValueError):
        _client(creds, received)._handle_data_message(dms)
    assert received == []


async def test_patched_decrypts_vapid_header():
    assert patch.apply_patch() == "patched"
    dms, creds = _vapid_message(f"dh={SENDER_PUB}; p256ecdsa={SENDER_PUB}")
    received = []
    _client(creds, received)._handle_data_message(dms)
    assert received == [{"foo": "bar"}]


async def test_patched_handles_reordered_params():
    patch.apply_patch()
    dms, creds = _vapid_message(f"p256ecdsa={SENDER_PUB};dh={SENDER_PUB}")
    received = []
    _client(creds, received)._handle_data_message(dms)
    assert received == [{"foo": "bar"}]


async def test_patched_still_handles_plain_header():
    """The old single-parameter format keeps working."""
    patch.apply_patch()
    dms, creds = _vapid_message(f"dh={SENDER_PUB}")
    received = []
    _client(creds, received)._handle_data_message(dms)
    assert received == [{"foo": "bar"}]


async def test_patched_skips_garbage_instead_of_raising():
    """A message that still cannot be decrypted is dropped, not fatal (PR #37)."""
    patch.apply_patch()
    dms, creds = _vapid_message(f"dh={SENDER_PUB}")
    dms.raw_data = b"not encrypted"
    received = []
    _client(creds, received)._handle_data_message(dms)  # must not raise
    assert received == []


def test_apply_is_idempotent_and_removable():
    assert patch.apply_patch() == "patched"
    assert patch.apply_patch() == "already"
    assert patch.remove_patch() is True
    assert patch.apply_patch() == "patched"


@pytest.mark.parametrize(
    ("header", "name", "expected"),
    [
        ("dh=AAAA", "dh", "AAAA"),
        ("dh=AAAA; p256ecdsa=CCCC", "dh", "AAAA"),
        ("p256ecdsa=CCCC;dh=AAAA", "dh", "AAAA"),
        ("dh=AAA", "dh", "AAA="),
        ("salt=BB", "salt", "BB=="),
        ("AAAA", "dh", "AAAA"),
    ],
)
def test_header_param(header, name, expected):
    assert patch.webpush_header_param(header, name) == expected
