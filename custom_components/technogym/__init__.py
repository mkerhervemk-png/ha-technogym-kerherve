"""Intégration Technogym (Mywellness) pour Home Assistant."""

from __future__ import annotations

import logging
from datetime import timedelta

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import dt as dt_util

from .api import MywellnessAuthError, MywellnessClient, MywellnessError
from .const import (
    ATTR_CLASS_ID,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_DAYS,
    ATTR_ONLY_BOOKABLE,
    ATTR_PARTITION_DATE,
    CONF_FACILITY_ID,
    CONF_FACILITY_URL,
    DOMAIN,
    SERVICE_BOOK_CLASS,
    SERVICE_LIST_CLASSES,
    SERVICE_REFRESH,
    SERVICE_UNBOOK_CLASS,
)
from .coordinator import TechnogymCoordinator, parse_class

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.SENSOR, Platform.BINARY_SENSOR, Platform.CALENDAR]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

type TechnogymConfigEntry = ConfigEntry[TechnogymCoordinator]

_ENTRY_FIELD = {vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string}

BOOK_SCHEMA = vol.Schema(
    {
        **_ENTRY_FIELD,
        vol.Required(ATTR_CLASS_ID): cv.string,
        vol.Required(ATTR_PARTITION_DATE): vol.Coerce(int),
    }
)
LIST_SCHEMA = vol.Schema(
    {
        **_ENTRY_FIELD,
        vol.Optional(ATTR_DAYS, default=7): vol.All(vol.Coerce(int), vol.Range(min=0, max=21)),
        vol.Optional(ATTR_ONLY_BOOKABLE, default=False): cv.boolean,
    }
)
REFRESH_SCHEMA = vol.Schema(_ENTRY_FIELD)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Enregistre les services (une seule fois pour toutes les entrées)."""
    _register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TechnogymConfigEntry) -> bool:
    client = MywellnessClient(
        async_get_clientsession(hass),
        entry.data[CONF_EMAIL],
        entry.data[CONF_PASSWORD],
        facility_url=entry.data.get(CONF_FACILITY_URL),
        facility_id=entry.data.get(CONF_FACILITY_ID),
    )
    try:
        await client.login()
    except MywellnessAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except MywellnessError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = TechnogymCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: TechnogymConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_options(hass: HomeAssistant, entry: TechnogymConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


# ------------------------------------------------------------------- services


def _get_coordinator(hass: HomeAssistant, call: ServiceCall) -> TechnogymCoordinator:
    entry_id = call.data.get(ATTR_CONFIG_ENTRY_ID)
    entries = [
        e
        for e in hass.config_entries.async_entries(DOMAIN)
        if e.state is ConfigEntryState.LOADED and (entry_id is None or e.entry_id == entry_id)
    ]
    if not entries:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="no_loaded_entry"
        )
    return entries[0].runtime_data


def _register_services(hass: HomeAssistant) -> None:
    async def _book(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call)
        try:
            result = await coordinator.client.async_book_class(
                call.data[ATTR_CLASS_ID], call.data[ATTR_PARTITION_DATE]
            )
        except MywellnessError as err:
            raise HomeAssistantError(f"Réservation refusée : {err}") from err
        await coordinator.async_request_refresh()
        return {"result": result.get("result"), "message": result.get("message")}

    async def _unbook(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call)
        try:
            result = await coordinator.client.async_unbook_class(
                call.data[ATTR_CLASS_ID], call.data[ATTR_PARTITION_DATE]
            )
        except MywellnessError as err:
            raise HomeAssistantError(f"Annulation refusée : {err}") from err
        await coordinator.async_request_refresh()
        return {"result": result.get("result"), "message": result.get("message")}

    async def _list(call: ServiceCall) -> ServiceResponse:
        coordinator = _get_coordinator(hass, call)
        today = dt_util.now().date()
        try:
            raw = await coordinator.client.async_search_classes(
                today, today + timedelta(days=call.data[ATTR_DAYS])
            )
        except MywellnessError as err:
            raise HomeAssistantError(f"Planning indisponible : {err}") from err
        now = dt_util.now()
        classes = sorted(
            (c for c in (parse_class(i) for i in raw) if c is not None and c.end >= now),
            key=lambda c: c.start,
        )
        if call.data[ATTR_ONLY_BOOKABLE]:
            classes = [
                c
                for c in classes
                if not c.is_participant
                and (c.available_places or 0) > 0
                and c.booking_status.lower() in ("canbook", "")
            ]
        return {"cours": [c.as_dict() for c in classes]}

    async def _refresh(call: ServiceCall) -> None:
        await _get_coordinator(hass, call).async_refresh()

    hass.services.async_register(
        DOMAIN, SERVICE_BOOK_CLASS, _book, schema=BOOK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_UNBOOK_CLASS, _unbook, schema=BOOK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN, SERVICE_LIST_CLASSES, _list, schema=LIST_SCHEMA,
        supports_response=SupportsResponse.ONLY,
    )
    hass.services.async_register(DOMAIN, SERVICE_REFRESH, _refresh, schema=REFRESH_SCHEMA)
