"""Platform for Airtouch3."""
import asyncio
import logging

from aiohttp import ClientConnectionError
from homeassistant.config_entries import SOURCE_IMPORT, ConfigEntry
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from . import config_flow  # noqa: F401
from .const import DOMAIN, TIMEOUT
from .smart_control import async_setup_services

try:
    from custom_components.airtouch3.vzduch import Vzduch
except ImportError:
    # For local development
    from .vzduch import Vzduch

_LOGGER = logging.getLogger(__name__)

COMPONENT_TYPES = ["climate", "sensor", "switch", "fan"]

async def async_setup(hass, config):
    """Connect to Airtouch3 Unit"""
    if DOMAIN not in config:
        return True

    host = config[DOMAIN].get(CONF_HOST)
    if not host:
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}
        )
    else:
        await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_IMPORT}, data={CONF_HOST: host}
        )

    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry):
    """Connect to Airtouch3 Unit"""
    conf = entry.data

    vzduch_api = await api_init(
        hass,
        conf[CONF_HOST],
        conf.get(CONF_PORT),
    )
    if not vzduch_api:
        return False
    hass.data.setdefault(DOMAIN, {}).update({entry.entry_id: vzduch_api})

    await hass.config_entries.async_forward_entry_setups(entry, COMPONENT_TYPES)

    # Setup smart control services
    await async_setup_services(hass)

    return True

async def async_unload_entry(hass, config_entry):
    """Unload a config entry."""
    await asyncio.wait(
        [
            hass.config_entries.async_forward_entry_unload(config_entry, component)
            for component in COMPONENT_TYPES
        ]
    )
    hass.data[DOMAIN].pop(config_entry.entry_id)
    if not hass.data[DOMAIN]:
        hass.data.pop(DOMAIN)
    return True

async def api_init(hass, host, port, timeout = TIMEOUT):
    """Init the Airtouch unit."""

    session = async_get_clientsession(hass)
    try:
        _LOGGER.debug(f"We have host {host} port {port}")
        device = Vzduch(session, host, port, timeout)
        await device.async_update()
    except asyncio.TimeoutError:
        _LOGGER.debug("Connection to %s timed out", host)
        raise ConfigEntryNotReady
    except ClientConnectionError:
        _LOGGER.debug("ClientConnectionError to %s", host)
        raise ConfigEntryNotReady
    except Exception as ex:  # pylint: disable=broad-except
        # Make the exception more specific by logging the actual error
        _LOGGER.error("Unexpected error creating device %s: %s", host, str(ex))
        return None

    return device