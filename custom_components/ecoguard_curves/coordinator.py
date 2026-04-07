"""Data update coordinator for Curves API."""
from __future__ import annotations

import logging
import zoneinfo
from datetime import datetime, timedelta
from datetime import timezone as dt_timezone
from typing import Any

import homeassistant.util.dt as dt_util
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.models import StatisticData, StatisticMetaData

try:
    from homeassistant.components.recorder.models import StatisticMeanType

    _USE_MEAN_TYPE = True
except ImportError:
    _USE_MEAN_TYPE = False
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    get_last_statistics,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import CurvesAPIClient, CurvesAPIError
from .const import DOMAIN, UTILITY_TYPES

_LOGGER = logging.getLogger(__name__)

# Swedish timezone (Europe/Stockholm)
SWEDISH_TIMEZONE = "Europe/Stockholm"

# StatisticMetaData mean kwargs — use mean_type if available, fall back to has_mean
_STAT_MEAN_KWARGS: dict[str, Any] = (
    {"mean_type": StatisticMeanType.NONE} if _USE_MEAN_TYPE else {"has_mean": False}
)


class CurvesDataUpdateCoordinator(DataUpdateCoordinator):
    """Class to manage fetching data from the Curves API."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: CurvesAPIClient,
        node_id: str | None,
        measuring_point_id: str | None,
        update_interval: int,
        data_interval: str,
        utilities: list[str],
        vat_rate: float = 0.0,
        currency: str = "SEK",
    ) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            name="EcoGuard Curves",
            update_interval=timedelta(seconds=update_interval),
        )
        self.client = client
        self._node_id = node_id
        self._measuring_point_id = measuring_point_id
        self._data_interval = data_interval
        self._utilities = utilities
        self._vat_rate = vat_rate
        self._currency = currency
        self._tariff_rates: dict[str, float] = {}
        self._service_fee: float = 0.0
        self._tariff_last_updated: datetime | None = None
        self._last_tariff_fetch: datetime | None = None

    @property
    def utilities(self) -> list[str]:
        """Return configured utilities."""
        return self._utilities

    async def _fetch_tariff_rates(self) -> None:
        """Fetch and update tariff rates from billing results."""
        try:
            billing_results = await self.client.get_billing_results(node_id=self._node_id)

            if not billing_results:
                _LOGGER.warning("No billing results returned from API")
                return

            # Find the most recent billing period
            sorted_billings = sorted(billing_results, key=lambda x: x.get("End", 0), reverse=True)

            if not sorted_billings:
                _LOGGER.warning("No billing periods found")
                return

            latest_billing = sorted_billings[0]
            _LOGGER.debug(
                f"Using billing period: {latest_billing.get('Billing', {}).get('Name', 'Unknown')}"
            )

            # Extract rates for each utility
            rates = {}
            service_fee = 0.0

            for part in latest_billing.get("Parts", []):
                utility_code = part.get("Code")

                # Extract service fee (no utility code)
                if not utility_code or utility_code is None:
                    for item in part.get("Items", []):
                        component_name = item.get("PriceComponent", {}).get("Name", "")
                        if "EcoGuard" in component_name or "Energiservice" in component_name:
                            service_fee = float(item.get("Rate", 0.0))
                            _LOGGER.debug(f"Service fee: {service_fee}")
                    continue

                # Skip if not in our utility codes
                if utility_code not in ["CW", "HW", "HEAT", "ELEC"]:
                    continue

                # Extract rate for this utility (average if multiple items)
                utility_rates = []
                for item in part.get("Items", []):
                    component_name = item.get("PriceComponent", {}).get("Name", "")

                    # Only include consumption-based charges
                    if "Rörlig avgift" in component_name or "Variable" in component_name:
                        rate = float(item.get("Rate", 0.0))
                        if rate > 0:
                            utility_rates.append(rate)

                if utility_rates:
                    avg_rate = sum(utility_rates) / len(utility_rates)
                    rates[utility_code] = avg_rate
                    _LOGGER.debug(f"Rate for {utility_code}: {avg_rate}")

            # Update cached rates
            self._tariff_rates = rates
            self._service_fee = service_fee
            self._tariff_last_updated = dt_util.utcnow()
            self._last_tariff_fetch = dt_util.utcnow()

            _LOGGER.info(f"Updated tariff rates: {rates}, service fee: {service_fee}")

        except Exception:
            _LOGGER.exception("Error fetching tariff rates")
            # Don't raise - continue with old rates if available

    @staticmethod
    def _extract_values(
        response_data: list[dict[str, Any]], utility: str, func: str
    ) -> list[dict[str, Any]]:
        """Extract values from API response for a specific utility and function.

        Args:
            response_data: Raw API response data
            utility: Utility API code (e.g. "ELEC", "HW")
            func: Function type (e.g. "con", "price", "co2")

        Returns:
            List of value dicts with "Time" and "Value" keys
        """
        values_list: list[dict[str, Any]] = []
        for item in response_data:
            if not isinstance(item, dict):
                continue
            results = item.get("Result", [])
            for result in results:
                if not isinstance(result, dict):
                    continue
                if result.get("Utl") == utility and result.get("Func") == func:
                    values = result.get("Values", [])
                    if isinstance(values, list):
                        values_list.extend(values)
        return values_list

    @staticmethod
    def _sum_values(values: list[dict[str, Any]]) -> float:
        """Sum numeric values from API response.

        Args:
            values: List of data points from API with "Value" field

        Returns:
            Sum of all numeric values, 0.0 if no valid values
        """
        return sum(
            float(p.get("Value", 0.0))
            for p in values
            if isinstance(p, dict) and isinstance(p.get("Value"), (int, float))
        )

    async def _import_initial_history(
        self,
        utility_key: str,
        utility_config: dict[str, Any],
        statistic_id: str,
        cost_statistic_id: str,
        vat_multiplier: float,
        currency: str,
    ) -> None:
        """Import 30 days of historical data on first run.

        Args:
            utility_key: Key identifying the utility type
            utility_config: Utility configuration dict
            statistic_id: Statistic ID for consumption
            cost_statistic_id: Statistic ID for cost
            vat_multiplier: VAT multiplier to apply to costs
            currency: Currency code for cost statistics
        """
        api_code = utility_config["api_code"]
        now_utc = dt_util.utcnow()

        # Calculate 30 days ago (aligned to midnight in Swedish timezone)
        swedish_tz = zoneinfo.ZoneInfo(SWEDISH_TIMEZONE)
        now_swedish = now_utc.astimezone(swedish_tz)
        history_start_swedish = now_swedish - timedelta(days=30)
        history_start_swedish = history_start_swedish.replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        history_start_utc = history_start_swedish.astimezone(dt_timezone.utc)

        _LOGGER.info(
            f"Fetching 30-day history for {utility_key} from {history_start_utc} to {now_utc}"
        )

        try:
            # Build utility list for API call
            utilities = [f"{api_code}[con]", f"{api_code}[price]"]

            # Fetch historical data
            history_data = await self.client.get_data(
                node_id=self._node_id,
                measuring_point_id=self._measuring_point_id,
                from_date=history_start_utc,
                to_date=now_utc,
                interval="hour",
                utilities=utilities,
            )

            # Extract consumption and cost values
            history_consumption_values = self._extract_values(history_data, api_code, "con")
            history_cost_values = self._extract_values(history_data, api_code, "price")

            # Build consumption statistics
            consumption_sum = 0.0
            consumption_statistics = []
            for point in history_consumption_values:
                if not isinstance(point, dict):
                    continue

                value = point.get("Value")
                time_ts = point.get("Time")

                # Skip None values (incomplete hours)
                if value is None or time_ts is None:
                    continue

                start = datetime.fromtimestamp(time_ts, tz=dt_timezone.utc)
                consumption_sum += float(value)
                consumption_statistics.append(
                    StatisticData(start=start, state=float(value), sum=consumption_sum)
                )

            # Build cost statistics
            cost_sum = 0.0
            cost_statistics = []
            for point in history_cost_values:
                if not isinstance(point, dict):
                    continue

                value = point.get("Value")
                time_ts = point.get("Time")

                if value is None or time_ts is None:
                    continue

                start = datetime.fromtimestamp(time_ts, tz=dt_timezone.utc)
                cost_value = float(value) * vat_multiplier
                cost_sum += cost_value
                cost_statistics.append(StatisticData(start=start, state=cost_value, sum=cost_sum))

            # Build metadata
            # unit_class enables HA unit conversion (kWh↔MWh, m³↔L↔gal)
            device_class = utility_config["device_class_consumption"]
            if device_class == "energy":
                unit_class = "energy"
            elif device_class == "water":
                unit_class = "volume"
            else:
                unit_class = None

            consumption_metadata = StatisticMetaData(
                **_STAT_MEAN_KWARGS,
                has_sum=True,
                name=f"{utility_config['name']} Consumption",
                source=DOMAIN,
                statistic_id=statistic_id,
                unit_class=unit_class,
                unit_of_measurement=utility_config["unit_consumption"],
            )

            cost_metadata = StatisticMetaData(
                **_STAT_MEAN_KWARGS,
                has_sum=True,
                name=f"{utility_config['name']} Cost",
                source=DOMAIN,
                statistic_id=cost_statistic_id,
                unit_class=None,
                unit_of_measurement=currency,
            )

            # Import statistics
            if consumption_statistics:
                async_add_external_statistics(
                    self.hass, consumption_metadata, consumption_statistics
                )
                _LOGGER.info(
                    f"Imported {len(consumption_statistics)} historical consumption statistics "
                    f"for {utility_key}"
                )

            if cost_statistics:
                async_add_external_statistics(self.hass, cost_metadata, cost_statistics)
                _LOGGER.info(
                    f"Imported {len(cost_statistics)} historical cost statistics for {utility_key}"
                )

        except Exception:
            _LOGGER.exception(f"Error importing initial history for {utility_key}")
            # Don't raise - continue with normal operation even if history import fails

    async def _import_statistics(
        self,
        utility_key: str,
        hourly_consumption_values: list[dict],
        hourly_cost_values: list[dict],
        vat_multiplier: float,
        currency: str,
    ) -> None:
        """Import hourly statistics for energy dashboard.

        Args:
            utility_key: Key identifying the utility type
            hourly_consumption_values: List of hourly consumption data points
            hourly_cost_values: List of hourly cost data points
            vat_multiplier: VAT multiplier to apply to costs
            currency: Currency code for cost statistics
        """
        utility_config = UTILITY_TYPES[utility_key]

        # Build statistic IDs
        statistic_id = f"{DOMAIN}:{utility_key}_consumption_{self._node_id}"
        cost_statistic_id = f"{DOMAIN}:{utility_key}_cost_{self._node_id}"

        # unit_class enables HA unit conversion (kWh↔MWh, m³↔L↔gal)
        device_class = utility_config["device_class_consumption"]
        if device_class == "energy":
            unit_class = "energy"
        elif device_class == "water":
            unit_class = "volume"
        else:
            unit_class = None

        # Build metadata for consumption statistics
        # Note: mean_type is MANDATORY in HA 2026.4+, has_mean is deprecated
        consumption_metadata = StatisticMetaData(
            **_STAT_MEAN_KWARGS,
            has_sum=True,
            name=f"{utility_config['name']} Consumption",
            source=DOMAIN,
            statistic_id=statistic_id,
            unit_class=unit_class,
            unit_of_measurement=utility_config["unit_consumption"],
        )

        # Build metadata for cost statistics
        cost_metadata = StatisticMetaData(
            **_STAT_MEAN_KWARGS,
            has_sum=True,
            name=f"{utility_config['name']} Cost",
            source=DOMAIN,
            statistic_id=cost_statistic_id,
            unit_class=None,
            unit_of_measurement=currency,
        )

        # Get last known sums for incremental updates
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
        )

        if statistic_id in last_stats and last_stats[statistic_id]:
            consumption_sum = last_stats[statistic_id][0].get("sum", 0.0)
            last_consumption_time = last_stats[statistic_id][0]["start"]
        else:
            consumption_sum = 0.0
            last_consumption_time = None

        last_cost_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, cost_statistic_id, True, {"sum"}
        )

        if cost_statistic_id in last_cost_stats and last_cost_stats[cost_statistic_id]:
            cost_sum = last_cost_stats[cost_statistic_id][0].get("sum", 0.0)
            last_cost_time = last_cost_stats[cost_statistic_id][0]["start"]
        else:
            cost_sum = 0.0
            last_cost_time = None

        # On first run, fetch 30 days of historical data for initial import
        if last_consumption_time is None:
            _LOGGER.info(f"First run for {utility_key} statistics - importing 30 days of history")
            await self._import_initial_history(
                utility_key,
                utility_config,
                statistic_id,
                cost_statistic_id,
                vat_multiplier,
                currency,
            )

            # Re-fetch last statistics after initial history import
            last_stats = await get_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 1, statistic_id, True, {"sum"}
            )

            if statistic_id in last_stats and last_stats[statistic_id]:
                consumption_sum = last_stats[statistic_id][0].get("sum", 0.0)
                last_consumption_time = last_stats[statistic_id][0]["start"]

            last_cost_stats = await get_instance(self.hass).async_add_executor_job(
                get_last_statistics, self.hass, 1, cost_statistic_id, True, {"sum"}
            )

            if cost_statistic_id in last_cost_stats and last_cost_stats[cost_statistic_id]:
                cost_sum = last_cost_stats[cost_statistic_id][0].get("sum", 0.0)
                last_cost_time = last_cost_stats[cost_statistic_id][0]["start"]

        # Build consumption statistics from hourly values
        consumption_statistics = []
        for point in hourly_consumption_values:
            if not isinstance(point, dict):
                continue

            value = point.get("Value")
            time_ts = point.get("Time")

            # Skip None values (incomplete hours) and already imported data
            if value is None or time_ts is None:
                continue

            if last_consumption_time is not None and time_ts < last_consumption_time.timestamp():
                continue

            # API timestamps are UTC Unix epoch
            start = datetime.fromtimestamp(time_ts, tz=dt_timezone.utc)

            consumption_sum += float(value)
            consumption_statistics.append(
                StatisticData(start=start, state=float(value), sum=consumption_sum)
            )

        # Build cost statistics from hourly values
        cost_statistics = []
        for point in hourly_cost_values:
            if not isinstance(point, dict):
                continue

            value = point.get("Value")
            time_ts = point.get("Time")

            # Skip None values and already imported data
            if value is None or time_ts is None:
                continue

            if last_cost_time is not None and time_ts < last_cost_time.timestamp():
                continue

            start = datetime.fromtimestamp(time_ts, tz=dt_timezone.utc)
            cost_value = float(value) * vat_multiplier

            cost_sum += cost_value
            cost_statistics.append(StatisticData(start=start, state=cost_value, sum=cost_sum))

        # Insert statistics into HA database
        if consumption_statistics:
            async_add_external_statistics(self.hass, consumption_metadata, consumption_statistics)
            _LOGGER.debug(
                f"Imported {len(consumption_statistics)} consumption statistics for {utility_key}"
            )

        if cost_statistics:
            async_add_external_statistics(self.hass, cost_metadata, cost_statistics)
            _LOGGER.debug(f"Imported {len(cost_statistics)} cost statistics for {utility_key}")

    async def _fetch_utility_data(
        self,
        utility_key: str,
        now_utc: datetime,
        day_start: datetime,
        month_start: datetime,
        year_start: datetime,
        prev_month_start: datetime,
        prev_month_end: datetime,
        past_12_months_start: datetime,
    ) -> dict[str, Any]:
        """Fetch and process data for a single utility type."""
        utility_config = UTILITY_TYPES[utility_key]
        api_code = utility_config["api_code"]

        # Build utility list for API call
        utilities = [f"{api_code}[con]", f"{api_code}[price]"]
        # CO2 data seems to be available for electricity and hot water
        if utility_key in ["electricity", "hot_water"]:
            utilities.append(f"{api_code}[co2]")

        # Fetch data for different time periods
        daily_data = await self.client.get_data(
            node_id=self._node_id,
            measuring_point_id=self._measuring_point_id,
            from_date=day_start,
            to_date=now_utc,
            interval=self._data_interval,
            utilities=utilities,
        )

        monthly_data = await self.client.get_data(
            node_id=self._node_id,
            measuring_point_id=self._measuring_point_id,
            from_date=month_start,
            to_date=now_utc,
            interval=self._data_interval,
            utilities=utilities,
        )

        yearly_data = await self.client.get_data(
            node_id=self._node_id,
            measuring_point_id=self._measuring_point_id,
            from_date=year_start,
            to_date=now_utc,
            interval=self._data_interval,
            utilities=utilities,
        )

        # Fetch previous month data
        prev_month_data = await self.client.get_data(
            node_id=self._node_id,
            measuring_point_id=self._measuring_point_id,
            from_date=prev_month_start,
            to_date=prev_month_end,
            interval=self._data_interval,
            utilities=utilities,
        )

        # Fetch past 12 months data (rolling window)
        past_12_months_data = await self.client.get_data(
            node_id=self._node_id,
            measuring_point_id=self._measuring_point_id,
            from_date=past_12_months_start,
            to_date=now_utc,
            interval=self._data_interval,
            utilities=utilities,
        )

        # VAT multiplier used for both statistics import and sensor cost values
        vat_multiplier = 1.0 + (self._vat_rate / 100.0) if self._vat_rate > 0 else 1.0

        # Extract values for this utility
        daily_consumption_values = self._extract_values(daily_data, api_code, "con")
        monthly_consumption_values = self._extract_values(monthly_data, api_code, "con")
        yearly_consumption_values = self._extract_values(yearly_data, api_code, "con")
        prev_month_consumption_values = self._extract_values(prev_month_data, api_code, "con")
        past_12_months_consumption_values = self._extract_values(
            past_12_months_data, api_code, "con"
        )
        daily_cost_values = self._extract_values(daily_data, api_code, "price")
        monthly_cost_values = self._extract_values(monthly_data, api_code, "price")
        yearly_cost_values = self._extract_values(yearly_data, api_code, "price")
        prev_month_cost_values = self._extract_values(prev_month_data, api_code, "price")
        past_12_months_cost_values = self._extract_values(past_12_months_data, api_code, "price")

        # Import statistics for energy dashboard (using hourly data with correct timestamps)
        try:
            await self._import_statistics(
                utility_key,
                daily_consumption_values,  # These are hourly values for today
                daily_cost_values,
                vat_multiplier,
                self._currency,
            )
        except Exception:
            _LOGGER.exception(f"Error importing statistics for {utility_key}")

        # Extract CO2 values only for electricity and hot water
        if utility_key in ["electricity", "hot_water"]:
            daily_co2_values = self._extract_values(daily_data, api_code, "co2")
            monthly_co2_values = self._extract_values(monthly_data, api_code, "co2")
            yearly_co2_values = self._extract_values(yearly_data, api_code, "co2")
            prev_month_co2_values = self._extract_values(prev_month_data, api_code, "co2")
            past_12_months_co2_values = self._extract_values(past_12_months_data, api_code, "co2")
        else:
            daily_co2_values = []
            monthly_co2_values = []
            yearly_co2_values = []
            prev_month_co2_values = []
            past_12_months_co2_values = []

        # Calculate daily consumption
        daily_consumption = self._sum_values(daily_consumption_values)

        # Calculate monthly consumption
        monthly_consumption = self._sum_values(monthly_consumption_values)

        # Calculate yearly consumption
        yearly_consumption = self._sum_values(yearly_consumption_values)

        # Calculate previous month consumption
        prev_month_consumption = self._sum_values(prev_month_consumption_values)

        # Calculate past 12 months consumption
        past_12_months_consumption = self._sum_values(past_12_months_consumption_values)

        # Calculate costs
        daily_cost_without_vat = self._sum_values(daily_cost_values)

        monthly_cost_without_vat = self._sum_values(monthly_cost_values)

        yearly_cost_without_vat = self._sum_values(yearly_cost_values)

        prev_month_cost_without_vat = self._sum_values(prev_month_cost_values)

        past_12_months_cost_without_vat = self._sum_values(past_12_months_cost_values)

        # Calculate CO2 totals (API returns grams, convert to kg)
        daily_co2 = self._sum_values(daily_co2_values) / 1000.0

        monthly_co2 = self._sum_values(monthly_co2_values) / 1000.0

        yearly_co2 = self._sum_values(yearly_co2_values) / 1000.0

        prev_month_co2 = self._sum_values(prev_month_co2_values) / 1000.0

        past_12_months_co2 = self._sum_values(past_12_months_co2_values) / 1000.0

        # Apply VAT to costs
        daily_cost = daily_cost_without_vat * vat_multiplier
        monthly_cost = monthly_cost_without_vat * vat_multiplier
        yearly_cost = yearly_cost_without_vat * vat_multiplier
        prev_month_cost = prev_month_cost_without_vat * vat_multiplier
        past_12_months_cost = past_12_months_cost_without_vat * vat_multiplier

        # Find latest values
        latest_value = 0.0
        latest_timestamp = None
        latest_cost_value = 0.0
        latest_cost_timestamp = None
        latest_co2_value = 0.0
        latest_co2_timestamp = None

        for point in daily_consumption_values:
            if isinstance(point, dict):
                value = point.get("Value", 0.0)
                time_value = point.get("Time")
                if isinstance(value, (int, float)) and time_value:
                    if latest_timestamp is None or time_value > latest_timestamp:
                        latest_value = float(value)
                        latest_timestamp = time_value

        for point in daily_cost_values:
            if isinstance(point, dict):
                value = point.get("Value", 0.0)
                time_value = point.get("Time")
                if isinstance(value, (int, float)) and time_value:
                    if latest_cost_timestamp is None or time_value > latest_cost_timestamp:
                        latest_cost_value = float(value) * vat_multiplier
                        latest_cost_timestamp = time_value

        for point in daily_co2_values:
            if isinstance(point, dict):
                value = point.get("Value", 0.0)
                time_value = point.get("Time")
                if isinstance(value, (int, float)) and time_value:
                    if latest_co2_timestamp is None or time_value > latest_co2_timestamp:
                        latest_co2_value = float(value) / 1000.0  # Convert g to kg
                        latest_co2_timestamp = time_value

        # Format latest reading timestamp
        latest_reading_str = None
        if latest_timestamp:
            try:
                latest_dt = dt_util.utc_from_timestamp(latest_timestamp)
                latest_reading_str = latest_dt.isoformat()
            except (ValueError, TypeError, OSError):
                latest_reading_str = str(latest_timestamp)

        # Calculate estimated costs using tariff rates
        tariff_rate = self._tariff_rates.get(api_code, 0.0)
        estimated_daily_cost = daily_consumption * tariff_rate if tariff_rate > 0 else 0.0
        estimated_monthly_cost = monthly_consumption * tariff_rate if tariff_rate > 0 else 0.0
        estimated_prev_month_cost = prev_month_consumption * tariff_rate if tariff_rate > 0 else 0.0
        estimated_past_12_months_cost = (
            past_12_months_consumption * tariff_rate if tariff_rate > 0 else 0.0
        )

        return {
            "consumption": daily_consumption,  # Use daily as total for now
            "daily_consumption": daily_consumption,
            "monthly_consumption": monthly_consumption,
            "yearly_consumption": yearly_consumption,
            "prev_month_consumption": prev_month_consumption,
            "past_12_months_consumption": past_12_months_consumption,
            "current_power": latest_value,
            "latest_reading": latest_reading_str,
            "daily_cost": daily_cost,
            "monthly_cost": monthly_cost,
            "yearly_cost": yearly_cost,
            "prev_month_cost": prev_month_cost,
            "past_12_months_cost": past_12_months_cost,
            "current_cost": latest_cost_value,
            "estimated_daily_cost": estimated_daily_cost,
            "estimated_monthly_cost": estimated_monthly_cost,
            "estimated_prev_month_cost": estimated_prev_month_cost,
            "estimated_past_12_months_cost": estimated_past_12_months_cost,
            "tariff_rate": tariff_rate,
            "tariff_last_updated": (
                self._tariff_last_updated.isoformat() if self._tariff_last_updated else None
            ),
            "daily_co2": daily_co2,
            "monthly_co2": monthly_co2,
            "yearly_co2": yearly_co2,
            "prev_month_co2": prev_month_co2,
            "past_12_months_co2": past_12_months_co2,
            "current_co2": latest_co2_value,
        }

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch data from the Curves API for all configured utilities."""
        try:
            # Fetch tariff rates periodically (once per day or on first run)
            should_fetch_tariffs = self._last_tariff_fetch is None or (
                dt_util.utcnow() - self._last_tariff_fetch
            ) > timedelta(days=1)

            if should_fetch_tariffs:
                await self._fetch_tariff_rates()

            # Get current time in Swedish timezone for proper day boundaries
            swedish_tz = zoneinfo.ZoneInfo(SWEDISH_TIMEZONE)
            now_utc = dt_util.utcnow()

            # Convert UTC to Swedish timezone
            if now_utc.tzinfo is None:
                now_utc = now_utc.replace(tzinfo=dt_timezone.utc)

            now_swedish = now_utc.astimezone(swedish_tz)

            # Day start in Swedish time (00:00:00)
            day_start_swedish = now_swedish.replace(hour=0, minute=0, second=0, microsecond=0)
            day_start = day_start_swedish.astimezone(dt_timezone.utc)

            # Month start in Swedish time
            month_start_swedish = now_swedish.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            month_start = month_start_swedish.astimezone(dt_timezone.utc)

            # Year start in Swedish time
            year_start_swedish = now_swedish.replace(
                month=1, day=1, hour=0, minute=0, second=0, microsecond=0
            )
            year_start = year_start_swedish.astimezone(dt_timezone.utc)

            # Calculate previous month boundaries
            # Get first day of current month
            current_month_start_swedish = now_swedish.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            # Previous month is one day before current month start
            last_day_prev_month = current_month_start_swedish - timedelta(days=1)
            # Get first day of previous month
            prev_month_start_swedish = last_day_prev_month.replace(
                day=1, hour=0, minute=0, second=0, microsecond=0
            )
            # Convert to UTC
            prev_month_start = prev_month_start_swedish.astimezone(dt_timezone.utc)
            prev_month_end = current_month_start_swedish.astimezone(dt_timezone.utc)

            # Calculate past 12 months start (rolling 12-month window)
            # Simply go back ~365 days from now
            past_12_months_start = now_utc - timedelta(days=365)

            # Fetch data for each configured utility
            result = {
                "service_fee": self._service_fee,
                "service_fee_last_updated": (
                    self._tariff_last_updated.isoformat() if self._tariff_last_updated else None
                ),
            }
            for utility_key in self._utilities:
                if utility_key not in UTILITY_TYPES:
                    _LOGGER.warning(f"Unknown utility type: {utility_key}")
                    continue

                try:
                    utility_data = await self._fetch_utility_data(
                        utility_key,
                        now_utc,
                        day_start,
                        month_start,
                        year_start,
                        prev_month_start,
                        prev_month_end,
                        past_12_months_start,
                    )
                    result[utility_key] = utility_data
                except Exception:
                    _LOGGER.exception(f"Error fetching data for {utility_key}")
                    # Continue with other utilities even if one fails
                    result[utility_key] = {
                        "consumption": 0.0,
                        "daily_consumption": 0.0,
                        "monthly_consumption": 0.0,
                        "yearly_consumption": 0.0,
                        "prev_month_consumption": 0.0,
                        "past_12_months_consumption": 0.0,
                        "current_power": 0.0,
                        "latest_reading": None,
                        "daily_cost": 0.0,
                        "monthly_cost": 0.0,
                        "yearly_cost": 0.0,
                        "prev_month_cost": 0.0,
                        "past_12_months_cost": 0.0,
                        "current_cost": 0.0,
                        "estimated_daily_cost": 0.0,
                        "estimated_monthly_cost": 0.0,
                        "estimated_prev_month_cost": 0.0,
                        "estimated_past_12_months_cost": 0.0,
                        "tariff_rate": 0.0,
                        "tariff_last_updated": None,
                        "daily_co2": 0.0,
                        "monthly_co2": 0.0,
                        "yearly_co2": 0.0,
                        "prev_month_co2": 0.0,
                        "past_12_months_co2": 0.0,
                        "current_co2": 0.0,
                    }

            return result

        except CurvesAPIError as err:
            raise UpdateFailed(f"Error communicating with Curves API: {err}") from err
