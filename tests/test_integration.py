"""Tests de bout en bout avec réponses Mywellness simulées."""
from datetime import timedelta

import pytest
from freezegun.api import FrozenDateTimeFactory
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.technogym.const import DOMAIN

CORE = "https://core.mywellness.com/v2/enduser/authentication/login"
HIST = "https://services.mywellness.com/monclub/Training/User/u-1/ActivityHistory"
MOV = "https://services.mywellness.com/monclub/Training/User/u-1/MyMovergy"
CLS = "https://calendar.mywellness.com/v2/enduser/class/Search"
BOOK = "https://calendar.mywellness.com/v2/enduser/class/Book"

LOGIN_OK = {
    "data": {
        "token": "tok",
        "userContext": {"id": "u-1", "firstName": "Mael", "lastName": "K", "defaultCulture": "fr-FR"},
        "facilities": [{"id": "fac-1", "url": "monclub", "name": "Mon Club Saint-Brieuc"}],
    }
}


def _today_partition():
    return int(dt_util.now().strftime("%Y%m%d"))


def _history():
    p = _today_partition()
    y = int((dt_util.now() - timedelta(days=2)).strftime("%Y%m%d"))
    return {"data": {"items": [
        {"idCr": 111, "partitionDate": p, "hour": 7, "minute": 30, "name": "Full body", "type": "WorkoutSession",
         "totalDurationInMinutes": 45, "moves": 620, "calories": 410, "workoutEffectiveness": 82},
        {"idCr": 109, "partitionDate": p, "hour": 0, "minute": 8, "name": "iphone", "type": "Movement",
         "duration": 10, "moves": 100, "calories": 40},
        {"idCr": 110, "partitionDate": y, "hour": 18, "minute": 0, "name": "Tapis", "type": "WorkoutSession",
         "duration": 30, "moves": 300, "calories": 250},
    ]}}


def _classes():
    start = (dt_util.now() + timedelta(days=1)).replace(hour=19, minute=0, second=0, microsecond=0)
    fmt = "%Y-%m-%dT%H:%M:%S"
    return [
        {"id": "c-1", "name": "Group Cycle", "startDate": start.strftime(fmt),
         "endDate": (start + timedelta(minutes=45)).strftime(fmt), "partitionDate": int(start.strftime("%Y%m%d")),
         "room": "Studio", "assignedTo": "Julie", "isParticipant": True, "availablePlaces": 3, "maxParticipants": 20,
         "bookingInfo": {"bookingUserStatus": "AlreadyBooked"}},
        {"id": "c-2", "name": "Pilates", "startDate": (start + timedelta(hours=1)).strftime(fmt),
         "endDate": (start + timedelta(hours=2)).strftime(fmt), "partitionDate": int(start.strftime("%Y%m%d")),
         "isParticipant": False, "availablePlaces": 5, "bookingInfo": {"bookingUserStatus": "CanBook"}},
    ]


def _mock_all(aioclient_mock: AiohttpClientMocker, history=None):
    aioclient_mock.post(CORE, json=LOGIN_OK)
    aioclient_mock.post(HIST, json=history or _history())
    aioclient_mock.post(MOV, json={"data": {"movergy": 74}})
    aioclient_mock.get(CLS, json=_classes())


async def test_config_flow(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    _mock_all(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "m@x.fr", "password": "pw"})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
    assert result["data"]["facility_url"] == "monclub"
    assert result["result"].unique_id == "u-1"


async def test_config_flow_bad_password(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.post(CORE, status=401)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "m@x.fr", "password": "bad"})
    assert result["errors"] == {"base": "invalid_auth"}


