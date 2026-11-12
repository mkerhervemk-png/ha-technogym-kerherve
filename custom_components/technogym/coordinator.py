"""Coordinateur de mise à jour Technogym."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import MywellnessAuthError, MywellnessClient, MywellnessError
from .const import (
    CONF_CLASS_DAYS,
    CONF_HISTORY_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_CLASS_DAYS,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    EVENT_NEW_ACTIVITY,
)

_LOGGER = logging.getLogger(__name__)

# Types renvoyés par ActivityHistory qui correspondent à un vrai entraînement
WORKOUT_TYPES = {"WorkoutSession", "HRWorkout", "ClassSession", "Class", "CardioSession"}


@dataclass(slots=True)
class Activity:
    """Une activité de l'historique Mywellness."""

    id: str
    start: datetime
    name: str
    type: str
    duration_min: int | None
    moves: int | None
    calories: int | None
    effectiveness: int | None
    compliance: int | None
    exercises_done: int | None
    exercises_planned: int | None

    @property
    def end(self) -> datetime:
        return self.start + timedelta(minutes=max(self.duration_min or 0, 1))

    @property
    def is_workout(self) -> bool:
        return self.type in WORKOUT_TYPES or (self.duration_min or 0) > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "debut": self.start.isoformat(),
            "nom": self.name,
            "type": self.type,
            "duree_min": self.duration_min,
            "moves": self.moves,
            "calories": self.calories,
            "efficacite": self.effectiveness,
            "conformite": self.compliance,
            "exercices": (
                f"{self.exercises_done}/{self.exercises_planned}"
                if self.exercises_planned
                else self.exercises_done
            ),
        }


@dataclass(slots=True)
class ClassEvent:
    """Un cours du planning du club."""

    id: str
    name: str
    start: datetime
    end: datetime
    partition_date: int
    room: str
    coach: str
    is_participant: bool
    in_waiting_list: bool
    waiting_position: int | None
    available_places: int | None
    max_participants: int | None
    booking_status: str
    booking_opens_on: datetime | None

    def as_dict(self) -> dict[str, Any]:
        return {
            "class_id": self.id,
            "partition_date": self.partition_date,
            "nom": self.name,
            "debut": self.start.isoformat(),
            "fin": self.end.isoformat(),
            "salle": self.room,
            "coach": self.coach,
            "inscrit": self.is_participant,
            "liste_attente": self.in_waiting_list,
            "places_libres": self.available_places,
            "places_max": self.max_participants,
            "statut_reservation": self.booking_status,
            "ouverture_reservation": (
                self.booking_opens_on.isoformat() if self.booking_opens_on else None
            ),
        }


@dataclass(slots=True)
class TechnogymData:
    """Instantané agrégé exposé aux entités."""

    activities: list[Activity] = field(default_factory=list)
    classes: list[ClassEvent] = field(default_factory=list)
    movergy: float | None = None
    classes_ok: bool = True
    movergy_ok: bool = True

    # ----- agrégats -----------------------------------------------------------

    def _since(self, start: datetime) -> list[Activity]:
        return [a for a in self.activities if a.start >= start]

    @staticmethod
    def _sum(items: list[Activity], attr: str) -> int:
        return sum(getattr(a, attr) or 0 for a in items)

    def period(self, kind: str) -> list[Activity]:
        now = dt_util.now()
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        if kind == "today":
            return self._since(today)
        if kind == "week":
            return self._since(today - timedelta(days=today.weekday()))
        if kind == "month":
            return self._since(today.replace(day=1))
        raise ValueError(kind)

    def total(self, kind: str, attr: str) -> int:
        return self._sum(self.period(kind), attr)

    def workouts(self, kind: str) -> list[Activity]:
        return [a for a in self.period(kind) if a.is_workout]

    @property
    def last_workout(self) -> Activity | None:
        return next((a for a in self.activities if a.is_workout), None)

    @property
    def booked_classes(self) -> list[ClassEvent]:
        now = dt_util.now()
        return [
            c
            for c in self.classes
            if (c.is_participant or c.in_waiting_list) and c.end >= now
        ]

    @property
    def next_booked_class(self) -> ClassEvent | None:
        booked = [c for c in self.booked_classes if c.is_participant]
        return booked[0] if booked else None


