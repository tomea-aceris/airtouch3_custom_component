"""Support for the Airthouch 3 Unit."""
import logging

try:
    from custom_components.airtouch3.vzduch import (
        Vzduch,
        AC_POWER_ON,
        AC_POWER_OFF,
        AC_FAN_MODE_QUIET,
        AC_FAN_MODE_LOW,
        AC_FAN_MODE_MEDIUM,
        AC_FAN_MODE_HIGH,
        AC_FAN_MODE_POWERFUL,
        AC_FAN_MODE_AUTO,
        AC_MODE_HEAT,
        AC_MODE_COOL,
        AC_MODE_FAN,
        AC_MODE_DRY,
        AC_MODE_AUTO
    )
except ImportError:
    # For local development
    from .vzduch import (
        Vzduch,
        AC_POWER_ON,
        AC_POWER_OFF,
        AC_FAN_MODE_QUIET,
        AC_FAN_MODE_LOW,
        AC_FAN_MODE_MEDIUM,
        AC_FAN_MODE_HIGH,
        AC_FAN_MODE_POWERFUL,
        AC_FAN_MODE_AUTO,
        AC_MODE_HEAT,
        AC_MODE_COOL,
        AC_MODE_FAN,
        AC_MODE_DRY,
        AC_MODE_AUTO
    )

