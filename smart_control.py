"""Smart AC Control logic for AirTouch 3 with simplified zone-based temperature control."""
import logging
import voluptuous as vol
import time

from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.helpers import entity_registry as er

from .const import DOMAIN as AT3_DOMAIN

_LOGGER = logging.getLogger(__name__)

# Constants for the control logic
TEMP_ABOVE_THRESHOLD = 1  # Degrees above set temp to turn off zone
TEMP_BELOW_THRESHOLD = 2  # Degrees below set temp to turn on zone

# Climate domain and services
CLIMATE_DOMAIN = "climate"
SERVICE_TURN_ON = "turn_on"
SERVICE_TURN_OFF = "turn_off"

# Default notification service - can be overridden in service call
DEFAULT_NOTIFY_SERVICE = "mobile_app_toms_phone"

async def async_setup_services(hass: HomeAssistant):
    """Set up services for AirTouch3 smart control."""
    _LOGGER.debug(f"[AT3SmartControl] Setting up services for {AT3_DOMAIN}")

    async def handle_smart_control(call: ServiceCall):
        """Handle the smart control service call."""
        start_time = time.time()  # Track start time
        _LOGGER.debug(f"[AT3SmartControl] Running smart control logic with call data: {call.data}")

        # Get parameters from service call data with defaults
        climate_entity_id = call.data.get("climate_entity_id", None)
        notify_service = call.data.get("notify_service", DEFAULT_NOTIFY_SERVICE)

        # Store active zones when smart control is first activated
        # Use the hass.data dictionary to store active zones
        if f"{AT3_DOMAIN}_active_zones" not in hass.data:
            # First run - need to identify active zones
            _LOGGER.debug(f"[AT3SmartControl] First run detected - will record active zones")
            is_first_run = True
        else:
            is_first_run = False

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
        vzduch_api = None

        # First, check if DOMAIN exists in hass.data
        if AT3_DOMAIN not in hass.data:
            _LOGGER.error(f"[AT3SmartControl] {AT3_DOMAIN} not found in hass.data")
            return

        # Find the first entry_id in the domain data
        for e_id, data in hass.data[AT3_DOMAIN].items():
            entry_id = e_id
            vzduch_api = data
            break

        if not entry_id or not vzduch_api:
            _LOGGER.error(f"[AT3SmartControl] No AirTouch3 integration or API found")
            return

        # Force an update to get the latest data
        try:
            await vzduch_api.async_update(no_throttle=True)
        except TypeError:
            # If no_throttle is not accepted, call without it
            await vzduch_api.async_update()

        # On first run, record active zones
        if is_first_run:
            active_zone_ids = [zone.id for zone in vzduch_api.zones if zone.status == 1]
            hass.data[f"{AT3_DOMAIN}_active_zones"] = active_zone_ids
            _LOGGER.info(f"[AT3SmartControl] Recorded active zones: {active_zone_ids}")

        # Get the list of zones that were active when smart control was activated
        monitored_zone_ids = hass.data.get(f"{AT3_DOMAIN}_active_zones", [])

        # Track states for control logic
        all_active_zones_above_threshold = True  # All zones at or above max threshold temp
        any_active_zone_below_threshold = False  # Any zone 2+ degrees below desired temp
        active_monitored_zones_count = 0  # Count of currently active monitored zones

        # Process each zone that was initially active
        for zone in vzduch_api.zones:
            # Only process zones that were active when smart control was activated
            if zone.id not in monitored_zone_ids:
                _LOGGER.debug(f"[AT3SmartControl] Ignoring zone {zone.name} as it wasn't active at smart control activation")
                continue

            # Skip zones without sensors
            if not zone.sensors:
                _LOGGER.debug(f"[AT3SmartControl] Zone {zone.name} has no sensors, skipping")
                continue

            # Get temperature from the first sensor in the zone
            zone_temp = zone.sensors[0].temperature
            if zone_temp is None:
                _LOGGER.debug(f"[AT3SmartControl] Zone {zone.name} has no temperature reading")
                continue

            # Track if this is an active zone
            if zone.status == 1:
                active_monitored_zones_count += 1

                # Check if zone is not above threshold temperature
                if zone_temp < zone.desired_temperature + TEMP_ABOVE_THRESHOLD:
                    all_active_zones_above_threshold = False

            # Check if zone is too warm (1+ degree above desired)
            if zone_temp >= zone.desired_temperature + TEMP_ABOVE_THRESHOLD:
                if zone.status == 1:  # Only switch off if currently on
                    _LOGGER.info(f"[AT3SmartControl] Zone {zone.name} temp ({zone_temp}°C) is {TEMP_ABOVE_THRESHOLD}° above desired, turning off")
                    await vzduch_api.zone_switch(zone.id, 0)  # Switch zone off

                    # Notify user
                    if notify_service:
                        try:
                            await hass.services.async_call(
                                "notify",
                                notify_service,
                                {
                                    "title": "Zone Turned Off",
                                    "message": f"Zone {zone.name} turned off because temperature ({zone_temp}°C) is {TEMP_ABOVE_THRESHOLD}° above desired ({zone.desired_temperature}°C)."
                                }
                            )
                        except Exception as e:
                            _LOGGER.warning(f"[AT3SmartControl] Failed to send notification: {e}")

            # Check if zone is too cold (2+ degrees below desired)
            elif zone_temp <= zone.desired_temperature - TEMP_BELOW_THRESHOLD:
                if zone.status == 0:  # Only switch on if currently off
                    _LOGGER.info(f"[AT3SmartControl] Zone {zone.name} temp ({zone_temp}°C) is {TEMP_BELOW_THRESHOLD}° below desired, turning on")
                    await vzduch_api.zone_switch(zone.id, 1)  # Switch zone on

                    # Notify user
                    if notify_service:
                        try:
                            await hass.services.async_call(
                                "notify",
                                notify_service,
                                {
                                    "title": "Zone Turned On",
                                    "message": f"Zone {zone.name} turned on because temperature ({zone_temp}°C) is {TEMP_BELOW_THRESHOLD}° below desired ({zone.desired_temperature}°C)."
                                }
                            )
                        except Exception as e:
                            _LOGGER.warning(f"[AT3SmartControl] Failed to send notification: {e}")

                # Mark that we have an active zone below threshold
                any_active_zone_below_threshold = True

        # Force another update to get the latest zone status after our changes
        try:
            await vzduch_api.async_update(no_throttle=True)
        except TypeError:
            await vzduch_api.async_update()

        # Re-check if we have any active zones after the updates
        active_monitored_zones_count = 0
        for zone in vzduch_api.zones:
            if zone.id in monitored_zone_ids and zone.status == 1:
                active_monitored_zones_count += 1

        _LOGGER.debug(f"[AT3SmartControl] Status: active_zones={active_monitored_zones_count}, all_above_threshold={all_active_zones_above_threshold}, any_below_threshold={any_active_zone_below_threshold}")

        # AC Control Logic
        if all_active_zones_above_threshold and active_monitored_zones_count > 0:
            # All active zones are at or above threshold temp - turn off AC and reactivate all managed zones
            _LOGGER.info(f"[AT3SmartControl] All active zones ({active_monitored_zones_count}) above temperature threshold, turning AC off and reactivating all managed zones")

            # First turn off the AC if it's on
            if vzduch_api.power == 1:  # Only turn off if currently on
                await hass.services.async_call(
                    CLIMATE_DOMAIN,
                    SERVICE_TURN_OFF,
                    {"entity_id": climate_entity_id}
                )

                # Notify user
                if notify_service:
                    try:
                        await hass.services.async_call(
                            "notify",
                            notify_service,
                            {
                                "title": "AC Turned Off",
                                "message": "AC turned off because all active zones have reached their max temperature threshold."
                            }
                        )
                    except Exception as e:
                        _LOGGER.warning(f"[AT3SmartControl] Failed to send notification: {e}")

            # Then reactivate all managed zones for visibility
            for zone in vzduch_api.zones:
                if zone.id in monitored_zone_ids and zone.status == 0:
                    _LOGGER.info(f"[AT3SmartControl] Reactivating managed zone {zone.name} for visibility")
                    await vzduch_api.zone_switch(zone.id, 1)  # Switch zone on

        elif any_active_zone_below_threshold and vzduch_api.power == 0:
            # At least one active zone is below threshold and AC is off - turn on AC
            _LOGGER.info(f"[AT3SmartControl] At least one active zone below threshold, turning AC on")
            await hass.services.async_call(
                CLIMATE_DOMAIN,
                SERVICE_TURN_ON,
                {"entity_id": climate_entity_id}
            )

            # Notify user
            if notify_service:
                try:
                    await hass.services.async_call(
                        "notify",
                        notify_service,
                        {
                            "title": "AC Turned On",
                            "message": "AC turned on because at least one active zone is below temperature threshold."
                        }
                    )
                except Exception as e:
                    _LOGGER.warning(f"[AT3SmartControl] Failed to send notification: {e}")

        # Log total execution time
        execution_time = time.time() - start_time
        _LOGGER.info(f"[AT3SmartControl] Total execution time: {execution_time:.2f} seconds")

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