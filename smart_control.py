"""Smart AC Control logic for AirTouch 3."""
import logging
import voluptuous as vol

from homeassistant.components.climate.const import (
    DOMAIN as CLIMATE_DOMAIN,
    SERVICE_TURN_OFF,
    SERVICE_TURN_ON,
)
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.helpers.typing import ConfigType
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN as AT3_DOMAIN

_LOGGER = logging.getLogger(__name__)

# Constants for the control logic
HIGH_FAN_PERCENTAGE = 100
LOW_FAN_PERCENTAGE = 5
TEMP_THRESHOLD_HIGH = 2  # Degrees above set temp to turn off zone
TEMP_THRESHOLD_LOW = 2   # Degrees below set temp to turn on AC
MIN_DAMPER_PERCENTAGE = 50  # Minimum required combined damper opening percentage

# Default notification service - can be overridden in service call
DEFAULT_NOTIFY_SERVICE = "mobile_app_toms_phone"

async def async_setup_services(hass: HomeAssistant):
    """Set up services for AirTouch3 smart control."""

    async def handle_smart_control(call: ServiceCall):
        """Handle the smart control service call."""
        _LOGGER.debug(f"[AT3SmartControl] Running smart control logic")

        # Get parameters from service call data with defaults
        climate_entity_id = call.data.get("climate_entity_id", None)
        notify_service = call.data.get("notify_service", DEFAULT_NOTIFY_SERVICE)

        # If no climate entity ID was provided, try to find the AirTouch climate entity
        if not climate_entity_id:
            registry = er.async_get(hass)
            airtouch_entities = [
                entity_id for entity_id, entry in registry.entities.items()
                if entry.platform == AT3_DOMAIN and entry.domain == CLIMATE_DOMAIN
            ]

            if airtouch_entities:
                climate_entity_id = airtouch_entities[0]
                _LOGGER.debug(f"[AT3SmartControl] Auto-discovered climate entity: {climate_entity_id}")
            else:
                _LOGGER.error(f"[AT3SmartControl] No AirTouch climate entity found. Please specify climate_entity_id in service call.")
                return

        # Get aircon state
        aircon_state = hass.states.get(climate_entity_id)
        if not aircon_state:
            _LOGGER.error(f"[AT3SmartControl] AC entity {climate_entity_id} not found")
            return

        # Get AC API (vzduch_api) from the hass data
        entry_id = None
        for e_id, data in hass.data[AT3_DOMAIN].items():
            entry_id = e_id
            break

        if not entry_id:
            _LOGGER.error(f"[AT3SmartControl] No AirTouch3 integration found")
            return

        vzduch_api = hass.data[AT3_DOMAIN].get(entry_id)
        if not vzduch_api:
            _LOGGER.error(f"[AT3SmartControl] API not found")
            return

        # Force an update to get the latest data
        await vzduch_api.async_update(no_throttle=True)

        # Process each zone
        active_zones = 0
        combined_damper = 0
        all_zones_at_temp = True
        any_zone_below_min = False

        for zone in vzduch_api.zones:
            # Skip inactive zones or zones without sensors
            if zone.status != 1 or not zone.sensors:
                continue

            active_zones += 1
            combined_damper += zone.fan_value

            # Get temperature from the first sensor in the zone
            zone_temp = zone.sensors[0].temperature if zone.sensors else None
            if zone_temp is None:
                _LOGGER.debug(f"[AT3SmartControl] Zone {zone.name} has no temperature reading")
                continue

            # Logic for fan control based on temperature
            if zone_temp >= zone.desired_temperature + TEMP_THRESHOLD_HIGH:
                # Close damper completely if 2+ degrees above target (instead of turning off)
                _LOGGER.info(f"[AT3SmartControl] Zone {zone.name} temp ({zone_temp}°C) is above threshold, closing damper")
                await vzduch_api.set_zone_damper(zone.id, 0)

                # Notify user if notification service is available
                if notify_service:
                    try:
                        await hass.services.async_call(
                            "notify",
                            notify_service,
                            {
                                "title": "Zone Damper Closed",
                                "message": f"Zone {zone.name} damper closed because temperature ({zone_temp}°C) is {TEMP_THRESHOLD_HIGH}+ degrees above desired ({zone.desired_temperature}°C)."
                            }
                        )
                    except Exception as e:
                        _LOGGER.warning(f"[AT3SmartControl] Failed to send notification: {e}")
            elif zone_temp >= zone.desired_temperature:
                # Set fan to LOW_FAN_PERCENTAGE if at/above target temp
                _LOGGER.info(f"[AT3SmartControl] Zone {zone.name} temp ({zone_temp}°C) is at/above set point, setting fan to {LOW_FAN_PERCENTAGE}%")
                await vzduch_api.set_zone_damper(zone.id, LOW_FAN_PERCENTAGE)
            else:
                # Set fan to HIGH_FAN_PERCENTAGE if below target temp
                _LOGGER.info(f"[AT3SmartControl] Zone {zone.name} temp ({zone_temp}°C) is below set point, setting fan to {HIGH_FAN_PERCENTAGE}%")
                await vzduch_api.set_zone_damper(zone.id, HIGH_FAN_PERCENTAGE)
                all_zones_at_temp = False

            # Check if any zone is below min temperature threshold
            if zone_temp <= zone.desired_temperature - TEMP_THRESHOLD_LOW:
                any_zone_below_min = True

        # Recalculate combined damper after adjustments
        combined_damper = 0
        active_zones = 0
        for zone in vzduch_api.zones:
            if zone.status == 1:
                active_zones += 1
                combined_damper += zone.fan_value

        # Check damper requirement (50% combined)
        damper_ratio = combined_damper / (active_zones * 100) if active_zones > 0 else 0
        if damper_ratio < (MIN_DAMPER_PERCENTAGE / 100) and active_zones > 0:
            # Turn off AC if not enough combined damper opening
            _LOGGER.warning(f"[AT3SmartControl] Insufficient damper opening ({combined_damper}%), turning AC off")
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_TURN_OFF,
                {"entity_id": climate_entity_id}
            )

            # Notify user if notification service is available
            if notify_service:
                try:
                    await hass.services.async_call(
                        "notify",
                        notify_service,
                        {
                            "title": "AC Turned Off",
                            "message": f"AC turned off due to insufficient damper opening. Combined damper value: {combined_damper}%"
                        }
                    )
                except Exception as e:
                    _LOGGER.warning(f"[AT3SmartControl] Failed to send notification: {e}")
        elif all_zones_at_temp and active_zones > 0:
            # Turn off AC if all zones at target temp
            _LOGGER.info(f"[AT3SmartControl] All {active_zones} zones at set temp, turning AC off")
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_TURN_OFF,
                {"entity_id": climate_entity_id}
            )
        elif any_zone_below_min and vzduch_api.power == 0:  # Check if AC is off (0 = off, 1 = on)
            # Turn on AC if any zone is below min temp and AC is currently off
            _LOGGER.info(f"[AT3SmartControl] At least one zone below min temp, turning AC on")
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_TURN_ON,
                {"entity_id": climate_entity_id}
            )

    # Register the service with schema
    service_schema = vol.Schema(
        {
            vol.Optional("climate_entity_id"): str,
            vol.Optional("notify_service"): str,
        }
    )

    hass.services.async_register(
        AT3_DOMAIN,
        "run_smart_control",
        handle_smart_control,
        schema=service_schema
    )

    return True