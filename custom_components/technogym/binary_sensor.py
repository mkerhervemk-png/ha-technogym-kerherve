"""Capteurs binaires Technogym."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TechnogymConfigEntry
from .coordinator import TechnogymCoordinator
from .entity import TechnogymEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechnogymConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([TrainedTodaySensor(coordinator), ClassTodaySensor(coordinator)])


class TrainedTodaySensor(TechnogymEntity, BinarySensorEntity):
    """Vrai si une séance a été enregistrée aujourd'hui."""

    _attr_icon = "mdi:arm-flex"

    def __init__(self, coordinator: TechnogymCoordinator) -> None:
        super().__init__(coordinator, "trained_today")

    @property
    def is_on(self) -> bool:
        return bool(self.coordinator.data.workouts("today"))

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        return {"seances": [a.as_dict() for a in self.coordinator.data.workouts("today")]}


class ClassTodaySensor(TechnogymEntity, BinarySensorEntity):
    """Vrai si un cours réservé a lieu aujourd'hui (pas encore terminé)."""

    _attr_icon = "mdi:calendar-today"

    def __init__(self, coordinator: TechnogymCoordinator) -> None:
        super().__init__(coordinator, "class_today")

    @property
    def is_on(self) -> bool:
        today = dt_util.now().date()
        return any(
            c.is_participant and c.start.date() == today
            for c in self.coordinator.data.booked_classes
        )
