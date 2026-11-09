"""Runtime patch for firebase-messaging's FcmPushClient (no Home Assistant imports).

Ring (via python-ring-doorbell) receives doorbell/motion events as Web Push
messages through firebase-messaging. Since late Oct 2026 Ring signs those pushes
with VAPID, so the ``crypto-key`` header arrives as ``dh=<key>; p256ecdsa=<key>``.
firebase-messaging 0.4.5 strips the ``dh=`` prefix by position, keeps the
``; p256ecdsa=...`` tail, and fails to decode it ("Incorrect padding"). The
failure happens before the message is acknowledged, so the server redelivers it
and the push client shuts down again on every reconnect.

This module replaces ``FcmPushClient._handle_data_message`` with a version that
combines two unreleased upstream fixes:

* sdb9696/firebase-messaging PR #43 - read ``dh``/``salt`` by name and restore
  base64url padding.
* sdb9696/firebase-messaging PR #37 - skip a message that still cannot be
  decrypted instead of tearing down the listener.

The patch is a no-op if the installed library already contains the upstream fix.
"""

from __future__ import annotations

import json
import logging
from contextlib import suppress
from typing import Any

_LOGGER = logging.getLogger(__name__)

PATCH_MARKER = "_ring_push_fix_patched"
_ORIGINAL_ATTR = "_ring_push_fix_original_handle_data_message"


def b64_pad(value: str) -> str:
    """Restore the ``=`` padding stripped from a base64url value."""
    return value + "=" * (-len(value) % 4)


def webpush_header_param(header: str, name: str) -> str:
    """Return one parameter of a Web Push ``Crypto-Key``/``Encryption`` header.

    Port of ``_webpush_header_param`` from upstream PR #43: parameters are
    ``;``-separated and unordered, so look them up by name. A bare value with no
    ``name=`` prefix is accepted as-is. Padding is restored.
    """
    for segment in header.split(";"):
        param, sep, value = segment.strip().partition("=")
        if sep and param == name:
            return b64_pad(value.strip())

    bare = header.strip()
    # '=' only appears in base64url as trailing padding, so a header without an
    # inner '=' cannot be a parameter list.
    if "=" not in bare.rstrip("="):
        return b64_pad(bare)
    raise ValueError(f"no {name!r} parameter in header {header.split('=')[0]!r}")


def _patched_handle_data_message(self: Any, msg: Any) -> None:
    """Fixed copy of FcmPushClient._handle_data_message (0.4.5 + PR #43 + #37)."""
    from firebase_messaging import fcmpushclient as fpc  # noqa: PLC0415

    fpc._logger.debug(  # noqa: SLF001
        "Received data message Stream ID: %s, Last: %s, Status: %s",
        msg.stream_id,
        msg.last_stream_id_received,
        msg.status,
    )

    if (
        self._app_data_by_key(msg, "message_type", do_not_raise=True)
        == "deleted_messages"
    ):
        # The deleted_messages message does not contain data.
        return

    if not self.credentials:
        return

    try:
        crypto_key = webpush_header_param(
            self._app_data_by_key(msg, "crypto-key"), "dh"
        )
        salt = webpush_header_param(self._app_data_by_key(msg, "encryption"), "salt")
    except (RuntimeError, ValueError) as ex:
        self._log_warn_with_limit(
            "Skipping message %s with unreadable encryption headers: %s",
            msg.persistent_id,
            ex,
        )
        return

    subtype = self._app_data_by_key(msg, "subtype")
    if subtype != self.credentials["gcm"]["app_id"]:
        self._log_warn_with_limit(
            "Subtype %s in data message does not match"
            + "app id client was registered with %s",
            subtype,
            self.credentials["gcm"]["app_id"],
        )

    try:
        decrypted = self._decrypt_raw_data(
            self.credentials, crypto_key, salt, msg.raw_data
        )
    except ValueError as ex:
        # binascii.Error and http_ece decrypt failures are both ValueError.
        # Skip the message instead of shutting the listener down (PR #37).
        self._log_warn_with_limit(
            "Failed to decrypt data for message %s: %s", msg.persistent_id, ex
        )
        return

    decrypted_json = None
    with suppress(json.JSONDecodeError, ValueError):
        decrypted_json = json.loads(decrypted.decode("utf-8"))

    if not decrypted_json:
        self._log_warn_with_limit(
            "Failed to decrypt data for message %s", msg.persistent_id
        )

    ret_val = decrypted_json if decrypted_json else decrypted
    self._log_verbose("Data for message %s is: %s", msg.persistent_id, ret_val)
    try:
        if not isinstance(ret_val, dict):
            ret_val = {"message": ret_val}
        self.callback(ret_val, msg.persistent_id, self.callback_context)
        self._reset_error_count(fpc.ErrorType.NOTIFY)
    except Exception:  # noqa: BLE001
        fpc._logger.exception(  # noqa: SLF001
            "Unexpected exception calling notification callback\n"
        )
        self._try_increment_error_count(fpc.ErrorType.NOTIFY)


_REQUIRED = (
    "_handle_data_message",
    "_app_data_by_key",
    "_decrypt_raw_data",
    "_log_warn_with_limit",
    "_log_verbose",
    "_reset_error_count",
    "_try_increment_error_count",
)


def apply_patch() -> str:
    """Patch FcmPushClient. Returns 'patched', 'already', 'upstream' or 'incompatible'."""
    from firebase_messaging import fcmpushclient as fpc  # noqa: PLC0415

    cls = fpc.FcmPushClient
    if getattr(cls, PATCH_MARKER, False):
        return "already"
    if hasattr(fpc, "_webpush_header_param"):
        # The installed library already contains upstream PR #43.
        return "upstream"
    if not all(hasattr(cls, name) for name in _REQUIRED) or not hasattr(
        fpc, "ErrorType"
    ):
        return "incompatible"

    setattr(cls, _ORIGINAL_ATTR, cls._handle_data_message)  # noqa: SLF001
    cls._handle_data_message = _patched_handle_data_message  # noqa: SLF001
    setattr(cls, PATCH_MARKER, True)
    return "patched"


def remove_patch() -> bool:
    """Restore the original method. Returns True if something was restored."""
    from firebase_messaging import fcmpushclient as fpc  # noqa: PLC0415

    cls = fpc.FcmPushClient
    original = getattr(cls, _ORIGINAL_ATTR, None)
    if original is None:
        return False
    cls._handle_data_message = original  # noqa: SLF001
    delattr(cls, _ORIGINAL_ATTR)
    setattr(cls, PATCH_MARKER, False)
    return True
