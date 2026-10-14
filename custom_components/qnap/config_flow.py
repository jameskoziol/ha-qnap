"""Config flow to configure qnap component."""

import logging
from typing import Any, override

import probatio
from requests.exceptions import RequestException

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.const import (
    CONF_HOST,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_SSL,
    CONF_USERNAME,
    CONF_VERIFY_SSL,
)
from homeassistant.helpers import config_validation as cv

from .api import QnapAuthError
from .const import DEFAULT_PORT, DEFAULT_SSL, DEFAULT_VERIFY_SSL, DOMAIN
from .coordinator import create_client, suppress_insecure_request_warning

DATA_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_HOST): cv.string,
        probatio.Required(CONF_USERNAME): cv.string,
        probatio.Required(CONF_PASSWORD): cv.string,
        probatio.Optional(CONF_SSL, default=DEFAULT_SSL): cv.boolean,
        probatio.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): cv.boolean,
        probatio.Optional(CONF_PORT, default=DEFAULT_PORT): probatio.Port(),
    }
)

_LOGGER = logging.getLogger(__name__)


class QnapConfigFlow(ConfigFlow, domain=DOMAIN):
    """Qnap configuration flow."""

    VERSION = 1

    async def _async_validate_input(
        self, user_input: dict[str, Any]
    ) -> tuple[dict[str, str], dict[str, Any] | None]:
        """Validate user input by connecting to the QNAP device."""
        errors: dict[str, str] = {}
        api = create_client(user_input)

        def _get_system_stats():
            with suppress_insecure_request_warning():
                return api.get_system_stats()

        try:
            stats = await self.hass.async_add_executor_job(_get_system_stats)
        except QnapAuthError:
            errors["base"] = "invalid_auth"
        except RequestException:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error")
            errors["base"] = "unknown"
        else:
            if stats is None:
                # Logged in, but the account lacks system-monitoring rights.
                errors["base"] = "invalid_auth"
            else:
                return errors, stats
        return errors, None

    @override
    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle a flow initialized by the user."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors, stats = await self._async_validate_input(user_input)
            if not errors and stats is not None:
                unique_id = stats["system"]["serial_number"]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_configured()
                title = stats["system"]["name"]
                return self.async_create_entry(title=title, data=user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(DATA_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle reconfiguration of the integration."""
        errors: dict[str, str] = {}
        reconfigure_entry = self._get_reconfigure_entry()

        if user_input is not None:
            errors, stats = await self._async_validate_input(user_input)
            if not errors and stats is not None:
                unique_id = stats["system"]["serial_number"]
                await self.async_set_unique_id(unique_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    reconfigure_entry,
                    data_updates=user_input,
                )

        suggested_values: dict[str, Any] = dict(user_input or reconfigure_entry.data)
        suggested_values.pop(CONF_PASSWORD, None)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(
                DATA_SCHEMA,
                suggested_values,
            ),
            errors=errors,
        )
