import asyncio
import logging

import voluptuous as vol
from aiohttp import ClientError, ClientResponseError
from async_timeout import timeout
from homeassistant import config_entries, core
from homeassistant.const import CONF_HOST, CONF_PORT
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import DEFAULT_PORT, DOMAIN, TIMEOUT

try:
    from custom_components.airtouch3.vzduch import Vzduch
except ImportError:
    # For local development
    from .vzduch import Vzduch

_LOGGER = logging.getLogger(__name__)

@config_entries.HANDLERS.register(DOMAIN)
class AirTouch3ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """AirTouch 3 config flow."""

    VERSION = 1
    CONNECTION_CLASS = config_entries.CONN_CLASS_LOCAL_POLL

    @core.callback
    def _async_get_entry(self, data):

        return self.async_create_entry(
            title=data[CONF_HOST],
            data={
                CONF_HOST: data[CONF_HOST],
                CONF_PORT: data.get(CONF_PORT)
            },
        )

    async def async_step_user(self, user_input=None):
        _LOGGER.debug("async_step_user")
        if user_input is None:
            return self.async_show_form(step_id="user", data_schema=self.schema)

        host = user_input[CONF_HOST]
        port = user_input[CONF_PORT]

        try:
            _LOGGER.debug("create_device")
            session = async_get_clientsession(self.hass)
            with timeout(TIMEOUT):
                _LOGGER.debug("Call vzduch")
                device = Vzduch(session, host, port, timeout)
                await device.async_update()
        except asyncio.TimeoutError:
            return self.async_show_form(
                step_id="user",
                data_schema=self.schema,
                errors={"base": "device_timeout"},
            )
        except ClientResponseError as ex:
            if ex.status == 403:  # Handle HTTPForbidden (403)
                return self.async_show_form(
                    step_id="user", data_schema=self.schema, errors={"base": "forbidden"},
                )
            else:
                _LOGGER.exception("HTTP error: %s", str(ex))
                return self.async_show_form(
                    step_id="user", data_schema=self.schema, errors={"base": "device_fail"},
                )
        except ClientError as ex:
            _LOGGER.exception(f"ClientError: {str(ex)}")
            return self.async_show_form(
                step_id="user", data_schema=self.schema, errors={"base": "device_fail"},
            )
        except (ValueError, TimeoutError) as ex:
            _LOGGER.exception(f"Error creating device: {str(ex)}")
            return self.async_show_form(
                step_id="user", data_schema=self.schema, errors={"base": "device_fail"},
            )

        _LOGGER.debug(f"Device with name AirTouch_{device.name} has been setup")

        return self._async_get_entry(user_input)

    async def create_device(self, host, port=DEFAULT_PORT):
        try:
            _LOGGER.debug("create_device")
            session = async_get_clientsession(self.hass)
            with timeout(TIMEOUT):
                _LOGGER.debug("Call vzduch")
                device = Vzduch(session, host, port, timeout)
                await device.async_update()

        except asyncio.TimeoutError:
            return self.async_show_form(
                step_id="user",
                data_schema=self.schema,
                errors={"base": "device_timeout"},
            )
        except ClientResponseError as ex:
            if ex.status == 403:  # This is equivalent to HTTPForbidden
                return self.async_show_form(
                    step_id="user", data_schema=self.schema, errors={"base": "forbidden"},
                )
            else:
                _LOGGER.exception("HTTP error: %s", str(ex))
                return self.async_show_form(
                    step_id="user", data_schema=self.schema, errors={"base": "device_fail"},
                )
        except ClientError as ex:
            _LOGGER.exception(f"ClientError: {str(ex)}")
            return self.async_show_form(
                step_id="user", data_schema=self.schema, errors={"base": "device_fail"},
            )
        except (ValueError, TimeoutError) as ex:
            _LOGGER.exception(f"Error creating device: {str(ex)}")
            return self.async_show_form(
                step_id="user", data_schema=self.schema, errors={"base": "device_fail"},
            )

        return self._async_get_entry({
            CONF_HOST: host,
            CONF_PORT: port
        })

    @property
    def schema(self):
        """Return current schema."""
        return vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Optional(CONF_PORT): int
            }
        )