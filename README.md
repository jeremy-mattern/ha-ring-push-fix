# Ring Push Fix (temporary)

A small Home Assistant custom integration that restores **real-time Ring doorbell
and motion events** (`event.*_ding`, `event.*_motion`) until Home Assistant ships
the official fix.

## The problem

Since late October 2026, Ring's real-time events stop reaching Home Assistant.
Polled Ring sensors (last activity, battery, recordings) keep working, but the
event entities never update, and the log shows:

```
ERROR [firebase_messaging.fcmpushclient] Unknown error: Incorrect padding, shutting down FcmPushClient.
```

Ring started signing its Web Push messages with VAPID, so the `crypto-key`
header now arrives as `dh=<key>; p256ecdsa=<key>`. `firebase-messaging` 0.4.5
strips `dh=` by character position, keeps the `; p256ecdsa=...` tail, and the
key fails to decode. The message is never acknowledged, so it is redelivered and
the push client shuts down again on every reconnect. Reloading or re-adding the
Ring integration does not help.

Tracked in [home-assistant/core#189885](https://github.com/home-assistant/core/issues/189885).

## What this does

At startup it replaces `FcmPushClient._handle_data_message` in
`firebase-messaging` with a fixed version that combines two unmerged upstream
pull requests:

- [sdb9696/firebase-messaging#43](https://github.com/sdb9696/firebase-messaging/pull/43)
  (Vyacheslav Scherbinin): read `dh` / `salt` by name and restore base64url padding.
- [sdb9696/firebase-messaging#37](https://github.com/sdb9696/firebase-messaging/pull/37)
  (Dirk Nienhaus): skip a message that still cannot be decrypted instead of
  shutting the listener down.

It then reloads the Ring integration so its push listener restarts.

Nothing else in Home Assistant or Ring is modified. If the installed
`firebase-messaging` already contains the upstream fix, the patch does nothing
and logs that this integration can be removed.

## Install

1. In Home Assistant: **Settings → Marketplace → ⋮ → Custom repositories**, add
   `https://github.com/jeremy-mattern/ha-ring-push-fix` as an **Integration**.
2. Install **Ring Push Fix (temporary)** and restart Home Assistant.
3. **Settings → Devices & services → Add integration → Ring Push Fix**.

The log shows `Ring Push Fix: patched firebase-messaging FcmPushClient` when it
is active.

## Remove

Delete the integration entry, uninstall it from Marketplace, and restart, once a
Home Assistant release includes the upstream fix.

## Tests

```
pip install firebase-messaging==0.4.5 pytest pytest-asyncio
pytest -o asyncio_mode=auto tests
```

The tests reproduce the crash against unpatched 0.4.5 and confirm the patched
handler decrypts VAPID-signed messages, reordered headers, the old
single-parameter format, and drops undecryptable messages without raising.
Test fixtures are from `firebase-messaging` (MIT).

## License

MIT. Portions adapted from `firebase-messaging` (MIT, © sdb9696 and contributors).
