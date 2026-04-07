"""Sensor platform for EcoGuard Curves integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfEnergy, UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTR_CURRENT_POWER, ATTR_LAST_UPDATE, ATTR_LATEST_READING, DOMAIN, UTILITY_TYPES
from .coordinator import CurvesDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up EcoGuard Curves sensors from a config entry."""
    coordinator: CurvesDataUpdateCoordinator = hass.data[DOMAIN][config_entry.entry_id]

    # Get utilities from coordinator
    utilities = coordinator.utilities

    entities = []
    for utility_key in utilities:
        if utility_key not in UTILITY_TYPES:
            _LOGGER.warning(f"Unknown utility type: {utility_key}, skipping sensors")
            continue

        # Create common sensors for this utility (consumption + cost)
        entities.extend(
            [
                UtilityConsumptionSensor(coordinator, config_entry, utility_key),
                UtilityDailyConsumptionSensor(coordinator, config_entry, utility_key),
                UtilityMonthlyConsumptionSensor(coordinator, config_entry, utility_key),
                UtilityLastMonthConsumptionSensor(coordinator, config_entry, utility_key),
                UtilityPast12MonthsConsumptionSensor(coordinator, config_entry, utility_key),
                UtilityCostSensor(coordinator, config_entry, utility_key),
                UtilityDailyCostSensor(coordinator, config_entry, utility_key),
                UtilityMonthlyCostSensor(coordinator, config_entry, utility_key),
                UtilityYearlyCostSensor(coordinator, config_entry, utility_key),
                UtilityLastMonthCostSensor(coordinator, config_entry, utility_key),
                UtilityPast12MonthsCostSensor(coordinator, config_entry, utility_key),
                UtilityEstimatedDailyCostSensor(coordinator, config_entry, utility_key),
                UtilityEstimatedMonthlyCostSensor(coordinator, config_entry, utility_key),
                UtilityEstimatedLastMonthCostSensor(coordinator, config_entry, utility_key),
                UtilityTariffRateSensor(coordinator, config_entry, utility_key),
            ]
        )

        # CO2 sensors only for electricity and hot water (other utilities seem to not have CO2 data)
        if utility_key in ["electricity", "hot_water"]:
            entities.extend(
                [
                    UtilityCO2Sensor(coordinator, config_entry, utility_key),
                    UtilityDailyCO2Sensor(coordinator, config_entry, utility_key),
                    UtilityMonthlyCO2Sensor(coordinator, config_entry, utility_key),
                    UtilityYearlyCO2Sensor(coordinator, config_entry, utility_key),
                    UtilityLastMonthCO2Sensor(coordinator, config_entry, utility_key),
                    UtilityPast12MonthsCO2Sensor(coordinator, config_entry, utility_key),
                ]
            )

    # Add service fee sensor (once, not per utility)
    entities.append(EcoGuardServiceFeeSensor(coordinator, config_entry))

    async_add_entities(entities)


class UtilityConsumptionSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of total Consumption sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the Consumption sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        # Set device class and unit based on utility type
        device_class = self._utility_config["device_class_consumption"]
        if device_class == "energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif device_class == "water":
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        else:
            self._attr_device_class = None
            self._attr_native_unit_of_measurement = self._utility_config["unit_consumption"]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Consumption"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_consumption"

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            consumption = self.coordinator.data[self._utility_key].get("consumption", 0.0)
            return round(consumption, 3) if consumption is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        if not self.coordinator.data or self._utility_key not in self.coordinator.data:
            return {}

        data = self.coordinator.data[self._utility_key]
        attrs: dict[str, Any] = {
            ATTR_CURRENT_POWER: round(data.get("current_power", 0.0), 2),
            ATTR_LATEST_READING: data.get("latest_reading"),
        }

        # Add last update time if available
        if (
            hasattr(self.coordinator, "last_update_success_time")
            and self.coordinator.last_update_success_time
        ):
            attrs[ATTR_LAST_UPDATE] = self.coordinator.last_update_success_time.isoformat()

        return attrs


class UtilityDailyConsumptionSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of daily Consumption sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the daily Consumption sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        # Set device class and unit based on utility type
        device_class = self._utility_config["device_class_consumption"]
        if device_class == "energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif device_class == "water":
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        else:
            self._attr_device_class = None
            self._attr_native_unit_of_measurement = self._utility_config["unit_consumption"]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Daily Consumption"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_daily_consumption"

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            consumption = self.coordinator.data[self._utility_key].get("daily_consumption", 0.0)
            return round(consumption, 3) if consumption is not None else None
        return None


class UtilityMonthlyConsumptionSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of monthly Consumption sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the monthly Consumption sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        # Set device class and unit based on utility type
        device_class = self._utility_config["device_class_consumption"]
        if device_class == "energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif device_class == "water":
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        else:
            self._attr_device_class = None
            self._attr_native_unit_of_measurement = self._utility_config["unit_consumption"]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Monthly Consumption"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_monthly_consumption"

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            consumption = self.coordinator.data[self._utility_key].get("monthly_consumption", 0.0)
            return round(consumption, 3) if consumption is not None else None
        return None


