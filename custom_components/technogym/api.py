"""Client asynchrone pour le cloud Technogym Mywellness.

Technogym ne publie pas d'API personnelle : ce client parle la même API JSON
que l'application web officielle (endusernext.mywellness.com). Endpoints
documentés par les projets open source health_mcp/technogym (MIT),
CrowdSalat/medi-bucher et AeonDave/myWellnezz.

* POST core.mywellness.com/v2/enduser/authentication/login        -> jeton Bearer
* POST services.mywellness.com/{facility}/Training/User/{id}/ActivityHistory
* POST services.mywellness.com/{facility}/Training/User/{id}/MyMovergy
* GET  calendar.mywellness.com/v2/enduser/class/Search
* POST calendar.mywellness.com/v2/enduser/class/Book | Unbook

Si Technogym change son flux, c'est ce fichier qu'il faudra adapter.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CORE_URL = "https://core.mywellness.com"
SERVICES_URL = "https://services.mywellness.com"
CALENDAR_URL = "https://calendar.mywellness.com"

APP_ID = "EC1D38D7-D359-48D0-A60C-D8C0B8FB9DF9"
APP_NAME = "enduserweb"
APP_VERSION = "1.18.1-2105"
CULTURE = "fr-FR"

LOGIN_PATH = "/v2/enduser/authentication/login"
LEGACY_LOGIN_PATH = f"/Application/{APP_ID}/Login"
HISTORY_PATH = "/{facility}/Training/User/{user_id}/ActivityHistory"
MOVERGY_PATH = "/{facility}/Training/User/{user_id}/MyMovergy"
CLASS_SEARCH_PATH = "/v2/enduser/class/Search"
CLASS_BOOK_PATH = "/v2/enduser/class/Book"
CLASS_UNBOOK_PATH = "/v2/enduser/class/Unbook"

TIMEOUT = aiohttp.ClientTimeout(total=45)

_AUTH_ERROR_MARKERS = {
    "tokennotvalid",
    "invalidtoken",
    "tokenexpired",
    "notauthenticated",
    "unauthorized",
    "usernotlogged",
}
_BAD_CREDENTIALS_MARKERS = {
    "invalidcredentials",
    "wrongusernameorpassword",
    "usernotfound",
    "invalidusernameorpassword",
    "loginfailed",
}


class MywellnessError(Exception):
    """Erreur générique de l'API."""


class MywellnessAuthError(MywellnessError):
    """Identifiants refusés ou session impossible à établir."""


class MywellnessConnectionError(MywellnessError):
    """Serveur injoignable ou réponse illisible."""


@dataclass(slots=True)
class Facility:
    """Club Technogym rattaché au compte."""

    id: str
    url: str
    name: str


@dataclass(slots=True)
class UserInfo:
    """Profil Mywellness minimal."""

    user_id: str
    first_name: str
    last_name: str
    culture: str
    facilities: list[Facility] = field(default_factory=list)

    @property
    def display_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip() or "Mywellness"


