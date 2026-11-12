"""Entité de base Technogym."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_FACILITY_NAME, DOMAIN, MANUFACTURER
from .coordinator import TechnogymCoordinator


class TechnogymEntity(CoordinatorEntity[TechnogymCoordinator]):
    """Rattache toutes les entités à un appareil « service » par compte."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TechnogymCoordinator, key: str) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        uid = entry.unique_id or entry.entry_id
        self._attr_unique_id = f"{uid}_{key}"
        self._attr_translation_key = key
        user = coordinator.client.user
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, uid)},
            name=f"Technogym {user.first_name}".strip() if user else "Technogym",
            manufacturer=MANUFACTURER,
            model=entry.data.get(CONF_FACILITY_NAME) or "Mywellness",
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://www.mywellness.com",
        )