async def _setup(hass, aioclient_mock, history=None):
    _mock_all(aioclient_mock, history)
    entry = MockConfigEntry(domain=DOMAIN, unique_id="u-1", data={
        "email": "m@x.fr", "password": "pw", "facility_id": "fac-1",
        "facility_url": "monclub", "facility_name": "Mon Club Saint-Brieuc"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    await _setup(hass, aioclient_mock)
    states = {s.entity_id: s for s in hass.states.async_all() if "technogym" in s.entity_id}
    for eid, s in sorted(states.items()):
        print(eid, "=", s.state)
    assert hass.states.get("sensor.technogym_mael_moves_today").state == "720"
    assert hass.states.get("sensor.technogym_mael_movergy_index").state == "74.0"
    assert hass.states.get("binary_sensor.technogym_mael_trained_today").state == "on"
    assert hass.states.get("sensor.technogym_mael_last_workout_name").state == "Full body"
    assert hass.states.get("sensor.technogym_mael_days_since_last_workout").state == "0"
    assert hass.states.get("sensor.technogym_mael_workouts_this_week").state == "2"
    nxt = hass.states.get("sensor.technogym_mael_next_booked_class")
    assert nxt.state not in ("unknown", "unavailable")
    assert nxt.attributes["nom"] == "Group Cycle"
    cal = hass.states.get("calendar.technogym_mael_workouts")
    assert cal is not None


async def test_services(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    await _setup(hass, aioclient_mock)
    resp = await hass.services.async_call(DOMAIN, "list_classes", {"only_bookable": True},
                                          blocking=True, return_response=True)
    assert [c["nom"] for c in resp["cours"]] == ["Pilates"]
    aioclient_mock.post(BOOK, json={"result": "Booked"})
    resp = await hass.services.async_call(DOMAIN, "book_class",
        {"class_id": "c-2", "partition_date": resp["cours"][0]["partition_date"]},
        blocking=True, return_response=True)
    assert resp["result"] == "Booked"
    book_call = [c for c in aioclient_mock.mock_calls if str(c[1]) == BOOK][0]
    assert book_call[2] == {"partitionDate": resp and book_call[2]["partitionDate"], "userId": "u-1", "classId": "c-2"}


async def test_new_activity_event(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    entry = await _setup(hass, aioclient_mock)
    events = []
    hass.bus.async_listen("technogym_new_activity", lambda e: events.append(e))
    h = _history()
    h["data"]["items"].insert(0, {"idCr": 112, "partitionDate": _today_partition(), "hour": 12, "minute": 0,
                                  "name": "Vélo", "type": "WorkoutSession", "duration": 20, "moves": 200, "calories": 150})
    aioclient_mock.clear_requests()
    _mock_all(aioclient_mock, h)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    assert len(events) == 1 and events[0].data["nom"] == "Vélo"
    assert hass.states.get("sensor.technogym_mael_moves_today").state == "920"


async def test_token_expired_relogin(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    entry = await _setup(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(CORE, json=LOGIN_OK)
    # 1er appel : TokenNotValid en HTTP 200 ; l'intégration doit se reconnecter
    calls = {"n": 0}
    async def hist_side(method, url, data):
        from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMockResponse
        calls["n"] += 1
        if calls["n"] == 1:
            return AiohttpClientMockResponse(method, url, json={"errors": [{"field": "TokenNotValid", "type": "Security"}]})
        return AiohttpClientMockResponse(method, url, json=_history())
    aioclient_mock.post(HIST, side_effect=hist_side)
    aioclient_mock.post(MOV, json={"data": {"movergy": 75}})
    aioclient_mock.get(CLS, json=_classes())
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.last_update_success
    assert calls["n"] == 2
    assert sum(1 for c in aioclient_mock.mock_calls if str(c[1]).startswith(CORE)) == 1


async def test_classes_failure_keeps_data(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    entry = await _setup(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(HIST, json=_history())
    aioclient_mock.post(MOV, json={"data": {"movergy": 74}})
    aioclient_mock.get(CLS, status=500)
    await entry.runtime_data.async_refresh()
    assert entry.runtime_data.last_update_success
    assert hass.states.get("sensor.technogym_mael_next_booked_class").attributes["nom"] == "Group Cycle"


async def test_auth_failure_triggers_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    entry = await _setup(hass, aioclient_mock)
    aioclient_mock.clear_requests()
    aioclient_mock.post(CORE, status=401)
    aioclient_mock.post(HIST, status=401)
    await entry.runtime_data.async_refresh()
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert any(f["context"]["source"] == "reauth" for f in flows)


async def test_legacy_login_fallback(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker):
    aioclient_mock.post(CORE, status=404)
    legacy = {"token": "tok-legacy", "expireIn": 3600, "data": LOGIN_OK["data"] | {"token": None}}
    aioclient_mock.post("https://services.mywellness.com/Application/EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9/Login", json=legacy)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"email": "m@x.fr", "password": "pw"})
    assert result["type"] is FlowResultType.CREATE_ENTRY, result