from homeassistant.components.climate import ClimateEntity
from homeassistant.components.climate.const import (
    ATTR_FAN_MODE,
    ATTR_HVAC_MODE,
    HVACMode,
    HVACAction,
    ClimateEntityFeature,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature

from . import DOMAIN as AT3_DOMAIN
from .const import (
    FAN_QUIET,
    FAN_LOW,
    FAN_MEDIUM,
    FAN_HIGH,
    FAN_POWERFUL,
    FAN_AUTO
)

_LOGGER = logging.getLogger(__name__)

HA_STATE_TO_AT3 = {
    HVACMode.OFF :-1,
    HVACMode.HEAT: AC_MODE_HEAT,
    HVACMode.COOL: AC_MODE_COOL,
    HVACMode.FAN_ONLY: AC_MODE_FAN,
    HVACMode.DRY: AC_MODE_DRY,
    HVACMode.HEAT_COOL: AC_MODE_AUTO,
}

AT3_TO_HA_STATE = {
   -1: HVACMode.OFF,
    AC_MODE_HEAT: HVACMode.HEAT,
    AC_MODE_COOL: HVACMode.COOL,
    AC_MODE_FAN: HVACMode.FAN_ONLY,
    AC_MODE_DRY: HVACMode.DRY,
    AC_MODE_AUTO: HVACMode.HEAT_COOL
}

HA_STATE_TO_CURRENT_STATE = {
    HVACMode.OFF : HVACAction.OFF,
    HVACMode.HEAT: HVACAction.HEATING,
    HVACMode.COOL: HVACAction.COOLING,
    HVACMode.FAN_ONLY: HVACAction.IDLE,
    HVACMode.DRY: HVACAction.DRYING,
    HVACMode.HEAT_COOL: HVACAction.IDLE
}

HA_FAN_MODE_TO_AT3 = {
    FAN_QUIET : AC_FAN_MODE_QUIET,
    FAN_LOW : AC_FAN_MODE_LOW,
    FAN_MEDIUM : AC_FAN_MODE_MEDIUM,
    FAN_HIGH : AC_FAN_MODE_HIGH,
    FAN_POWERFUL : AC_FAN_MODE_POWERFUL,
    FAN_AUTO : AC_FAN_MODE_AUTO
}

AT3_TO_HA_FAN_MODE = {
    AC_FAN_MODE_QUIET: FAN_QUIET,
    AC_FAN_MODE_LOW: FAN_LOW,
    AC_FAN_MODE_MEDIUM: FAN_MEDIUM,
    AC_FAN_MODE_HIGH: FAN_HIGH,
    AC_FAN_MODE_POWERFUL: FAN_POWERFUL,
    AC_FAN_MODE_AUTO: FAN_AUTO
}

TEMPERATURE_PRECISION = 1
TARGET_TEMPERATURE_STEP = 1

SUPPORTED_FEATURES = \
    ClimateEntityFeature.TARGET_TEMPERATURE | \
    ClimateEntityFeature.FAN_MODE

CLIMATE_ICON = "mdi:home-variant-outline"

async def async_setup_entry(hass, entry, async_add_entities):
    """Set up AirTouch3 climate based on config_entry."""
    vzduch_api = hass.data[AT3_DOMAIN].get(entry.entry_id)
    _LOGGER.debug(f"[AT3Climate] Init {vzduch_api.name}")
    async_add_entities([AirTouch3Climate(vzduch_api)], update_before_add=True)

    async def handle_set_zone_temperature(call):
        """Handle the service call."""
        _LOGGER.debug(f"[AT3Climate.handle_set_zone_temperature] Call Data [{call}]")

        desired_temperature = call.data.get('temperature')
        if desired_temperature is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] Desired Temperature not specified")
            return

        if type(desired_temperature) != int:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] Desired Temperature must be a whole positive number")
            return

        if desired_temperature < 16 or desired_temperature >32:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] Desired Temperature out of range. Valid temperature range is 16 to 32 degrees {desired_temperature}")
            return

        entity_id = call.data.get('entity_id')
        if entity_id is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] entity_id not specified")
            return

        entity_item = hass.states.get(entity_id)
        if entity_item is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] Entity not found {entity_id}")
            return

        zone_id = entity_item.attributes['id']
        if zone_id is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] Entity does not have id attribute {entity_item}")
            return

        current_desired_temperature = entity_item.attributes['desired_temperature']
        if current_desired_temperature is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] Entity does not have desired_temperature attribute {entity_item}")
            return

        while desired_temperature != current_desired_temperature:
            current_desired_temperature = await vzduch_api.set_zone_temperature(zone_id, desired_temperature)
            _LOGGER.debug(f"current_desired_temperature {current_desired_temperature} {desired_temperature}")
            if current_desired_temperature is None:
                _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] Desired Temperature failed. Try again")
                break
            if current_desired_temperature == 0:
                _LOGGER.warning(f"[AT3Climate.handle_set_zone_temperature] Desired Temperature for zone id {zone_id} cannot be set. Zone is non temperature controlled")
                break

    async def handle_set_zone_damper(call):
        """Handle setting a zone's damper percentage."""
        _LOGGER.debug(f"[AT3Climate.handle_set_zone_damper] Call Data [{call}]")

        percentage = call.data.get('percentage')
        if percentage is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_damper] Percentage not specified")
            return

        if type(percentage) != int:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_damper] Percentage must be a whole positive number")
            return

        if percentage < 0 or percentage > 100:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_damper] Percentage out of range. Valid range is 0 to 100 percent: {percentage}")
            return

        entity_id = call.data.get('entity_id')
        if entity_id is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_damper] entity_id not specified")
            return

        entity_item = hass.states.get(entity_id)
        if entity_item is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_damper] Entity not found {entity_id}")
            return

        zone_id = entity_item.attributes.get('id')
        if zone_id is None:
            _LOGGER.warning(f"[AT3Climate.handle_set_zone_damper] Entity does not have id attribute {entity_item}")
            return

        await vzduch_api.set_zone_damper(zone_id, percentage)

    async def handle_zone_switch(call):
        """Handle switching a zone on or off."""
        _LOGGER.debug(f"[AT3Climate.handle_zone_switch] Call Data [{call}]")

        to_state = call.data.get('to_state')
        if to_state is None:
            _LOGGER.warning(f"[AT3Climate.handle_zone_switch] to_state not specified")
            return

        if type(to_state) != int or to_state not in [0, 1]:
            _LOGGER.warning(f"[AT3Climate.handle_zone_switch] to_state must be 0 (off) or 1 (on)")
            return

        entity_id = call.data.get('entity_id')
        if entity_id is None:
            _LOGGER.warning(f"[AT3Climate.handle_zone_switch] entity_id not specified")
            return

        entity_item = hass.states.get(entity_id)
        if entity_item is None:
            _LOGGER.warning(f"[AT3Climate.handle_zone_switch] Entity not found {entity_id}")
            return

        zone_id = entity_item.attributes.get('id')
        if zone_id is None:
            _LOGGER.warning(f"[AT3Climate.handle_zone_switch] Entity does not have id attribute {entity_item}")
            return

        await vzduch_api.zone_switch(zone_id, to_state)

    # Register custom handlers for turn_on and turn_off services
    async def handle_climate_turn_on(call):
        """Handle climate.turn_on service for AirTouch3 entities."""
        entity_ids = call.data.get("entity_id", [])
        if not entity_ids:
            return

        _LOGGER.debug(f"[AT3Climate] Custom climate.turn_on handler called for {entity_ids}")
        entities = [entity for entity in hass.data.get("climate", {}).entities
                    if entity.entity_id in entity_ids and isinstance(entity, AirTouch3Climate)]

        for entity in entities:
            await entity.async_turn_on()

    async def handle_climate_turn_off(call):
        """Handle climate.turn_off service for AirTouch3 entities."""
        entity_ids = call.data.get("entity_id", [])
        if not entity_ids:
            return

        _LOGGER.debug(f"[AT3Climate] Custom climate.turn_off handler called for {entity_ids}")
        entities = [entity for entity in hass.data.get("climate", {}).entities
                    if entity.entity_id in entity_ids and isinstance(entity, AirTouch3Climate)]

        for entity in entities:
            await entity.async_turn_off()

    # Register our custom handlers for the climate domain services
    hass.services.async_register("climate", "turn_on", handle_climate_turn_on)
    hass.services.async_register("climate", "turn_off", handle_climate_turn_off)
    hass.services.async_register(AT3_DOMAIN, "set_zone_temperature", handle_set_zone_temperature)
    hass.services.async_register(AT3_DOMAIN, "set_zone_damper", handle_set_zone_damper)
    hass.services.async_register(AT3_DOMAIN, "zone_switch", handle_zone_switch)


