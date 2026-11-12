"""Assistant de configuration Technogym (Mywellness)."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import (
    MywellnessAuthError,
    MywellnessClient,
    MywellnessConnectionError,
    MywellnessError,
    UserInfo,
)
from .const import (
    CONF_CLASS_DAYS,
    CONF_FACILITY_ID,
    CONF_FACILITY_NAME,
    CONF_FACILITY_URL,
    CONF_HISTORY_DAYS,
    CONF_SCAN_INTERVAL,
    DEFAULT_CLASS_DAYS,
    DEFAULT_HISTORY_DAYS,
    DEFAULT_SCAN_INTERVAL_MIN,
    DOMAIN,
    MAX_SCAN_INTERVAL_MIN,
    MIN_SCAN_INTERVAL_MIN,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_EMAIL): TextSelector(TextSelectorConfig(type=TextSelectorType.EMAIL)),
        vol.Required(CONF_PASSWORD): TextSelector(
            TextSelectorConfig(type=TextSelectorType.PASSWORD)
        ),
    }
)


class TechnogymConfigFlow(ConfigFlow, domain=DOMAIN):
    """Gère l'ajout d'un compte Mywellness."""

    VERSION = 1

    def __init__(self) -> None:
        self._email: str = ""
        self._password: str = ""
        self._user: UserInfo | None = None

    async def _try_login(self, email: str, password: str) -> tuple[UserInfo | None, str | None]:
        client = MywellnessClient(async_get_clientsession(self.hass), email, password)
        try:
            return await client.login(), None
        except MywellnessAuthError:
            return None, "invalid_auth"
        except MywellnessConnectionError:
            return None, "cannot_connect"
        except MywellnessError:
            _LOGGER.exception("Erreur Mywellness inattendue")
            return None, "unknown"

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip()
            self._password = user_input[CONF_PASSWORD]
            user, error = await self._try_login(self._email, self._password)
            if error:
                errors["base"] = error
            elif user is not None:
                await self.async_set_unique_id(user.user_id)
                self._abort_if_unique_id_configured()
                self._user = user
                if not user.facilities:
                    return self.async_abort(reason="no_facility")
                if len(user.facilities) == 1:
                    return self._create(user.facilities[0].id)
                return await self.async_step_facility()

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(
                USER_SCHEMA, {CONF_EMAIL: self._email}
            ),
            errors=errors,
        )

    async def async_step_facility(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._user is not None
        if user_input is not None:
            return self._create(user_input[CONF_FACILITY_ID])

        options = [
            SelectOptionDict(value=f.id, label=f.name or f.url) for f in self._user.facilities
        ]
        return self.async_show_form(
            step_id="facility",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_FACILITY_ID, default=self._user.facilities[0].id
                    ): SelectSelector(SelectSelectorConfig(options=options))
                }
            ),
        )

    def _create(self, facility_id: str) -> ConfigFlowResult:
        assert self._user is not None
        facility = next(f for f in self._user.facilities if f.id == facility_id)
        return self.async_create_entry(
            title=f"Technogym — {self._user.first_name or self._email}",
            data={
                CONF_EMAIL: self._email,
                CONF_PASSWORD: self._password,
                CONF_FACILITY_ID: facility.id,
                CONF_FACILITY_URL: facility.url,
                CONF_FACILITY_NAME: facility.name,
            },
        )

    # ------------------------------------------------------------- reauth

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        self._email = entry_data[CONF_EMAIL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            user, error = await self._try_login(self._email, user_input[CONF_PASSWORD])
            if error:
                errors["base"] = error
            elif user is not None:
                await self.async_set_unique_id(user.user_id)
                self._abort_if_unique_id_mismatch(reason="wrong_account")
                return self.async_update_reload_and_abort(
                    self._get_reauth_entry(),
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_PASSWORD): TextSelector(
                        TextSelectorConfig(type=TextSelectorType.PASSWORD)
                    )
                }
            ),
            description_placeholders={"email": self._email},
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        return TechnogymOptionsFlow()


class TechnogymOptionsFlow(OptionsFlow):
    """Fréquence de mise à jour et profondeur d'historique."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(
                data={k: int(v) for k, v in user_input.items()}
            )

        opts = self.config_entry.options
        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=opts.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_MIN),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=MIN_SCAN_INTERVAL_MIN,
                        max=MAX_SCAN_INTERVAL_MIN,
                        step=5,
                        unit_of_measurement="min",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_HISTORY_DAYS,
                    default=opts.get(CONF_HISTORY_DAYS, DEFAULT_HISTORY_DAYS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=7, max=120, step=1, unit_of_measurement="j",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
                vol.Required(
                    CONF_CLASS_DAYS,
                    default=opts.get(CONF_CLASS_DAYS, DEFAULT_CLASS_DAYS),
                ): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=21, step=1, unit_of_measurement="j",
                        mode=NumberSelectorMode.BOX,
                    )
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
