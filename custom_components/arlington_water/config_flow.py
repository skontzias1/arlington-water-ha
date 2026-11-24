"""Config flow for Arlington Water."""

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.selector import (
    BooleanSelector,
    EntitySelector,
    EntitySelectorConfig,
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .client import CannotConnect, Client, InvalidAuth
from .const import CONF_BLOCKED, CONF_METERS, CONF_REFUSE, CONF_WINTER_TG, DOMAIN
from .portal import BadPage

_LOGGER = logging.getLogger(__name__)
PASSWORD = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))


async def _validate(
    hass: HomeAssistant, username: str, password: str
) -> dict[str, str]:
    """Sign in once and check the account is readable; return form errors."""
    client = Client(hass, username, password)
    try:
        await client.fetch()
    except InvalidAuth:
        return {"base": "invalid_auth"}
    except BadPage as err:
        _LOGGER.warning("Arlington Water account check: %s", err)
        return {"base": "unsupported"}
    except CannotConnect as err:
        _LOGGER.warning("Arlington Water sign-in check: %s", err)
        return {"base": "cannot_connect"}
    except Exception as err:  # noqa: BLE001
        _LOGGER.warning("Arlington Water sign-in check: %s", type(err).__name__)
        return {"base": "unknown"}
    finally:
        client.close()
    return {}


class ArlingtonConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle setup and reauth."""

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the login."""
        errors: dict[str, str] = {}
        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()
            errors = await _validate(
                self.hass, user_input[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_create_entry(title="Arlington Water", data=user_input)
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_USERNAME): str,
                    vol.Required(CONF_PASSWORD): PASSWORD,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Start reauth."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the new password."""
        errors: dict[str, str] = {}
        entry = self._get_reauth_entry()
        if user_input is not None:
            errors = await _validate(
                self.hass, entry.data[CONF_USERNAME], user_input[CONF_PASSWORD]
            )
            if not errors:
                return self.async_update_reload_and_abort(
                    entry, data_updates={**user_input, CONF_BLOCKED: False}
                )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): PASSWORD}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> "ArlingtonOptionsFlow":
        """Return the options flow."""
        return ArlingtonOptionsFlow()


class ArlingtonOptionsFlow(OptionsFlowWithReload):
    """Choose meters and the sewer cap for the estimate."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Ask for the estimate settings."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        schema = vol.Schema(
            {
                vol.Optional(CONF_METERS, default=[]): EntitySelector(
                    EntitySelectorConfig(
                        domain="sensor", device_class="water", multiple=True
                    )
                ),
                vol.Optional(CONF_WINTER_TG): NumberSelector(
                    NumberSelectorConfig(
                        min=0, max=1000, step=1, mode=NumberSelectorMode.BOX
                    )
                ),
                vol.Required(CONF_REFUSE, default=True): BooleanSelector(),
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                schema, self.config_entry.options
            ),
        )