class AirTouch3Climate(ClimateEntity):
    """Representation of a AirTouch3 Unit."""

    def __init__(self, api):
        """Initialize"""
        self._api = api
        self._list = {
            ATTR_HVAC_MODE: list(HA_STATE_TO_AT3),
            ATTR_FAN_MODE: list(HA_FAN_MODE_TO_AT3)
        }
        self._supported_features = SUPPORTED_FEATURES

    @property
    def supported_features(self):
        """Return the list of supported features."""
        return self._supported_features

    @property
    def device_info(self):
        """Return a device description for device registry."""
        return self._api.device_info

    @property
    def icon(self):
        """Front End Icon"""
        return CLIMATE_ICON

    @property
    def name(self):
        """Return the name of the thermostat, if any."""
        return self._api.name

    @property
    def unique_id(self):
        """Return a unique ID."""
        return self._api.airtouch_id

    @property
    def temperature_unit(self):
        """Return the unit of measurement which this thermostat uses."""
        return UnitOfTemperature.CELSIUS

    @property
    def precision(self):
        """Return the precision of the temperature in the system."""
        return TEMPERATURE_PRECISION

    @property
    def current_temperature(self):
        """Return the current temperature."""
        return self._api.room_temperature

    @property
    def target_temperature(self):
        """Return the temperature we try to reach."""
        return self._api.desired_temperature

    @property
    def target_temperature_step(self):
        """Return the supported step of target temperature."""
        return TARGET_TEMPERATURE_STEP

    @property
    def hvac_action(self):
        """The current HVAC action (heating, cooling)"""
        if self._api.power == AC_POWER_OFF:
            return HVACAction.OFF
            
        ac_mode = self._api.mode
        return HA_STATE_TO_CURRENT_STATE.get(AT3_TO_HA_STATE.get(ac_mode, HVACMode.HEAT_COOL), HVACAction.IDLE)

    @property
    def hvac_mode(self):
        """Return current operation ie. heat, cool, idle. Used to determine state."""
        ac_mode = self._api.mode
        return AT3_TO_HA_STATE.get(ac_mode, HVACMode.HEAT_COOL)

    @property
    def hvac_modes(self):
        """Return the list of available operation modes."""
        return self._list.get(ATTR_HVAC_MODE)

    @property
    def fan_mode(self):
        """Return the fan setting."""
        ac_fan_mode = self._api.fan_mode
        return AT3_TO_HA_FAN_MODE.get(ac_fan_mode, FAN_LOW)

    @property
    def fan_modes(self):
        """List of available fan modes."""
        return self._list.get(ATTR_FAN_MODE)

    async def async_set_hvac_mode(self, hvac_mode):
        """Set HVAC mode."""
        _LOGGER.debug(f"[AT3Climate] async_set_hvac_mode called with {hvac_mode}")

        # If turning off, just use the power switch
        if hvac_mode == HVACMode.OFF:
            _LOGGER.debug("[AT3Climate] Turning AC OFF via power_switch")
            await self._api.power_switch(AC_POWER_OFF)
            return

        # If turning on, first turn on power if needed, then set the mode
        if self._api.power == AC_POWER_OFF:
            _LOGGER.debug(f"[AT3Climate] Turning AC ON via power_switch")
            await self._api.power_switch(AC_POWER_ON)

        # Set the mode (only for non-OFF modes)
        _LOGGER.debug(f"[AT3Climate] Setting AC mode to {hvac_mode}")
        await self._api.set_mode(HA_STATE_TO_AT3.get(hvac_mode))

    async def async_turn_on(self, **kwargs):
        """Turn the entity on."""
        _LOGGER.debug("[AT3Climate] async_turn_on called")
        if self._api.power == AC_POWER_OFF:
            await self._api.power_switch(AC_POWER_ON)

    async def async_turn_off(self, **kwargs):
        """Turn the entity off."""
        _LOGGER.debug("[AT3Climate] async_turn_off called")
        if self._api.power == AC_POWER_ON:
            await self._api.power_switch(AC_POWER_OFF)

    async def async_set_fan_mode(self, fan_mode):
        """Set fan mode."""
        await self._api.set_fan_mode(HA_FAN_MODE_TO_AT3.get(fan_mode)) 

    async def async_set_temperature(self, **kwargs):
        """Set the desired temperature"""
        _LOGGER.debug(f"[AT3Climate] async_set_temperature [{kwargs}]")
        temperature = kwargs.get(ATTR_TEMPERATURE)
        if temperature is not None:
            _LOGGER.debug(f"[AT3Climate] async_set_temperature Set temperature to [{temperature}]")
            await self._api.set_temperature(temperature)

    async def async_update(self):
        """Retrieve latest state."""
        await self._api.async_update()