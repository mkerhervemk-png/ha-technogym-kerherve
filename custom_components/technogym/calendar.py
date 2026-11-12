"""Calendrier Technogym : séances réalisées + cours réservés."""

from __future__ import annotations

from datetime import datetime

from homeassistant.components.calendar import CalendarEntity, CalendarEvent
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import TechnogymConfigEntry
from .coordinator import Activity, ClassEvent, TechnogymCoordinator
from .entity import TechnogymEntity


async def async_setup_entry(
    hass: HomeAssistant,
    entry: TechnogymConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([TechnogymCalendar(entry.runtime_data)])


def _activity_event(a: Activity) -> CalendarEvent:
    bits = []
    if a.duration_min:
        bits.append(f"{a.duration_min} min")
    if a.calories:
        bits.append(f"{a.calories} kcal")
    if a.moves:
        bits.append(f"{a.moves} MOVEs")
    return CalendarEvent(
        start=a.start,
        end=a.end,
        summary=f"✅ {a.name}",
        description=" · ".join(bits) or None,
        uid=f"activity-{a.id}",
    )


def _class_event(c: ClassEvent) -> CalendarEvent:
    prefix = "🏋️" if c.is_participant else "⏳"
    desc = [p for p in (c.coach and f"Coach : {c.coach}", c.room and f"Salle : {c.room}") if p]
    if c.in_waiting_list and c.waiting_position:
        desc.append(f"Liste d'attente : position {c.waiting_position}")
    return CalendarEvent(
        start=c.start,
        end=c.end,
        summary=f"{prefix} {c.name}",
        description="\n".join(desc) or None,
        location=c.room or None,
        uid=f"class-{c.id}-{c.partition_date}",
    )


class TechnogymCalendar(TechnogymEntity, CalendarEntity):
    """Séances faites (historique) et cours réservés (à venir)."""

    def __init__(self, coordinator: TechnogymCoordinator) -> None:
        super().__init__(coordinator, "calendar")

    def _all_events(self) -> list[CalendarEvent]:
        data = self.coordinator.data
        events = [_activity_event(a) for a in data.activities]
        events += [_class_event(c) for c in data.booked_classes]
        return sorted(events, key=lambda e: e.start_datetime_local)

    @property
    def event(self) -> CalendarEvent | None:
        now = dt_util.now()
        upcoming = [e for e in self._all_events() if e.end_datetime_local >= now]
        return upcoming[0] if upcoming else None

    async def async_get_events(
        self, hass: HomeAssistant, start_date: datetime, end_date: datetime
    ) -> list[CalendarEvent]:
        return [
            e
            for e in self._all_events()
            if e.end_datetime_local > start_date and e.start_datetime_local < end_date
        ]
