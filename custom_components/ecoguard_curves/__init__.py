"""The EcoGuard Curves integration."""
from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .api import CurvesAPIClient
from .const import (
    CONF_DOMAIN_CODE,
    CONF_MEASURING_POINT_ID,
    CONF_NODE_ID,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    CONF_UTILITIES,
    CONF_VAT_RATE,
    DEFAULT_VAT_RATE,
    DOMAIN,
)
from .coordinator import CurvesDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up EcoGuard Curves from a config entry."""
    hass.data.setdefault(DOMAIN, {})

    # Create API client
    client = CurvesAPIClient(
        hass,
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        entry.data[CONF_DOMAIN_CODE],
    )

    # Authenticate
    try:
        await client.authenticate()
    except Exception:
        _LOGGER.exception("Failed to authenticate with Curves API")
        return False

    # Get configuration
    node_id = entry.data.get(CONF_NODE_ID) or entry.options.get(CONF_NODE_ID)
    measuring_point_id = entry.data.get(CONF_MEASURING_POINT_ID) or entry.options.get(
        CONF_MEASURING_POINT_ID
    )
    update_interval = entry.data.get(CONF_UPDATE_INTERVAL, 300)
    # Get VAT rate, defaulting to 25% for Sweden if not set
    # Check data first, then options, allowing 0 as a valid value
    if CONF_VAT_RATE in entry.data:
        vat_rate = float(entry.data[CONF_VAT_RATE])
    elif CONF_VAT_RATE in entry.options:
        vat_rate = float(entry.options[CONF_VAT_RATE])
    else:
        vat_rate = DEFAULT_VAT_RATE

    # Get utilities, defaulting to electricity for backward compatibility
    utilities = (
        entry.options.get(CONF_UTILITIES) or entry.data.get(CONF_UTILITIES) or ["electricity"]
    )

    # Get currency, defaulting to SEK
    currency = entry.data.get("currency") or entry.options.get("currency", "SEK")

    # Data interval is hardcoded to hourly
    data_interval = "hour"

    # Create coordinator
    coordinator = CurvesDataUpdateCoordinator(
        hass,
        client,
        node_id,
        measuring_point_id,
        update_interval,
        data_interval,
        utilities,
        vat_rate,
        currency,
    )

    # Fetch initial data so we have data when the entities are added
    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    entry.async_on_unload(entry.add_update_listener(_async_update_options))

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def _async_update_options(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Handle options update by reloading the integration."""
    await hass.config_entries.async_reload(entry.entry_id)