class UtilityLastMonthConsumptionSensor(
    CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity
):
    """Representation of last month Consumption sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the last month Consumption sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        # Set device class and unit based on utility type
        device_class = self._utility_config["device_class_consumption"]
        if device_class == "energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif device_class == "water":
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        else:
            self._attr_device_class = None
            self._attr_native_unit_of_measurement = self._utility_config["unit_consumption"]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Last Month Consumption"
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_last_month_consumption"
        )

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            consumption = self.coordinator.data[self._utility_key].get(
                "prev_month_consumption", 0.0
            )
            return round(consumption, 3) if consumption is not None else None
        return None


class UtilityCostSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of current Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Cost"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_cost"

    @property
    def native_value(self) -> float | None:
        """Return the cost for the latest period."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("current_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None


class UtilityDailyCostSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of daily Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the daily Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Daily Cost"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_daily_cost"

    @property
    def native_value(self) -> float | None:
        """Return the total cost for today."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("daily_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None


class UtilityMonthlyCostSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of monthly Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the monthly Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Monthly Cost"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_monthly_cost"

    @property
    def native_value(self) -> float | None:
        """Return the total cost for this month."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("monthly_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None


class UtilityYearlyCostSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of yearly Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the yearly Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Year to Date Cost"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_yearly_cost"

    @property
    def native_value(self) -> float | None:
        """Return the total cost for this year."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("yearly_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None


class UtilityLastMonthCostSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of last month Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the last month Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Last Month Cost"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_last_month_cost"

    @property
    def native_value(self) -> float | None:
        """Return the total cost for last month."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("prev_month_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None


class UtilityCO2Sensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of current CO2 sensor for any utility."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_native_unit_of_measurement = "kg"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the CO2 sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} CO2"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_co2"

    @property
    def native_value(self) -> float | None:
        """Return the current CO2 emission."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            co2 = self.coordinator.data[self._utility_key].get("current_co2", 0.0)
            return round(co2, 3) if co2 is not None else None
        return None


class UtilityDailyCO2Sensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of daily CO2 sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kg"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the daily CO2 sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Daily CO2"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_daily_co2"

    @property
    def native_value(self) -> float | None:
        """Return the CO2 emission for today."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            co2 = self.coordinator.data[self._utility_key].get("daily_co2", 0.0)
            return round(co2, 3) if co2 is not None else None
        return None


class UtilityMonthlyCO2Sensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of monthly CO2 sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kg"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the monthly CO2 sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Monthly CO2"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_monthly_co2"

    @property
    def native_value(self) -> float | None:
        """Return the CO2 emission for this month."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            co2 = self.coordinator.data[self._utility_key].get("monthly_co2", 0.0)
            return round(co2, 3) if co2 is not None else None
        return None


class UtilityYearlyCO2Sensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of yearly CO2 sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kg"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the yearly CO2 sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Year to Date CO2"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_yearly_co2"

    @property
    def native_value(self) -> float | None:
        """Return the CO2 emission for this year."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            co2 = self.coordinator.data[self._utility_key].get("yearly_co2", 0.0)
            return round(co2, 3) if co2 is not None else None
        return None


class UtilityLastMonthCO2Sensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of last month CO2 sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kg"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the last month CO2 sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Last Month CO2"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_last_month_co2"

    @property
    def native_value(self) -> float | None:
        """Return the CO2 emission for last month."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            co2 = self.coordinator.data[self._utility_key].get("prev_month_co2", 0.0)
            return round(co2, 3) if co2 is not None else None
        return None


class UtilityEstimatedDailyCostSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of estimated daily Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the estimated daily Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Estimated Daily Cost"
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_estimated_daily_cost"
        )

    @property
    def native_value(self) -> float | None:
        """Return the estimated cost for today."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("estimated_daily_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        if not self.coordinator.data or self._utility_key not in self.coordinator.data:
            return {}

        data = self.coordinator.data[self._utility_key]
        attrs = {
            "attribution": "Estimated using latest tariff rates",
            "tariff_rate": data.get("tariff_rate"),
            "tariff_last_updated": data.get("tariff_last_updated"),
        }
        return attrs


class UtilityEstimatedMonthlyCostSensor(
    CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity
):
    """Representation of estimated monthly Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the estimated monthly Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Estimated Monthly Cost"
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_estimated_monthly_cost"
        )

    @property
    def native_value(self) -> float | None:
        """Return the estimated cost for this month."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("estimated_monthly_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        if not self.coordinator.data or self._utility_key not in self.coordinator.data:
            return {}

        data = self.coordinator.data[self._utility_key]
        attrs = {
            "attribution": "Estimated using latest tariff rates",
            "tariff_rate": data.get("tariff_rate"),
            "tariff_last_updated": data.get("tariff_last_updated"),
        }
        return attrs


