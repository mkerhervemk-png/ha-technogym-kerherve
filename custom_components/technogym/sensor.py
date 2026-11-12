"""Capteurs Technogym."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory, UnitOfEnergy, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TechnogymConfigEntry
from .coordinator import TechnogymCoordinator, TechnogymData
from .entity import TechnogymEntity

MOVES = "MOVEs"


@dataclass(frozen=True, kw_only=True)
class TechnogymSensorDescription(SensorEntityDescription):
    value_fn: Callable[[TechnogymData], Any]
    attrs_fn: Callable[[TechnogymData], dict[str, Any]] | None = None


def _last_attrs(d: TechnogymData) -> dict[str, Any]:
    last = d.last_workout
    return last.as_dict() if last else {}


def _days_since(d: TechnogymData) -> int | None:
    last = d.last_workout
    if last is None:
        return None
    return (dt_util.now().date() - last.start.date()).days


def _next_class_attrs(d: TechnogymData) -> dict[str, Any]:
    nxt = d.next_booked_class
    attrs: dict[str, Any] = nxt.as_dict() if nxt else {}
    attrs["reservations"] = [c.as_dict() for c in d.booked_classes]
    return attrs


def _week_attrs(d: TechnogymData) -> dict[str, Any]:
    return {"seances": [a.as_dict() for a in d.workouts("week")]}


def _recent_attrs(d: TechnogymData) -> dict[str, Any]:
    return {"activites": [a.as_dict() for a in d.activities[:10]]}


SENSORS: tuple[TechnogymSensorDescription, ...] = (
    TechnogymSensorDescription(
        key="movergy",
        icon="mdi:speedometer",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.movergy,
    ),
    TechnogymSensorDescription(
        key="moves_today",
        icon="mdi:run-fast",
        native_unit_of_measurement=MOVES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.total("today", "moves"),
    ),
    TechnogymSensorDescription(
        key="moves_week",
        icon="mdi:run-fast",
        native_unit_of_measurement=MOVES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.total("week", "moves"),
    ),
    TechnogymSensorDescription(
        key="moves_month",
        icon="mdi:run-fast",
        native_unit_of_measurement=MOVES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.total("month", "moves"),
    ),
    TechnogymSensorDescription(
        key="calories_today",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.total("today", "calories"),
    ),
    TechnogymSensorDescription(
        key="calories_week",
        device_class=SensorDeviceClass.ENERGY,
        native_unit_of_measurement=UnitOfEnergy.KILO_CALORIE,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda d: d.total("week", "calories"),
    ),
    TechnogymSensorDescription(
        key="duration_week",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: d.total("week", "duration_min"),
    ),
    TechnogymSensorDescription(
        key="workouts_week",
        icon="mdi:dumbbell",
        native_unit_of_measurement="séances",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.workouts("week")),
        attrs_fn=_week_attrs,
    ),
    TechnogymSensorDescription(
        key="workouts_month",
        icon="mdi:dumbbell",
        native_unit_of_measurement="séances",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda d: len(d.workouts("month")),
    ),
    TechnogymSensorDescription(
        key="last_workout",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:weight-lifter",
        value_fn=lambda d: d.last_workout.start if d.last_workout else None,
        attrs_fn=_last_attrs,
    ),
    TechnogymSensorDescription(
        key="last_workout_name",
        icon="mdi:clipboard-text",
        value_fn=lambda d: d.last_workout.name if d.last_workout else None,
        attrs_fn=_recent_attrs,
    ),
    TechnogymSensorDescription(
        key="days_since_last_workout",
        icon="mdi:calendar-clock",
        native_unit_of_measurement=UnitOfTime.DAYS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=_days_since,
    ),
    TechnogymSensorDescription(
        key="next_class",
        device_class=SensorDeviceClass.TIMESTAMP,
        icon="mdi:calendar-star",
        value_fn=lambda d: d.next_booked_class.start if d.next_booked_class else None,
        attrs_fn=_next_class_attrs,
    ),
    TechnogymSensorDescription(
        key="booked_classes",
        icon="mdi:calendar-check",
        native_unit_of_measurement="cours",
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: len([c for c in d.booked_classes if c.is_participant]),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechnogymConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities(TechnogymSensor(coordinator, desc) for desc in SENSORS)


class TechnogymSensor(TechnogymEntity, SensorEntity):
    entity_description: TechnogymSensorDescription

    def __init__(
        self, coordinator: TechnogymCoordinator, description: TechnogymSensorDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        value = self.entity_description.value_fn(self.coordinator.data)
        if isinstance(value, datetime):
            return dt_util.as_utc(value)
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn else None