class TechnogymCoordinator(DataUpdateCoordinator[TechnogymData]):
    """Interroge Mywellness périodiquement."""

    config_entry: ConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, client: MywellnessClient
    ) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client
        self._known_ids: set[str] | None = None

    @property
    def history_days(self) -> int:
        return int(self.config_entry.options.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS))

    @property
    def class_days(self) -> int:
        return int(self.config_entry.options.get(CONF_CLASS_DAYS, DEFAULT_CLASS_DAYS))

    async def _async_update_data(self) -> TechnogymData:
        today = dt_util.now().date()
        # Couvre au minimum le mois en cours pour les cumuls mensuels
        history_start = min(today - timedelta(days=self.history_days), today.replace(day=1))
        previous = self.data

        try:
            raw_items = await self.client.async_get_activity_history(history_start, today)
        except MywellnessAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MywellnessError as err:
            raise UpdateFailed(f"Historique Mywellness indisponible : {err}") from err

        activities = sorted(
            (a for a in (parse_activity(i) for i in raw_items) if a is not None),
            key=lambda a: a.start,
            reverse=True,
        )
        data = TechnogymData(activities=activities)

        # Données secondaires : un échec garde la dernière valeur connue
        try:
            data.movergy = await self.client.async_get_movergy()
        except MywellnessAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MywellnessError as err:
            _LOGGER.debug("Movergy indisponible : %s", err)
            data.movergy = previous.movergy if previous else None
            data.movergy_ok = False

        try:
            raw_classes = await self.client.async_search_classes(
                today, today + timedelta(days=self.class_days)
            )
            data.classes = sorted(
                (c for c in (parse_class(i) for i in raw_classes) if c is not None),
                key=lambda c: c.start,
            )
        except MywellnessAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MywellnessError as err:
            _LOGGER.debug("Planning des cours indisponible : %s", err)
            data.classes = previous.classes if previous else []
            data.classes_ok = False

        self._fire_new_activity_events(activities)
        return data

    def _fire_new_activity_events(self, activities: list[Activity]) -> None:
        ids = {a.id for a in activities}
        if self._known_ids is None:
            # Premier passage : on mémorise sans notifier l'historique existant
            self._known_ids = ids
            return
        for act in reversed(activities):
            if act.id not in self._known_ids:
                payload = act.as_dict()
                payload["config_entry_id"] = self.config_entry.entry_id
                self.hass.bus.async_fire(EVENT_NEW_ACTIVITY, payload)
        self._known_ids |= ids


# ------------------------------------------------------------------------ parse


def _int(v: Any) -> int | None:
    try:
        return int(round(float(v))) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _partition_to_date(p: Any) -> date | None:
    s = str(p or "")
    if len(s) == 8 and s.isdigit():
        try:
            return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
        except ValueError:
            return None
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = dt_util.parse_datetime(str(value))
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt_util.get_default_time_zone())
    return dt_util.as_local(parsed)


def parse_activity(item: dict[str, Any]) -> Activity | None:
    day = _partition_to_date(item.get("partitionDate"))
    start = _parse_dt(item.get("startedOn") or item.get("date"))
    if start is None:
        if day is None:
            return None
        start = datetime(
            day.year,
            day.month,
            day.day,
            int(item.get("hour") or 0),
            int(item.get("minute") or 0),
            tzinfo=dt_util.get_default_time_zone(),
        )
    act_id = str(item.get("idCr") or item.get("id") or f"{start.isoformat()}-{item.get('name')}")
    return Activity(
        id=act_id,
        start=start,
        name=str(item.get("name") or item.get("displayName") or "Activité"),
        type=str(item.get("type") or ""),
        duration_min=_int(item.get("totalDurationInMinutes", item.get("duration"))),
        moves=_int(item.get("moves")),
        calories=_int(item.get("calories")),
        effectiveness=_int(item.get("workoutEffectiveness")),
        compliance=_int(item.get("workoutCompliance")),
        exercises_done=_int(item.get("numberOfExerciseDoneWithGroups")),
        exercises_planned=_int(item.get("numberOfExerciseToDoWithGroups")),
    )


def parse_class(item: dict[str, Any]) -> ClassEvent | None:
    start = _parse_dt(item.get("actualizedStartDateTime") or item.get("startDate"))
    end = _parse_dt(item.get("endDate"))
    if start is None or not item.get("id"):
        return None
    if end is None or end <= start:
        end = start + timedelta(hours=1)
    partition = _int(item.get("partitionDate")) or int(start.strftime("%Y%m%d"))
    booking = item.get("bookingInfo") or {}
    return ClassEvent(
        id=str(item["id"]),
        name=str(item.get("name") or "Cours"),
        start=start,
        end=end,
        partition_date=partition,
        room=str(item.get("room") or ""),
        coach=str(item.get("assignedTo") or ""),
        is_participant=bool(item.get("isParticipant")),
        in_waiting_list=bool(item.get("isInWaitingList")),
        waiting_position=_int(item.get("waitingListPosition")),
        available_places=_int(item.get("availablePlaces")),
        max_participants=_int(item.get("maxParticipants")),
        booking_status=str(booking.get("bookingUserStatus") or ""),
        booking_opens_on=_parse_dt(booking.get("bookingOpensOn")),
    )
