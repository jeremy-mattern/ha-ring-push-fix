"""Ring Push Fix: temporary runtime patch so Ring ding/motion events reach HA.

See patch.py for details. Remove this integration once Home Assistant ships a
firebase-messaging release that includes upstream PR #43; the patch detects that
and stops applying itself.
"""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.core import HomeAssistant

from .patch import apply_patch, remove_patch

DOMAIN = "ring_push_fix"
RING_DOMAIN = "ring"

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Patch firebase-messaging, then reload Ring so its listener restarts."""
    result = await hass.async_add_executor_job(apply_patch)

    if result == "upstream":
        _LOGGER.warning(
            "The installed firebase-messaging already includes the upstream "
            "fix; Ring Push Fix is not needed and can be removed"
        )
        return True
    if result == "incompatible":
        _LOGGER.error(
            "Ring Push Fix could not patch this firebase-messaging version "
            "(unexpected internals); nothing was changed"
        )
        return True

    _LOGGER.warning(
        "Ring Push Fix: patched firebase-messaging FcmPushClient "
        "(upstream PR #43 + #37) [%s]",
        result,
    )

    if result == "patched":
        # Ring's push client shuts itself down permanently after the bad
        # message, so restart it now that the handler is fixed.
        for ring_entry in hass.config_entries.async_entries(RING_DOMAIN):
            if ring_entry.state is ConfigEntryState.LOADED:
                _LOGGER.info("Reloading Ring entry %s", ring_entry.title)
                hass.config_entries.async_schedule_reload(ring_entry.entry_id)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Restore the original handler when the integration is removed."""
    if await hass.async_add_executor_job(remove_patch):
        _LOGGER.warning("Ring Push Fix: original firebase-messaging handler restored")
    return True
