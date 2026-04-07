"""API client for Curves API integration."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import aiohttp
import homeassistant.util.dt as dt_util
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .const import API_BASE_URL, API_DATA_ENDPOINT, API_TOKEN_ENDPOINT

_LOGGER = logging.getLogger(__name__)


class CurvesAPIError(Exception):
    """Base exception for Curves API errors."""


class CurvesAPIClient:
    """Client for interacting with the Curves API."""

    def __init__(
        self,
        hass: HomeAssistant,
        username: str,
        password: str,
        domain_code: str,
    ) -> None:
        """Initialize the Curves API client."""
        self.hass = hass
        self._username = username
        self._password = password
        self._domain_code = domain_code
        self._session = async_get_clientsession(hass)
        self._access_token: str | None = None
        self._token_expires: datetime | None = None

    async def authenticate(self) -> None:
        """Authenticate with the Curves API and get an access token."""
        url = f"{API_BASE_URL}{API_TOKEN_ENDPOINT}"

        # API expects form-urlencoded data, not JSON
        form_data = aiohttp.FormData()
        form_data.add_field("grant_type", "password")
        form_data.add_field("username", self._username)
        form_data.add_field("password", self._password)
        form_data.add_field("domain", self._domain_code)
        form_data.add_field("issue_refresh_token", "true")

        headers = {
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
        }

        try:
            async with self._session.post(url, data=form_data, headers=headers) as response:
                if response.status != 200:
                    error_text = await response.text()
                    raise CurvesAPIError(f"Authentication failed: {response.status} - {error_text}")

                response_data = await response.json()
                # Try different possible token field names
                self._access_token = (
                    response_data.get("access_token")
                    or response_data.get("token")
                    or response_data.get("accessToken")
                    or (
                        response_data.get("result", {}).get("access_token")
                        if isinstance(response_data.get("result"), dict)
                        else None
                    )
                )

                # Token expiration (default to 1 hour if not provided)
                expires_in = (
                    response_data.get("expires_in")
                    or response_data.get("expiresIn")
                    or response_data.get("expires")
                    or 3600
                )
                self._token_expires = dt_util.utcnow() + timedelta(seconds=int(expires_in))

                if not self._access_token:
                    _LOGGER.error("API response: %s", response_data)
                    raise CurvesAPIError("No access token received from API")

                _LOGGER.debug("Successfully authenticated with Curves API")

        except aiohttp.ClientError as err:
            raise CurvesAPIError(f"Error connecting to Curves API: {err}") from err

    async def _ensure_authenticated(self) -> None:
        """Ensure we have a valid access token."""
        if (
            not self._access_token
            or not self._token_expires
            or dt_util.utcnow() >= (self._token_expires - timedelta(minutes=5))
        ):
            await self.authenticate()

    async def get_data(
        self,
        node_id: str | None = None,
        measuring_point_id: str | None = None,
        from_date: datetime | None = None,
        to_date: datetime | None = None,
        interval: str = "hour",
        grouping: str | None = None,
        utilities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Get electricity consumption data from the Curves API."""
        await self._ensure_authenticated()

        url = API_DATA_ENDPOINT.format(domaincode=self._domain_code)

        # Convert interval to API format (h, d, w, m)
        interval_map = {"hour": "h", "day": "d", "week": "w", "month": "m"}
        api_interval = interval_map.get(interval, "h")

        params: list[tuple[str, str]] = [
            ("interval", api_interval),
        ]

        if node_id:
            params.append(("nodeID", node_id))
            params.append(("includeSubNodes", "true"))

        if measuring_point_id:
            params.append(("measuringpointid", measuring_point_id))

        # Convert datetime to Unix timestamp, aligned to interval start
        def align_to_interval(dt: datetime, interval: str) -> datetime:
            """Align datetime to the start of the interval."""
            if interval == "h":  # Hourly - round to start of hour
                return dt.replace(minute=0, second=0, microsecond=0)
            elif interval == "d":  # Daily - round to start of day
                return dt.replace(hour=0, minute=0, second=0, microsecond=0)
            elif interval == "w":  # Weekly - round to start of week (Monday)
                days_since_monday = dt.weekday()
                aligned = dt - timedelta(days=days_since_monday)
                return aligned.replace(hour=0, minute=0, second=0, microsecond=0)
            elif interval == "m":  # Monthly - round to start of month
                return dt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
            else:  # Default to hour alignment
                return dt.replace(minute=0, second=0, microsecond=0)

        if from_date:
            # Align to interval start and convert to Unix timestamp
            aligned_from = align_to_interval(from_date, api_interval)
            params.append(("from", str(int(aligned_from.timestamp()))))
        else:
            # Default to 24 hours ago if not specified
            default_from = dt_util.utcnow() - timedelta(days=1)
            aligned_default_from = align_to_interval(default_from, api_interval)
            params.append(("from", str(int(aligned_default_from.timestamp()))))

        if to_date:
            # Align to interval start and convert to Unix timestamp
            aligned_to = align_to_interval(to_date, api_interval)
            # For hourly intervals, add 1 hour to include the current hour period
            # API "to" is likely exclusive, so we need the next hour boundary
            if api_interval == "h":
                aligned_to = aligned_to + timedelta(hours=1)
            params.append(("to", str(int(aligned_to.timestamp()))))
        else:
            # Default to current time if not specified, aligned to interval
            now = dt_util.utcnow()
            aligned_now = align_to_interval(now, api_interval)
            # For hourly intervals, add 1 hour to include the current hour period
            if api_interval == "h":
                aligned_now = aligned_now + timedelta(hours=1)
            params.append(("to", str(int(aligned_now.timestamp()))))

        if grouping:
            params.append(("grouping", grouping))
        else:
            params.append(("grouping", "measuringpoint"))

        # Utility is required - default to electricity consumption if not specified
        if utilities:
            params.extend(("utl", util) for util in utilities)
        else:
            # Default utilities for electricity consumption
            params.append(("utl", "ELEC[con]"))
            params.append(("utl", "ELEC[price]"))
            params.append(("utl", "ELEC[co2]"))

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Accept": "*/*",
            "Content-Type": "application/json",
        }

        try:
            async with self._session.get(
                f"{API_BASE_URL}{url}", headers=headers, params=params
            ) as response:
                if response.status == 401:
                    # Token expired, re-authenticate and retry
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    async with self._session.get(
                        f"{API_BASE_URL}{url}", headers=headers, params=params
                    ) as retry_response:
                        if retry_response.status != 200:
                            error_text = await retry_response.text()
                            raise CurvesAPIError(
                                f"Failed to fetch data: {retry_response.status} - {error_text}"
                            )
                        return await retry_response.json()

                if response.status != 200:
                    error_text = await response.text()
                    raise CurvesAPIError(f"Failed to fetch data: {response.status} - {error_text}")

                data = await response.json()
                return data if isinstance(data, list) else [data]

        except aiohttp.ClientError as err:
            raise CurvesAPIError(f"Error fetching data from Curves API: {err}") from err

    async def get_measuring_devices(
        self,
        external_key: str | None = None,
        internal_key: str | None = None,
        status: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get list of measuring devices."""
        await self._ensure_authenticated()

        url = f"/api/{self._domain_code}/measuringdevices"

        params: dict[str, Any] = {}
        if external_key:
            params["externalkey"] = external_key
        if internal_key:
            params["internalkey"] = internal_key
        if status:
            params["status"] = status

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._session.get(
                f"{API_BASE_URL}{url}", headers=headers, params=params
            ) as response:
                if response.status == 401:
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    async with self._session.get(
                        f"{API_BASE_URL}{url}", headers=headers, params=params
                    ) as retry_response:
                        if retry_response.status != 200:
                            error_text = await retry_response.text()
                            raise CurvesAPIError(
                                f"Failed to fetch measuring devices: "
                                f"{retry_response.status} - {error_text}"
                            )
                        return await retry_response.json()

                if response.status != 200:
                    error_text = await response.text()
                    raise CurvesAPIError(
                        f"Failed to fetch measuring devices: {response.status} - {error_text}"
                    )

                return await response.json()

        except aiohttp.ClientError as err:
            raise CurvesAPIError(f"Error fetching measuring devices: {err}") from err

    async def get_nodes(
        self,
        node_id: str | None = None,
        include_subnodes: bool = True,
    ) -> list[dict[str, Any]]:
        """Get list of nodes."""
        await self._ensure_authenticated()

        url = f"/api/{self._domain_code}/nodes"

        params: dict[str, Any] = {
            "includesubnodes": "true" if include_subnodes else "false",
        }
        if node_id:
            params["nodeid"] = node_id

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._session.get(
                f"{API_BASE_URL}{url}", headers=headers, params=params
            ) as response:
                if response.status == 401:
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    async with self._session.get(
                        f"{API_BASE_URL}{url}", headers=headers, params=params
                    ) as retry_response:
                        if retry_response.status != 200:
                            error_text = await retry_response.text()
                            raise CurvesAPIError(
                                f"Failed to fetch nodes: {retry_response.status} - {error_text}"
                            )
                        return await retry_response.json()

                if response.status != 200:
                    error_text = await response.text()
                    raise CurvesAPIError(f"Failed to fetch nodes: {response.status} - {error_text}")

                return await response.json()

        except aiohttp.ClientError as err:
            raise CurvesAPIError(f"Error fetching nodes: {err}") from err

    async def get_utilities(self) -> list[dict[str, Any]]:
        """Get list of available utilities."""
        await self._ensure_authenticated()

        url = f"/api/{self._domain_code}/utilities"

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._session.get(f"{API_BASE_URL}{url}", headers=headers) as response:
                if response.status == 401:
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    async with self._session.get(
                        f"{API_BASE_URL}{url}", headers=headers
                    ) as retry_response:
                        if retry_response.status != 200:
                            error_text = await retry_response.text()
                            raise CurvesAPIError(
                                f"Failed to fetch utilities: {retry_response.status} - {error_text}"
                            )
                        return await retry_response.json()

                if response.status != 200:
                    error_text = await response.text()
                    raise CurvesAPIError(
                        f"Failed to fetch utilities: {response.status} - {error_text}"
                    )

                return await response.json()

        except aiohttp.ClientError as err:
            raise CurvesAPIError(f"Error fetching utilities: {err}") from err

    async def get_billing_results(
        self,
        node_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Get billing results for tariff rates."""
        await self._ensure_authenticated()

        url = f"/api/{self._domain_code}/billingresults"

        params: dict[str, Any] = {}
        if node_id:
            params["nodeID"] = node_id

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        try:
            async with self._session.get(
                f"{API_BASE_URL}{url}", headers=headers, params=params
            ) as response:
                if response.status == 401:
                    await self.authenticate()
                    headers["Authorization"] = f"Bearer {self._access_token}"
                    async with self._session.get(
                        f"{API_BASE_URL}{url}", headers=headers, params=params
                    ) as retry_response:
                        if retry_response.status != 200:
                            error_text = await retry_response.text()
                            raise CurvesAPIError(
                                f"Failed to fetch billing results: "
                                f"{retry_response.status} - {error_text}"
                            )
                        return await retry_response.json()

                if response.status != 200:
                    error_text = await response.text()
                    raise CurvesAPIError(
                        f"Failed to fetch billing results: {response.status} - {error_text}"
                    )

                return await response.json()

        except aiohttp.ClientError as err:
            raise CurvesAPIError(f"Error fetching billing results: {err}") from err
