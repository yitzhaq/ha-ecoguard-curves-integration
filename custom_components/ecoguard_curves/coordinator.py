"""Data update coordinator for Curves API."""
from __future__ import annotations

import asyncio
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
    statistics_during_period,
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
        self._last_reconciled_real_ts: dict[str, float] = {}
        self._history_import_running: set[str] = set()
        self._history_import_done: set[str] = set()
        self._import_lock = asyncio.Lock()

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
        """Import all available historical data on first run.

        Fetches up to 5 years of hourly data in yearly chunks (the API
        returns whatever is available). Runs as a background task to avoid
        blocking HA startup.

        For periods without billing data, estimates costs using the rate
        from the same calendar month of the oldest year with real cost data.
        This accounts for seasonal pricing differences.
        """
        # Acquire lock to serialize imports — concurrent API calls cause 429 rate limiting
        async with self._import_lock:
            await self._import_initial_history_locked(
                utility_key, utility_config, statistic_id,
                cost_statistic_id, vat_multiplier, currency,
            )

    async def _import_initial_history_locked(
        self,
        utility_key: str,
        utility_config: dict[str, Any],
        statistic_id: str,
        cost_statistic_id: str,
        vat_multiplier: float,
        currency: str,
    ) -> None:
        """Inner import method, called under lock."""
        self._history_import_running.add(utility_key)
        api_code = utility_config["api_code"]
        now_utc = dt_util.utcnow()

        # Fetch up to 5 years of history (aligned to midnight in Swedish timezone)
        swedish_tz = zoneinfo.ZoneInfo(SWEDISH_TIMEZONE)
        now_swedish = now_utc.astimezone(swedish_tz)
        history_start_swedish = (now_swedish - timedelta(days=5 * 365)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        history_start_utc = history_start_swedish.astimezone(dt_timezone.utc)

        _LOGGER.info(
            f"Background import: fetching all available history for {utility_key} "
            f"(up to 5 years from {history_start_utc.date()})"
        )

        try:
            # Fetch in yearly chunks — the API returns HTTP 500 for large
            # combined requests. Consumption and cost are fetched separately.
            all_consumption_values: list[dict[str, Any]] = []
            all_cost_values: list[dict[str, Any]] = []

            chunk_start = history_start_utc
            while chunk_start < now_utc:
                chunk_end = chunk_start + timedelta(days=365)
                if chunk_end > now_utc:
                    chunk_end = now_utc

                for func, target in [("con", all_consumption_values), ("price", all_cost_values)]:
                    try:
                        data = await self.client.get_data(
                            node_id=self._node_id,
                            measuring_point_id=self._measuring_point_id,
                            from_date=chunk_start,
                            to_date=chunk_end,
                            interval="hour",
                            utilities=[f"{api_code}[{func}]"],
                        )
                        target.extend(self._extract_values(data, api_code, func))
                    except Exception:
                        _LOGGER.debug(
                            f"No {func} data for {utility_key} "
                            f"({chunk_start.date()} → {chunk_end.date()})"
                        )

                    # Pause between API calls to avoid rate limiting (429)
                    await asyncio.sleep(5)

                chunk_start = chunk_end

            # Sort chronologically — API may return data out of order
            history_consumption = sorted(
                (p for p in all_consumption_values
                 if isinstance(p, dict) and p.get("Time") is not None),
                key=lambda p: p["Time"],
            )
            history_cost = sorted(
                (p for p in all_cost_values
                 if isinstance(p, dict) and p.get("Time") is not None),
                key=lambda p: p["Time"],
            )

            _LOGGER.info(
                f"Fetched {len(history_consumption)} consumption and "
                f"{len(history_cost)} cost points for {utility_key}"
            )

            # Build consumption lookup by timestamp
            consumption_by_time: dict[float, float] = {}
            for p in history_consumption:
                ts, val = p.get("Time"), p.get("Value")
                if ts is not None and val is not None:
                    consumption_by_time[ts] = float(val)

            # --- Calendar-month rate estimation ---
            # For periods without billing data, use the rate from the same
            # calendar month of the oldest year with real cost data.
            # This accounts for seasonal pricing (e.g., heat costs vary
            # significantly between winter and summer).

            # Build (year, month) → totals from real cost data
            monthly_cost: dict[tuple[int, int], float] = {}
            monthly_con: dict[tuple[int, int], float] = {}
            for p in history_cost:
                api_cost = p.get("Value")
                ts = p.get("Time")
                if ts is None or api_cost is None or float(api_cost) <= 0:
                    continue
                dt = datetime.fromtimestamp(ts, tz=dt_timezone.utc)
                key = (dt.year, dt.month)
                monthly_cost[key] = monthly_cost.get(key, 0.0) + float(api_cost)
                monthly_con[key] = monthly_con.get(key, 0.0) + consumption_by_time.get(ts, 0.0)

            # Derive per-unit rate by month, oldest year with data takes priority
            monthly_rates: dict[int, float] = {}  # month_number → ex-VAT rate
            for year, month in sorted(monthly_cost):
                if month not in monthly_rates and monthly_con.get((year, month), 0.0) > 0:
                    monthly_rates[month] = (
                        monthly_cost[(year, month)] / monthly_con[(year, month)]
                    )

            # Fall back to current tariff rate for months with no historical data
            tariff_rate = self._tariff_rates.get(api_code, 0.0)

            if monthly_rates:
                _LOGGER.info(
                    f"Built calendar-month rate lookup for {utility_key} from "
                    f"{min(y for y, _ in monthly_cost)} data: "
                    + ", ".join(f"M{m}={r:.2f}" for m, r in sorted(monthly_rates.items()))
                )

            # --- Build consumption statistics ---
            consumption_sum = 0.0
            consumption_statistics: list[StatisticData] = []
            for p in history_consumption:
                val = p.get("Value")
                ts = p.get("Time")
                if val is None or ts is None:
                    continue
                consumption_sum += float(val)
                consumption_statistics.append(StatisticData(
                    start=datetime.fromtimestamp(ts, tz=dt_timezone.utc),
                    state=float(val),
                    sum=consumption_sum,
                ))

            # --- Build cost statistics ---
            # Drive cost entries from CONSUMPTION timestamps, not cost timestamps.
            # The API may return no cost data for periods before billing started,
            # but we still want estimated costs for those consumption hours.
            cost_by_time: dict[float, float] = {}
            for p in history_cost:
                ts = p.get("Time")
                val = p.get("Value")
                if ts is not None and val is not None:
                    cost_by_time[ts] = float(val)

            cost_sum = 0.0
            cost_statistics: list[StatisticData] = []
            for p in history_consumption:
                ts = p.get("Time")
                val = p.get("Value")
                if ts is None or val is None:
                    continue

                consumption = float(val)
                api_cost = cost_by_time.get(ts)

                if api_cost is not None and api_cost > 0:
                    # Real billed cost (ex-VAT from API, apply VAT)
                    cost_value = api_cost * vat_multiplier
                else:
                    # Estimate: use calendar-month rate, fall back to current tariff
                    dt = datetime.fromtimestamp(ts, tz=dt_timezone.utc)
                    rate = monthly_rates.get(dt.month, tariff_rate)
                    if rate <= 0:
                        continue
                    cost_value = consumption * rate * vat_multiplier

                cost_sum += cost_value
                cost_statistics.append(StatisticData(
                    start=datetime.fromtimestamp(ts, tz=dt_timezone.utc),
                    state=cost_value,
                    sum=cost_sum,
                ))

            # --- Import into HA ---
            device_class = utility_config["device_class_consumption"]
            if device_class == "energy":
                unit_class = "energy"
            elif device_class == "water":
                unit_class = "volume"
            else:
                unit_class = None

            if consumption_statistics:
                async_add_external_statistics(self.hass, StatisticMetaData(
                    **_STAT_MEAN_KWARGS,
                    has_sum=True,
                    name=f"{utility_config['name']} Consumption",
                    source=DOMAIN,
                    statistic_id=statistic_id,
                    unit_class=unit_class,
                    unit_of_measurement=utility_config["unit_consumption"],
                ), consumption_statistics)
                _LOGGER.info(
                    f"Imported {len(consumption_statistics)} historical consumption "
                    f"statistics for {utility_key}"
                )

            if cost_statistics:
                async_add_external_statistics(self.hass, StatisticMetaData(
                    **_STAT_MEAN_KWARGS,
                    has_sum=True,
                    name=f"{utility_config['name']} Cost",
                    source=DOMAIN,
                    statistic_id=cost_statistic_id,
                    unit_class=None,
                    unit_of_measurement=currency,
                ), cost_statistics)
                _LOGGER.info(
                    f"Imported {len(cost_statistics)} historical cost statistics "
                    f"for {utility_key}"
                )

        except Exception:
            _LOGGER.exception(f"Error importing initial history for {utility_key}")
        finally:
            self._history_import_running.discard(utility_key)
            self._history_import_done.add(utility_key)

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

        # Check if statistics exist. If not, schedule background history import.
        last_stats = await get_instance(self.hass).async_add_executor_job(
            get_last_statistics, self.hass, 1, statistic_id, True, set()
        )

        if not last_stats:
            if utility_key not in self._history_import_running:
                if utility_key not in self._history_import_done:
                    _LOGGER.info(
                        f"No statistics for {utility_key} — "
                        f"scheduling background history import"
                    )
                    self.hass.async_create_task(
                        self._import_initial_history(
                            utility_key,
                            utility_config,
                            statistic_id,
                            cost_statistic_id,
                            vat_multiplier,
                            currency,
                        )
                    )
            return

        # --- Window replay (Tibber pattern) ---
        # Always rebuild the current data window. This handles corrections
        # in the API data and late-arriving consumption for cost estimation.
        # Use statistics_during_period to get the running sum from just before
        # the data window, then process all points from there.

        api_code = utility_config["api_code"]
        tariff_rate = self._tariff_rates.get(api_code, 0.0)

        # Build consumption lookup by timestamp for cost estimation
        consumption_by_time: dict[float, float] = {}
        for point in hourly_consumption_values:
            if isinstance(point, dict):
                ts = point.get("Time")
                val = point.get("Value")
                if ts is not None and val is not None:
                    consumption_by_time[ts] = float(val)

        # Helper: get the running sum from just before a timestamp.
        # Uses a 48-hour lookback and takes the LAST entry, to handle
        # data gaps (e.g., no entry at 21:00 UTC when midnight CEST
        # is 22:00 UTC and the last entry is at 20:00).
        async def _get_prior_sum(stat_id: str, first_ts: float) -> float:
            first_dt = datetime.fromtimestamp(first_ts, tz=dt_timezone.utc)
            lookback = first_dt - timedelta(hours=48)
            prior = await get_instance(self.hass).async_add_executor_job(
                statistics_during_period,
                self.hass,
                lookback,
                first_dt,
                {stat_id},
                "hour",
                None,
                {"sum"},
            )
            if stat_id in prior and prior[stat_id]:
                return prior[stat_id][-1].get("sum", 0.0)
            return 0.0

        # --- Consumption ---
        con_timestamps = sorted(
            p.get("Time") for p in hourly_consumption_values
            if isinstance(p, dict) and p.get("Time") is not None
        )

        consumption_statistics = []
        if con_timestamps:
            consumption_sum = await _get_prior_sum(statistic_id, con_timestamps[0])
            for point in hourly_consumption_values:
                if not isinstance(point, dict):
                    continue
                value = point.get("Value")
                time_ts = point.get("Time")
                if value is None or time_ts is None:
                    continue
                start = datetime.fromtimestamp(time_ts, tz=dt_timezone.utc)
                consumption_sum += float(value)
                consumption_statistics.append(
                    StatisticData(start=start, state=float(value), sum=consumption_sum)
                )

        # --- Cost ---
        # Drive cost from consumption timestamps (same approach as initial import).
        # Build a cost lookup from the API data, then iterate consumption timestamps.
        cost_by_time: dict[float, float] = {}
        for point in hourly_cost_values:
            if isinstance(point, dict):
                ts = point.get("Time")
                val = point.get("Value")
                if ts is not None and val is not None:
                    cost_by_time[ts] = float(val)

        cost_statistics = []
        if con_timestamps:
            cost_sum = await _get_prior_sum(cost_statistic_id, con_timestamps[0])
            for point in hourly_consumption_values:
                if not isinstance(point, dict):
                    continue
                time_ts = point.get("Time")
                con_val = point.get("Value")
                if time_ts is None or con_val is None:
                    continue

                consumption = float(con_val)
                api_cost = cost_by_time.get(time_ts)

                # Use real cost if available and positive, otherwise estimate
                if api_cost is not None and api_cost > 0:
                    cost_value = api_cost * vat_multiplier
                elif tariff_rate > 0:
                    cost_value = consumption * tariff_rate * vat_multiplier
                else:
                    continue  # No tariff rate — skip

                start = datetime.fromtimestamp(time_ts, tz=dt_timezone.utc)
                cost_sum += cost_value
                cost_statistics.append(
                    StatisticData(start=start, state=cost_value, sum=cost_sum)
                )

        # Insert statistics into HA database
        if consumption_statistics:
            async_add_external_statistics(self.hass, consumption_metadata, consumption_statistics)
            _LOGGER.debug(
                f"Imported {len(consumption_statistics)} consumption statistics for {utility_key}"
            )

        if cost_statistics:
            async_add_external_statistics(self.hass, cost_metadata, cost_statistics)
            _LOGGER.debug(f"Imported {len(cost_statistics)} cost statistics for {utility_key}")

    async def _reconcile_cost_statistics(self, utility_key: str) -> None:
        """Check if billing data became available and recalculate cost statistics.

        Runs once daily. Fetches the last 90 days of cost data from the API.
        If any previously-zero costs now have real billing values, rebuilds
        the entire cost statistics series from that point forward with
        correct running sums.
        """
        utility_config = UTILITY_TYPES[utility_key]
        api_code = utility_config["api_code"]
        cost_statistic_id = f"{DOMAIN}:{utility_key}_cost_{self._node_id}"

        now_utc = dt_util.utcnow()
        lookback_start = now_utc - timedelta(days=90)

        # Fetch 90 days of hourly data
        try:
            data = await self.client.get_data(
                node_id=self._node_id,
                measuring_point_id=self._measuring_point_id,
                from_date=lookback_start,
                to_date=now_utc,
                interval="hour",
                utilities=[f"{api_code}[con]", f"{api_code}[price]"],
            )
        except Exception:
            _LOGGER.exception(f"Error fetching data for cost reconciliation ({utility_key})")
            return

        consumption_values = self._extract_values(data, api_code, "con")
        cost_values = self._extract_values(data, api_code, "price")

        # Check: does any hour have real cost data (> 0)?
        real_cost_timestamps: list[float] = []
        for p in cost_values:
            if not isinstance(p, dict):
                continue
            api_cost = p.get("Value")
            ts = p.get("Time")
            if ts is not None and api_cost is not None and float(api_cost) > 0:
                real_cost_timestamps.append(ts)

        if not real_cost_timestamps:
            _LOGGER.debug(f"No real billing data yet for {utility_key}, skipping reconciliation")
            return

        latest_real_ts = max(real_cost_timestamps)
        prev_ts = self._last_reconciled_real_ts.get(utility_key)
        if prev_ts is not None and latest_real_ts <= prev_ts:
            _LOGGER.debug(f"No new billing data for {utility_key}, skipping reconciliation")
            return

        earliest_real_ts = min(real_cost_timestamps)

        # Get the existing cost sum just BEFORE the earliest real cost timestamp.
        # Use 48-hour lookback to handle data gaps, take the LAST entry.
        earliest_start = datetime.fromtimestamp(earliest_real_ts, tz=dt_timezone.utc)
        lookback = earliest_start - timedelta(hours=48)

        existing_stats = await get_instance(self.hass).async_add_executor_job(
            statistics_during_period,
            self.hass,
            lookback,
            earliest_start,
            {cost_statistic_id},
            "hour",
            None,
            {"sum"},
        )

        if cost_statistic_id in existing_stats and existing_stats[cost_statistic_id]:
            prior_sum = existing_stats[cost_statistic_id][-1].get("sum", 0.0)
        else:
            prior_sum = 0.0

        # Build consumption lookup
        consumption_by_time: dict[float, float] = {}
        for p in consumption_values:
            if isinstance(p, dict) and p.get("Time") is not None and p.get("Value") is not None:
                consumption_by_time[p["Time"]] = float(p["Value"])

        tariff_rate = self._tariff_rates.get(api_code, 0.0)
        vat_multiplier = 1.0 + (self._vat_rate / 100.0) if self._vat_rate > 0 else 1.0

        # Sort all cost data points from earliest_real_ts onward
        sorted_cost_points = sorted(
            [
                p
                for p in cost_values
                if isinstance(p, dict)
                and p.get("Time") is not None
                and p["Time"] >= earliest_real_ts
            ],
            key=lambda p: p["Time"],
        )

        # Rebuild cost statistics from earliest_real_ts forward.
        # IMPORTANT: Must rebuild ALL rows from this point, not just changed ones,
        # because running sums cascade — changing one row invalidates all later sums.
        cost_sum = prior_sum
        cost_statistics: list[StatisticData] = []
        for point in sorted_cost_points:
            time_ts = point["Time"]
            api_cost = point.get("Value")

            # Use real cost if available and positive, otherwise estimate
            if api_cost is not None and float(api_cost) > 0:
                cost_value = float(api_cost) * vat_multiplier
            elif tariff_rate > 0:
                consumption = consumption_by_time.get(time_ts, 0.0)
                cost_value = consumption * tariff_rate * vat_multiplier
            else:
                cost_value = 0.0

            cost_sum += cost_value
            start = datetime.fromtimestamp(time_ts, tz=dt_timezone.utc)
            cost_statistics.append(
                StatisticData(start=start, state=cost_value, sum=cost_sum)
            )

        if cost_statistics:
            cost_metadata = StatisticMetaData(
                **_STAT_MEAN_KWARGS,
                has_sum=True,
                name=f"{utility_config['name']} Cost",
                source=DOMAIN,
                statistic_id=cost_statistic_id,
                unit_class=None,
                unit_of_measurement=self._currency,
            )
            async_add_external_statistics(self.hass, cost_metadata, cost_statistics)
            _LOGGER.info(
                f"Reconciled {len(cost_statistics)} cost statistics for {utility_key} "
                f"with real billing data (from {earliest_start.isoformat()})"
            )

        self._last_reconciled_real_ts[utility_key] = latest_real_ts

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

        # Calculate estimated costs using tariff rates (including VAT)
        tariff_rate = self._tariff_rates.get(api_code, 0.0)
        estimated_daily_cost = (
            daily_consumption * tariff_rate * vat_multiplier if tariff_rate > 0 else 0.0
        )
        estimated_monthly_cost = (
            monthly_consumption * tariff_rate * vat_multiplier if tariff_rate > 0 else 0.0
        )
        estimated_prev_month_cost = (
            prev_month_consumption * tariff_rate * vat_multiplier if tariff_rate > 0 else 0.0
        )
        estimated_past_12_months_cost = (
            past_12_months_consumption * tariff_rate * vat_multiplier if tariff_rate > 0 else 0.0
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
            is_first_tariff_fetch = self._last_tariff_fetch is None
            should_fetch_tariffs = is_first_tariff_fetch or (
                dt_util.utcnow() - self._last_tariff_fetch
            ) > timedelta(days=1)

            if should_fetch_tariffs:
                await self._fetch_tariff_rates()

                # Daily: check if billing data appeared and reconcile cost statistics
                # Skip on first run — no existing statistics to reconcile yet
                if not is_first_tariff_fetch:
                    for utility_key in self._utilities:
                        if utility_key in UTILITY_TYPES:
                            try:
                                await self._reconcile_cost_statistics(utility_key)
                            except Exception:
                                _LOGGER.exception(
                                    f"Error reconciling cost statistics for {utility_key}"
                                )

            # Get current time in Swedish timezone for proper day boundaries
            swedish_tz = zoneinfo.ZoneInfo(SWEDISH_TIMEZONE)
            now_utc = dt_util.utcnow()

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

            # Calculate past 12 months start (rolling 12-month window, aligned to midnight)
            past_12_months_swedish = now_swedish.replace(
                hour=0, minute=0, second=0, microsecond=0
            ) - timedelta(days=365)
            past_12_months_start = past_12_months_swedish.astimezone(dt_timezone.utc)

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