class MywellnessClient:
    """Client Mywellness à authentification paresseuse.

    Connexion au premier appel ; une erreur qui ressemble à un jeton expiré
    déclenche une reconnexion puis un seul nouvel essai.
    """

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        facility_url: str | None = None,
        facility_id: str | None = None,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._facility_url = facility_url
        self._facility_id = facility_id
        self._token: str | None = None
        self._user: UserInfo | None = None
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------ props

    @property
    def user(self) -> UserInfo | None:
        return self._user

    @property
    def facility_id(self) -> str | None:
        if self._facility_id:
            return self._facility_id
        if self._user and self._user.facilities:
            return self._user.facilities[0].id
        return None

    @property
    def facility_url(self) -> str | None:
        if self._facility_url:
            return self._facility_url
        if self._user and self._user.facilities:
            return self._user.facilities[0].url
        return None

    # ---------------------------------------------------------------- headers

    def _headers(self, auth: bool = True) -> dict[str, str]:
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-MWAPPS-APPID": APP_ID,
            "X-MWAPPS-CLIENT": APP_NAME,
            "X-MWAPPS-CLIENTVERSION": f"{APP_VERSION},{APP_NAME}",
            "X-MWAPPS-TZ-OLSON": "Europe/Paris",
            "User-Agent": "HomeAssistant-Technogym/1.0",
        }
        if auth and self._token:
            headers["Authorization"] = f"Bearer {self._token}"
        if self.facility_id:
            headers["X-MWAPPS-FACILITYID"] = self.facility_id
        return headers

    # ------------------------------------------------------------------- auth

    async def login(self) -> UserInfo:
        """Se connecte et mémorise jeton + profil."""
        async with self._lock:
            body = await self._login_v2()
            if body is None:
                body = await self._login_legacy()

            data = body.get("data") if isinstance(body.get("data"), dict) else body
            token = body.get("token") or data.get("token")
            user = _parse_user(data)
            if not token or user is None:
                raise MywellnessAuthError(
                    f"Réponse de connexion inattendue ({_errors_text(body) or 'sans jeton'})"
                )
            self._token = str(token).strip()
            self._user = user
            _LOGGER.debug(
                "Connecté à Mywellness (user %s, %d club(s))",
                user.user_id,
                len(user.facilities),
            )
            return user

    async def _login_v2(self) -> dict[str, Any] | None:
        """Flux de l'appli web actuelle. None = essayer le flux historique."""
        url = f"{CORE_URL}{LOGIN_PATH}?_c={CULTURE}"
        payload = {
            "username": self._email,
            "password": self._password,
            "keepMeLoggedIn": True,
        }
        try:
            async with self._session.post(
                url, json=payload, headers=self._headers(auth=False), timeout=TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise MywellnessAuthError("Email ou mot de passe refusé")
                if resp.status >= 400:
                    _LOGGER.debug("Login v2 HTTP %s, bascule sur le flux historique", resp.status)
                    return None
                body = await resp.json(content_type=None)
        except MywellnessAuthError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            _LOGGER.debug("Login v2 indisponible (%s), bascule sur le flux historique", err)
            return None

        if not isinstance(body, dict):
            return None
        if body.get("errors"):
            if _looks_like_bad_credentials(body):
                raise MywellnessAuthError("Email ou mot de passe refusé")
            _LOGGER.debug("Login v2 erreur %s, bascule", _errors_text(body))
            return None
        return body

    async def _login_legacy(self) -> dict[str, Any]:
        """Flux historique de l'appli mobile (services.mywellness.com)."""
        url = f"{SERVICES_URL}{LEGACY_LOGIN_PATH}?_c={CULTURE}"
        payload = {
            "domain": "com.mywellness",
            "keepMeLoggedIn": True,
            "username": self._email,
            "password": self._password,
        }
        try:
            async with self._session.post(
                url, json=payload, headers=self._headers(auth=False), timeout=TIMEOUT
            ) as resp:
                if resp.status in (401, 403):
                    raise MywellnessAuthError("Email ou mot de passe refusé")
                resp.raise_for_status()
                body = await resp.json(content_type=None)
        except MywellnessAuthError:
            raise
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as err:
            raise MywellnessConnectionError(f"Connexion Mywellness impossible : {err}") from err

        if not isinstance(body, dict):
            raise MywellnessConnectionError("Réponse de connexion illisible")
        if body.get("errors"):
            raise MywellnessAuthError(f"Connexion refusée : {_errors_text(body)}")
        return body

    async def ensure_login(self) -> UserInfo:
        if self._user is not None and self._token:
            return self._user
        return await self.login()

    # ---------------------------------------------------------------- requests

    async def _request(
        self,
        method: str,
        url: str,
        *,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> Any:
        """Requête authentifiée ; une reconnexion automatique si le jeton a expiré."""
        await self.ensure_login()
        for attempt in (1, 2):
            try:
                async with self._session.request(
                    method,
                    url,
                    json=json_body,
                    params=params,
                    headers=self._headers(),
                    timeout=TIMEOUT,
                ) as resp:
                    status = resp.status
                    if status in (401, 403):
                        body: Any = {"errors": [{"details": f"HTTP {status}"}]}
                    else:
                        if status >= 400:
                            text = await resp.text()
                            raise MywellnessError(f"HTTP {status} sur {url} : {text[:200]}")
                        body = await resp.json(content_type=None)
            except (aiohttp.ClientError, asyncio.TimeoutError) as err:
                raise MywellnessConnectionError(f"{url} injoignable : {err}") from err
            except ValueError as err:
                raise MywellnessConnectionError(f"Réponse non JSON de {url}") from err

            errors = body.get("errors") if isinstance(body, dict) else None
            if not errors:
                if isinstance(body, dict) and "data" in body:
                    return body["data"]
                return body
            if attempt == 1 and _looks_like_auth_error(errors, status):
                _LOGGER.debug("Jeton Mywellness expiré, reconnexion")
                self._token = None
                self._user = None
                await self.login()
                continue
            if _looks_like_auth_error(errors, status):
                raise MywellnessAuthError(_errors_text(body))
            raise MywellnessError(_errors_text(body))
        raise MywellnessError("unreachable")

    def _services_url(self, path: str) -> str:
        if not self._user:
            raise MywellnessError("Non connecté")
        facility = self.facility_url
        if not facility:
            raise MywellnessError("Aucun club rattaché à ce compte Mywellness")
        return SERVICES_URL + path.format(facility=facility, user_id=self._user.user_id)

    # -------------------------------------------------------------- public API

    async def async_get_activity_history(
        self, start: date, end: date, only_workouts: bool = False
    ) -> list[dict[str, Any]]:
        """Toutes les activités (séances, cours, cardio, activités externes)."""
        await self.ensure_login()
        content: dict[str, Any] = {"startDay": _ymd(start), "endDay": _ymd(end)}
        if only_workouts:
            content["justThisType"] = "WorkoutSession"
        data = await self._request("POST", self._services_url(HISTORY_PATH), json_body=content)
        items = (data or {}).get("items") if isinstance(data, dict) else None
        return list(items or [])

    async def async_get_movergy(self) -> float | None:
        await self.ensure_login()
        data = await self._request("POST", self._services_url(MOVERGY_PATH), json_body={})
        if isinstance(data, dict):
            value = data.get("movergy")
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        return None

    async def async_search_classes(self, start: date, end: date) -> list[dict[str, Any]]:
        """Planning des cours du club (avec isParticipant pour l'utilisateur)."""
        await self.ensure_login()
        if not self.facility_id:
            return []
        data = await self._request(
            "GET",
            f"{CALENDAR_URL}{CLASS_SEARCH_PATH}",
            params={
                "facilityId": self.facility_id,
                "fromDate": start.isoformat(),
                "toDate": end.isoformat(),
                "eventTypes": "Class",
            },
        )
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for key in ("items", "eventItems", "classes"):
                if isinstance(data.get(key), list):
                    return data[key]
        return []

    async def async_book_class(self, class_id: str, partition_date: int) -> dict[str, Any]:
        user = await self.ensure_login()
        data = await self._request(
            "POST",
            f"{CALENDAR_URL}{CLASS_BOOK_PATH}",
            json_body={
                "partitionDate": int(partition_date),
                "userId": user.user_id,
                "classId": class_id,
            },
        )
        return data if isinstance(data, dict) else {"result": str(data)}

    async def async_unbook_class(self, class_id: str, partition_date: int) -> dict[str, Any]:
        user = await self.ensure_login()
        data = await self._request(
            "POST",
            f"{CALENDAR_URL}{CLASS_UNBOOK_PATH}",
            json_body={
                "partitionDate": int(partition_date),
                "userId": user.user_id,
                "classId": class_id,
            },
        )
        return data if isinstance(data, dict) else {"result": str(data)}


# ---------------------------------------------------------------------- helpers


def _parse_user(data: dict[str, Any]) -> UserInfo | None:
    uc = data.get("userContext") or data.get("user") or {}
    uid = uc.get("id")
    if not uid:
        return None
    facilities = [
        Facility(id=str(f.get("id", "")), url=str(f.get("url", "")), name=str(f.get("name", "")))
        for f in (data.get("facilities") or uc.get("facilities") or [])
        if f.get("id")
    ]
    return UserInfo(
        user_id=str(uid),
        first_name=str(uc.get("firstName") or ""),
        last_name=str(uc.get("lastName") or ""),
        culture=str(uc.get("defaultCulture") or CULTURE),
        facilities=facilities,
    )


def _errors_text(body: Any) -> str:
    if not isinstance(body, dict):
        return ""
    return "; ".join(
        str(e.get("errorMessage") or e.get("message") or e.get("details") or e.get("field") or e)
        for e in body.get("errors") or []
        if isinstance(e, dict)
    )


def _error_tokens(errors: list[Any]) -> set[str]:
    out: set[str] = set()
    for e in errors:
        if not isinstance(e, dict):
            continue
        for key in ("details", "field", "message", "errorMessage", "type"):
            val = e.get(key)
            if val:
                out.add(str(val).replace(" ", "").lower())
    return out


def _looks_like_auth_error(errors: list[Any], status: int) -> bool:
    if status in (401, 403):
        return True
    for tok in _error_tokens(errors):
        if tok in _AUTH_ERROR_MARKERS or "token" in tok:
            return True
    return False


def _looks_like_bad_credentials(body: dict[str, Any]) -> bool:
    for tok in _error_tokens(body.get("errors") or []):
        if tok in _BAD_CREDENTIALS_MARKERS or "password" in tok or "credential" in tok:
            return True
    return False


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")