class UtilityEstimatedLastMonthCostSensor(
    CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity
):
    """Representation of estimated last month Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the estimated last month Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Estimated Last Month Cost"
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_estimated_last_month_cost"
        )

    @property
    def native_value(self) -> float | None:
        """Return the estimated cost for last month."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("estimated_prev_month_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        if not self.coordinator.data or self._utility_key not in self.coordinator.data:
            return {}

        data = self.coordinator.data[self._utility_key]
        attrs = {
            "attribution": "Estimated using latest tariff rates",
            "tariff_rate": data.get("tariff_rate"),
            "tariff_last_updated": data.get("tariff_last_updated"),
        }
        return attrs


class UtilityTariffRateSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of tariff rate sensor for any utility."""

    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the tariff rate sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")

        # Set unit based on utility type
        consumption_unit = self._utility_config["unit_consumption"]
        self._attr_native_unit_of_measurement = f"{currency}/{consumption_unit}"

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Last Known Rate"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_tariff_rate"

    @property
    def native_value(self) -> float | None:
        """Return the current tariff rate."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            rate = self.coordinator.data[self._utility_key].get("tariff_rate", 0.0)
            return round(rate, 2) if rate is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        if not self.coordinator.data or self._utility_key not in self.coordinator.data:
            return {}

        data = self.coordinator.data[self._utility_key]
        attrs = {
            "attribution": "Tariff rate from latest billing period",
            "last_updated": data.get("tariff_last_updated"),
        }
        return attrs


class UtilityPast12MonthsConsumptionSensor(
    CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity
):
    """Representation of past 12 months Consumption sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the past 12 months Consumption sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        # Set device class and unit based on utility type
        device_class = self._utility_config["device_class_consumption"]
        if device_class == "energy":
            self._attr_device_class = SensorDeviceClass.ENERGY
            self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        elif device_class == "water":
            self._attr_device_class = SensorDeviceClass.WATER
            self._attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS
        else:
            self._attr_device_class = None
            self._attr_native_unit_of_measurement = self._utility_config["unit_consumption"]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Past 12 Months Consumption"
        self._attr_unique_id = (
            f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_past_12_months_consumption"
        )

    @property
    def native_value(self) -> float | None:
        """Return the state of the sensor."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            consumption = self.coordinator.data[self._utility_key].get(
                "past_12_months_consumption", 0.0
            )
            return round(consumption, 3) if consumption is not None else None
        return None


class UtilityPast12MonthsCostSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of past 12 months Cost sensor for any utility."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the past 12 months Cost sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Past 12 Months Cost"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_past_12_months_cost"

    @property
    def native_value(self) -> float | None:
        """Return the total cost for past 12 months."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            cost = self.coordinator.data[self._utility_key].get("past_12_months_cost", 0.0)
            return round(cost, 4) if cost is not None else None
        return None


class UtilityPast12MonthsCO2Sensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of past 12 months CO2 sensor for any utility."""

    _attr_state_class = SensorStateClass.TOTAL
    _attr_native_unit_of_measurement = "kg"
    _attr_should_poll = False

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
        utility_key: str,
    ) -> None:
        """Initialize the past 12 months CO2 sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry
        self._utility_key = utility_key
        self._utility_config = UTILITY_TYPES[utility_key]

        utility_name = self._utility_config["name"]
        self._attr_name = f"{utility_name} Past 12 Months CO2"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_{utility_key}_past_12_months_co2"

    @property
    def native_value(self) -> float | None:
        """Return the CO2 emission for past 12 months."""
        if self.coordinator.data and self._utility_key in self.coordinator.data:
            co2 = self.coordinator.data[self._utility_key].get("past_12_months_co2", 0.0)
            return round(co2, 3) if co2 is not None else None
        return None


class EcoGuardServiceFeeSensor(CoordinatorEntity[CurvesDataUpdateCoordinator], SensorEntity):
    """Representation of EcoGuard monthly service fee sensor."""

    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_state_class = SensorStateClass.TOTAL
    _attr_should_poll = False
    _attr_entity_registry_enabled_default = False  # Hidden by default

    def __init__(
        self,
        coordinator: CurvesDataUpdateCoordinator,
        config_entry: ConfigEntry,
    ) -> None:
        """Initialize the service fee sensor."""
        super().__init__(coordinator)
        self._config_entry = config_entry

        currency = config_entry.data.get("currency") or config_entry.options.get("currency", "SEK")
        self._attr_native_unit_of_measurement = currency

        self._attr_name = "EcoGuard Service Fee"
        self._attr_unique_id = f"{DOMAIN}_{config_entry.entry_id}_service_fee"

    @property
    def native_value(self) -> float | None:
        """Return the monthly service fee."""
        if self.coordinator.data:
            fee = self.coordinator.data.get("service_fee", 0.0)
            return round(fee, 2) if fee is not None else None
        return None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra attributes."""
        if not self.coordinator.data:
            return {}

        attrs = {
            "attribution": "Monthly service fee from latest billing",
            "last_updated": self.coordinator.data.get("service_fee_last_updated"),
            "unit_of_measurement_per": "month",
        }
        return attrs
