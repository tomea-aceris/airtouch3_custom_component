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
DEFAULT_NOTIFY_SERVICE = "mobile_app_tom_s_phone"

# Input boolean for main control switch
AC_CONTROL_ACTIVE = "input_boolean.ac_control_active"

# Input boolean prefix for zone controls
ZONE_CONTROL_PREFIX = "input_boolean.at3_zone_"

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

        # Check if automation is active
        automation_active = hass.states.get(AC_CONTROL_ACTIVE)
        if not automation_active or automation_active.state != "on":
            _LOGGER.debug(f"[AT3SmartControl] Smart control automation is not active")
            return

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

        # Get all the AirTouch3 switch entities from the registry
        registry = er.async_get(hass)
        airtouch_switch_entities = {}

        for entity_id, entry in registry.entities.items():
            if entry.platform == AT3_DOMAIN and entry.domain == "switch":
                # Extract the zone ID from the unique_id of the entity
                # The unique_id format is typically "{airtouch_id}-{zone_id}-switch"
                # We need to extract the zone_id part
                parts = entry.unique_id.split('-')
                if len(parts) >= 2:
                    zone_id_str = parts[1]  # The zone ID part
                    try:
                        zone_id = int(zone_id_str)
                        airtouch_switch_entities[zone_id] = entity_id
                        _LOGGER.debug(f"[AT3SmartControl] Found AirTouch switch: {entity_id} for zone ID {zone_id}")
                    except ValueError:
                        _LOGGER.warning(f"[AT3SmartControl] Could not parse zone ID from {entry.unique_id}")

        # Find controlled zones based on input_boolean entities
        controlled_zone_ids = []

        # Get all enabled zone control input_booleans
        for entity_id in hass.states.async_entity_ids("input_boolean"):
            if entity_id.startswith(ZONE_CONTROL_PREFIX) and hass.states.get(entity_id).state == "on":
                # For each enabled input_boolean, find the corresponding zone
                for zone in vzduch_api.zones:
                    # If the zone's switch entity is found in the registry
                    if zone.id in airtouch_switch_entities:
                        # Extract identifier from input_boolean (e.g., "living_area" from "input_boolean.at3_zone_living_area")
                        zone_identifier = entity_id.replace(ZONE_CONTROL_PREFIX, "")

                        # Get the zone name in a format that matches our input_boolean naming
                        zone_name_formatted = zone.name.lower().replace(' ', '_')

                        # Compare formatted names or try alternate formats
                        if (zone_identifier == zone_name_formatted or
                                zone_identifier in zone_name_formatted or
                                any(word in zone_identifier for word in zone_name_formatted.split('_'))):
                            controlled_zone_ids.append(zone.id)
                            _LOGGER.debug(f"[AT3SmartControl] Zone ID {zone.id} ({zone.name}) will be controlled by {entity_id}")
                            break

        # Safety check - if no controlled zones, exit
        if not controlled_zone_ids:
            _LOGGER.warning(f"[AT3SmartControl] No controlled zones found. Smart control will not manage any zones.")
            return

        # Track states for control logic
        any_controlled_zone_below_threshold = False  # Any zone 2+ degrees below desired temp

        # First count all active controlled zones
        active_controlled_zones_count = 0
        for zone in vzduch_api.zones:
            if zone.id in controlled_zone_ids and zone.status == 1:
                active_controlled_zones_count += 1

        # Now process each controlled zone
        for zone in vzduch_api.zones:
            # Only process zones that are controlled by input_boolean toggles
            if zone.id not in controlled_zone_ids:
                _LOGGER.debug(f"[AT3SmartControl] Ignoring zone {zone.name} as it's not controlled")
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

            # RULE 1: When a zone reaches TEMP_ABOVE_THRESHOLD degrees above the set desired temp, switch off the zone
            # BUT if it's the last active zone, don't turn it off - instead turn off the AC
            if zone_temp >= zone.desired_temperature + TEMP_ABOVE_THRESHOLD:
                if zone.status == 1:  # Only switch off if currently on
                    # Check if this is the last active zone
                    if active_controlled_zones_count == 1:
                        _LOGGER.info(f"[AT3SmartControl] Last active zone {zone.name} reached max temp, will turn off AC instead of zone")
                        # This zone will remain on, but we'll turn off the AC in the AC control logic section
                    else:
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

            # RULE 2: When a zone temp drops TEMP_BELOW_THRESHOLD degrees below the set desired temp, switch on the zone
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

                # Mark that we have a controlled zone below threshold
                any_controlled_zone_below_threshold = True

        # Force another update to get the latest zone status after our changes
        try:
            await vzduch_api.async_update(no_throttle=True)
        except TypeError:
            await vzduch_api.async_update()

        # Re-check if we have any active zones after the updates
        active_controlled_zones_count = 0
        all_active_zones_above_threshold = True
        for zone in vzduch_api.zones:
            if zone.id in controlled_zone_ids and zone.status == 1:
                active_controlled_zones_count += 1

                # Skip zones without sensors
                if not zone.sensors:
                    continue

                # Get temperature from the first sensor in the zone
                zone_temp = zone.sensors[0].temperature
                if zone_temp is None:
                    continue

                # Check if zone is not above threshold temperature
                if zone_temp < zone.desired_temperature + TEMP_ABOVE_THRESHOLD:
                    all_active_zones_above_threshold = False

        _LOGGER.debug(f"[AT3SmartControl] Status: active_zones={active_controlled_zones_count}, all_above_threshold={all_active_zones_above_threshold}, any_below_threshold={any_controlled_zone_below_threshold}")

        # Check current AC power state
        ac_is_on = vzduch_api.power == 1

        # RULE 3: When all currently switched on zones max temps are reached, turn off the AC,
        # and turn on all controlled zones that are currently off
        if all_active_zones_above_threshold and active_controlled_zones_count > 0 and ac_is_on:
            _LOGGER.info(f"[AT3SmartControl] All active zones ({active_controlled_zones_count}) above temperature threshold, turning AC off")

            # Turn off the AC
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

        # RULE 4: If any controlled zone drops 2 degrees below its desired temp, turn on the AC
        elif any_controlled_zone_below_threshold and not ac_is_on:
            _LOGGER.info(f"[AT3SmartControl] At least one controlled zone below threshold, turning AC on")
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
                            "message": "AC turned on because at least one controlled zone is below temperature threshold."
                        }
                    )
                except Exception as e:
                    _LOGGER.warning(f"[AT3SmartControl] Failed to send notification: {e}")

        # RULE 5: When AC is off, make sure all controlled zones are turned on
        # This ensures zones are ready for cooling/heating when AC turns on again
        elif not ac_is_on:
            zones_activated = 0
            for zone in vzduch_api.zones:
                if zone.id in controlled_zone_ids and zone.status == 0:
                    _LOGGER.info(f"[AT3SmartControl] AC is off, activating controlled zone {zone.name}")
                    await vzduch_api.zone_switch(zone.id, 1)  # Switch zone on
                    zones_activated += 1

            if zones_activated > 0:
                _LOGGER.info(f"[AT3SmartControl] AC is off, activated {zones_activated} controlled zones")

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