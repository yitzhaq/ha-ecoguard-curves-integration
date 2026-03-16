"""Config flow for EcoGuard Curves integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
    SelectOptionDict,
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
    TextSelector,
    TextSelectorConfig,
    TextSelectorType,
)

from .api import CurvesAPIClient, CurvesAPIError
from .const import (
    CONF_CURRENCY,
    CONF_DOMAIN_CODE,
    CONF_MEASURING_POINT_ID,
    CONF_NODE_ID,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    CONF_USERNAME,
    CONF_UTILITIES,
    CONF_VAT_RATE,
    DEFAULT_CURRENCY,
    DEFAULT_UPDATE_INTERVAL,
    DEFAULT_VAT_RATE,
    DOMAIN,
    UTILITY_TYPES,
)

_LOGGER = logging.getLogger(__name__)


class ElectricityConsumptionConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for EcoGuard Curves."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            # Validate credentials by attempting authentication
            try:
                client = CurvesAPIClient(
                    self.hass,
                    user_input[CONF_USERNAME],
                    user_input[CONF_PASSWORD],
                    user_input[CONF_DOMAIN_CODE],
                )
                await client.authenticate()

                # Create unique ID from domain code and username
                await self.async_set_unique_id(
                    f"{DOMAIN}_{user_input[CONF_DOMAIN_CODE]}_{user_input[CONF_USERNAME]}"
                )
                self._abort_if_unique_id_configured()

                # Store the validated config
                return self.async_create_entry(
                    title=f"EcoGuard Curves - {user_input[CONF_DOMAIN_CODE]}",
                    data=user_input,
                )
            except CurvesAPIError as err:
                errors["base"] = f"authentication_failed: {str(err)}"
            except Exception as err:
                _LOGGER.exception("Unexpected exception")
                errors["base"] = f"unknown: {str(err)}"

        # Create utility options from UTILITY_TYPES
        utility_options = [
            SelectOptionDict(value=key, label=config["name"])
            for key, config in UTILITY_TYPES.items()
        ]

        schema = vol.Schema(
            {
                vol.Required(CONF_USERNAME): TextSelector(TextSelectorConfig()),
                vol.Required(CONF_PASSWORD): TextSelector(
                    TextSelectorConfig(type=TextSelectorType.PASSWORD)
                ),
                vol.Required(CONF_DOMAIN_CODE): TextSelector(TextSelectorConfig()),
                vol.Required(CONF_NODE_ID): TextSelector(TextSelectorConfig()),
                vol.Optional(CONF_MEASURING_POINT_ID): TextSelector(TextSelectorConfig()),
                vol.Required(
                    CONF_UTILITIES,
                    default=["electricity"],
                ): SelectSelector(
                    SelectSelectorConfig(
                        options=utility_options,
                        multiple=True,
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                vol.Required(CONF_UPDATE_INTERVAL, default=DEFAULT_UPDATE_INTERVAL): NumberSelector(
                    NumberSelectorConfig(
                        min=60,
                        max=3600,
                        step=60,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="seconds",
                    )
                ),
                vol.Required(CONF_CURRENCY, default=DEFAULT_CURRENCY): TextSelector(
                    TextSelectorConfig()
                ),
                vol.Required(CONF_VAT_RATE, default=DEFAULT_VAT_RATE): NumberSelector(
                    NumberSelectorConfig(
                        min=0,
                        max=100,
                        step=0.1,
                        mode=NumberSelectorMode.BOX,
                        unit_of_measurement="%",
                    )
                ),
            }
        )

        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> config_entries.OptionsFlow:
        """Create the options flow."""
        return ElectricityConsumptionOptionsFlowHandler()


class ElectricityConsumptionOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle options flow for EcoGuard Curves."""

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Manage the options."""
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        # Create utility options from UTILITY_TYPES
        utility_options = [
            SelectOptionDict(value=key, label=config["name"])
            for key, config in UTILITY_TYPES.items()
        ]

        # Get current utilities, defaulting to electricity for backward compatibility
        current_utilities = self.config_entry.options.get(
            CONF_UTILITIES, self.config_entry.data.get(CONF_UTILITIES, ["electricity"])
        )

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_NODE_ID,
                        default=self.config_entry.options.get(
                            CONF_NODE_ID, self.config_entry.data.get(CONF_NODE_ID, "")
                        ),
                    ): TextSelector(TextSelectorConfig()),
                    vol.Optional(
                        CONF_MEASURING_POINT_ID,
                        default=self.config_entry.options.get(
                            CONF_MEASURING_POINT_ID,
                            self.config_entry.data.get(CONF_MEASURING_POINT_ID, ""),
                        ),
                    ): TextSelector(TextSelectorConfig()),
                    vol.Required(
                        CONF_UTILITIES,
                        default=current_utilities,
                    ): SelectSelector(
                        SelectSelectorConfig(
                            options=utility_options,
                            multiple=True,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(
                        CONF_UPDATE_INTERVAL,
                        default=self.config_entry.options.get(
                            CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=60,
                            max=3600,
                            step=60,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="seconds",
                        )
                    ),
                    vol.Required(
                        CONF_CURRENCY,
                        default=self.config_entry.options.get(
                            CONF_CURRENCY,
                            self.config_entry.data.get(CONF_CURRENCY, DEFAULT_CURRENCY),
                        ),
                    ): TextSelector(TextSelectorConfig()),
                    vol.Required(
                        CONF_VAT_RATE,
                        default=self.config_entry.options.get(
                            CONF_VAT_RATE,
                            self.config_entry.data.get(CONF_VAT_RATE, DEFAULT_VAT_RATE),
                        ),
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=0,
                            max=100,
                            step=0.1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="%",
                        )
                    ),
                }
            ),
        )
