# EcoGuard Curves Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/anton264/ecoguard_curves_home_assistant?style=for-the-badge)](LICENSE)
[![Maintainer](https://img.shields.io/badge/maintainer-@anton264-blue.svg?style=for-the-badge)](https://github.com/anton264)

A custom Home Assistant integration that tracks utility consumption (electricity, heat, hot water, cold water) using the [Curves API](https://integration.ecoguard.se/) from EcoGuard. This integration provides real-time monitoring of consumption and costs with support for multiple utility types and time periods.

## Features

- **Multi-Utility Support**: Track electricity, heat, hot water, and cold water consumption
- **Real-time Monitoring**: Tracks current usage and cumulative consumption
- **Cost Calculation**: Automatically calculates costs based on consumption and configurable currency
- **Tariff-Based Estimation**: Real-time cost estimates for unbilled periods using latest tariff rates
- **Time Periods**: Provides daily, monthly, year-to-date, and last month stats
- **Historical Billing**: Dedicated "Last Month" sensors show completed billing period data
- **Rate Transparency**: Tariff rate sensors for comparing costs across utilities
- **Config Flow**: Easy setup via Home Assistant UI with utility selection
- **Token Management**: Handles authentication and token refreshing automatically
- **Flexible Configuration**: Select any combination of utilities during setup

## Table of Contents

- [Prerequisites](#prerequisites)
- [Installation](#installation)
  - [HACS Installation](#hacs-installation)
  - [Manual Installation](#manual-installation)
- [Configuration](#configuration)
- [Usage](#usage)
  - [Sensors](#sensors-created)
  - [Automations](#example-automations)
  - [Dashboard](#lovelace-dashboard-example)
- [Important Notes](#important-notes)
  - [Cost Data and Billing Periods](#cost-data-and-billing-periods)
  - [Viewing Historical Costs](#viewing-historical-costs)
- [Troubleshooting](#troubleshooting)
- [Development](#development)
- [Credits](#credits)

## Prerequisites

Before installing this integration, make sure you have:

- **Home Assistant** 2023.1 or later installed
- A **Curves API account** with valid credentials
- Your **Domain Code** (visible in the URL when logged into Curves, e.g., `HSBBrfBerget`)
- Your **Node ID** (visible in the URL when logged into Curves, e.g., `123`)

### Finding Your Credentials

1. Log in to the Curves web interface at [curves-24.ecoguard.se](https://curves-24.ecoguard.se/)
2. Navigate to your dashboard or node view
3. Check the URL in your browser's address bar: `https://curves-24.ecoguard.se/[DOMAIN_CODE]/safetySensors/[NODE_ID]`
   - Example: `https://curves-24.ecoguard.se/HSBBrfBerget/safetySensors/321`
   - **Domain Code**: `HSBBrfBerget`
   - **Node ID**: `321`

## Installation

### HACS Installation

1. Open HACS.
2. Click on the three dots in the top right corner.
3. Select "Custom repositories".
4. Add the URL: `https://github.com/anton264/ecoguard_curves_home_assistant`
5. Select **Integration** as the category.
6. Click "Add" and then install the integration.
7. Restart Home Assistant.

### Manual Installation

1. Download the latest release.
2. Copy the `custom_components/ecoguard_curves` directory to your Home Assistant `config/custom_components/` directory.
3. Restart Home Assistant.

## Configuration

The integration uses a config flow, so you can set it up entirely through the Home Assistant UI.

1. Navigate to **Settings** → **Devices & Services**.
2. Click **Add Integration**.
3. Search for **EcoGuard Curves**.
4. Enter your credentials:

| Field | Description | Required | Example |
|-------|-------------|----------|---------|
| **Username** | Your Curves API username | Yes | `user@example.com` |
| **Password** | Your Curves API password | Yes | `password123` |
| **Domain Code** | Domain code from Curves URL | Yes | `HSBBrfBerget` |
| **Node ID** | Node ID from Curves URL | Yes | `123` |
| **Measuring Point ID** | Specific measuring point | No | `MP001` |
| **Utilities** | Select utilities to track | Yes | `Electricity, Heat, Hot Water, Cold Water` |
| **Update Interval** | Fetch frequency (seconds) | Yes | `300` (5 min) |
| **Currency** | Cost currency | Yes | `SEK` |
| **VAT Rate** | VAT percentage | Yes | `25` |

### Options

You can adjust the configuration after installation by clicking **Configure** on the integration entry.

## Usage

### Sensors Created

Sensors are created dynamically based on the utilities you select during setup:

- **Heat & Cold Water**: 15 sensors per utility
- **Electricity & Hot Water**: 21 sensors per utility (includes CO2 tracking)
- **EcoGuard Service Fee**: 1 sensor (hidden by default, applies to all utilities)

#### Consumption Sensors
| Sensor Name Pattern | Description | Unit |
|------------|-------------|------|
| **{Utility} Consumption** | Total cumulative consumption | kWh or m³ |
| **{Utility} Daily Consumption** | Consumption today | kWh or m³ |
| **{Utility} Monthly Consumption** | Consumption this month | kWh or m³ |
| **{Utility} Last Month Consumption** | Consumption last month (completed billing period) | kWh or m³ |
| **{Utility} Past 12 Months Consumption** | Rolling 12-month consumption | kWh or m³ |

#### Cost Sensors (Billed)
| Sensor Name Pattern | Description | Unit |
|------------|-------------|------|
| **{Utility} Cost** | Current cost (latest period)* | Currency |
| **{Utility} Daily Cost** | Cost today* | Currency |
| **{Utility} Monthly Cost** | Cost this month* | Currency |
| **{Utility} Year to Date Cost** | Cost year-to-date (completed months)* | Currency |
| **{Utility} Last Month Cost** | Cost last month (completed billing period)* | Currency |
| **{Utility} Past 12 Months Cost** | Rolling 12-month cost (completed periods)* | Currency |

_*May show 0.00 for unbilled periods. See [Cost Data and Billing Periods](#cost-data-and-billing-periods) below._

#### Cost Sensors (Estimated)
| Sensor Name Pattern | Description | Unit |
|------------|-------------|------|
| **{Utility} Estimated Daily Cost** | Estimated cost for today based on current tariff | Currency |
| **{Utility} Estimated Monthly Cost** | Estimated cost for this month based on current tariff | Currency |
| **{Utility} Estimated Last Month Cost** | Estimated cost for last month based on current tariff | Currency |

_See [Tariff-Based Cost Estimation](#tariff-based-cost-estimation) for how estimates are calculated._

#### Tariff & Fee Sensors
| Sensor Name Pattern | Description | Unit |
|------------|-------------|------|
| **{Utility} Last Known Rate** | Tariff rate from most recent billing period | Currency/kWh or Currency/m³ |
| **EcoGuard Service Fee** | Monthly service fee (hidden by default) | Currency |

#### CO2 Sensors (Electricity & Hot Water only)
| Sensor Name Pattern | Description | Unit |
|------------|-------------|------|
| **{Utility} CO2** | Current CO2 emission | kg |
| **{Utility} Daily CO2** | CO2 emission today | kg |
| **{Utility} Monthly CO2** | CO2 emission this month | kg |
| **{Utility} Year to Date CO2** | CO2 emission year-to-date | kg |
| **{Utility} Last Month CO2** | CO2 emission last month | kg |
| **{Utility} Past 12 Months CO2** | Rolling 12-month CO2 emission | kg |

**Example**: If you select "Heat", "Cold Water", and "Hot Water", you'll get **52 sensors** total:
- Heat: 15 sensors (5 consumption + 6 cost + 3 estimated cost + 1 tariff rate)
- Cold Water: 15 sensors (5 consumption + 6 cost + 3 estimated cost + 1 tariff rate)
- Hot Water: 21 sensors (5 consumption + 6 cost + 3 estimated cost + 1 tariff rate + 6 CO2)
- EcoGuard Service Fee: 1 sensor (shared across all utilities)

**Units**:
- Electricity & Heat: kWh (kilowatt-hours)
- Hot Water & Cold Water: m³ (cubic meters)
- CO2: kg (kilograms)

### Example Automations

#### Daily Consumption Report

```yaml
automation:
  - alias: "Daily Electricity Report"
    trigger:
      - platform: time
        at: "22:00:00"
    action:
      - service: notify.mobile_app_your_phone
        data:
          title: "Daily Electricity Report"
          message: >
            Today's consumption: {{ states('sensor.electricity_daily_consumption') }} kWh
            Last month's cost: {{ states('sensor.electricity_last_month_cost') }} SEK
```

### Lovelace Dashboard Example

```yaml
type: entities
entities:
  - entity: sensor.electricity_consumption
    name: Total Consumption
  - entity: sensor.electricity_daily_consumption
    name: Today
  - entity: sensor.electricity_monthly_consumption
    name: This Month
  - entity: sensor.electricity_last_month_consumption
    name: Last Month
  - entity: sensor.electricity_cost
    name: Current Cost
  - entity: sensor.electricity_daily_cost
    name: Today's Cost
  - entity: sensor.electricity_monthly_cost
    name: This Month Cost
  - entity: sensor.electricity_last_month_cost
    name: Last Month Cost (Billed)
```

## Important Notes

### Cost Data and Billing Periods

**Cost sensors may show zero for current billing periods.** Based on testing, the EcoGuard Curves API appears to only provide cost data for completed billing periods.

- **Daily Cost**: May show `0.00` for the current day
- **Monthly Cost**: May show `0.00` for the current month
- **Year to Date Cost**: Appears to show cost for **completed months only**

**Possible reason:** Utility companies may only finalize cost data after billing periods are closed. During an ongoing billing period, consumption is tracked but costs might not yet be available from the API.

### Tariff-Based Cost Estimation

To address zero costs shown for unbilled periods, this integration provides **estimated cost sensors** that multiply current consumption by the tariff rate from your last completed billing period.

#### How It Works

The integration simply takes:
```
Estimated Cost = Current Consumption × Rate from Last Billed Period
```

**Important:** The estimation uses whatever rate was in your most recent completed billing period, regardless of how old it is or whether it matches the current season. For example:
- In March 2026, estimates may use rates from January 2026 (if that's the last billed period)
- In October, you might still be using rates from August (different season)
- Heat rates vary significantly by season, so summer rates will underestimate winter costs and vice versa

The tariff rate is fetched once daily from the EcoGuard API and displayed in the `{Utility} Last Known Rate` sensor.

#### Accuracy and Limitations

Analysis of 18 months of billing data (single Norwegian account) shows:

| Utility | Mean Error | Max Error | Notes |
|---------|------------|-----------|-------|
| **Cold Water** | 2.5% | 19.3% | Relatively stable, occasional rate changes |
| **Hot Water** | 8.1% | 14.4% | Moderate rate fluctuations |
| **Heat** | 13.8% | 28.9% | Highly variable due to seasonal pricing |

**Overall mean estimation error: 8.1%** when using the previous completed billing period's rate.

**Limitations:**
- Tariff rates change month-to-month (average change: 8%)
- Heat rates vary seasonally due to energy market pricing (winter vs summer)
- Estimates can be significantly off if you're between billing periods and rates have changed
- Analysis based on one account in the Norwegian market; results will vary by location and provider
- **Not suitable for precise budgeting** — useful for cost awareness and rough tracking only

The estimated sensors show approximate costs instead of "0.00" for unbilled periods, but treat them as rough indicators rather than accurate predictions.

#### Available Estimated Sensors

For each utility, the following estimated cost sensors are provided:

| Sensor | Period Covered | Updates |
|--------|---------------|---------|
| `{Utility} Estimated Daily Cost` | Today (00:00 to now) | Every 5 minutes |
| `{Utility} Estimated Monthly Cost` | This month (1st to now) | Every 5 minutes |
| `{Utility} Estimated Last Month Cost` | Previous calendar month | Daily |

#### Sensor Attributes

Estimated cost sensors include these attributes:
- `attribution`: "Estimated using latest tariff rates"
- `tariff_rate`: Current rate being used (e.g., `72.62`)
- `tariff_last_updated`: When the tariff rate was last fetched

#### Use Cases

**Budget Tracking**:
```yaml
- sensor.heat_estimated_monthly_cost  # Real-time monthly spending estimate
- sensor.heat_monthly_cost             # Actual billed cost (updates after month ends)
```

**Rate Comparison** (comparing heat vs electricity):
```yaml
- sensor.heat_last_known_rate         # e.g., 1.45 NOK/kWh
- sensor.electricity_tariff_rate       # e.g., 2.50 NOK/kWh (from another integration)
```

**Automation Example**:
```yaml
automation:
  - alias: "Monthly Cost Alert"
    trigger:
      - platform: numeric_state
        entity_id: sensor.heat_estimated_monthly_cost
        above: 2000
    action:
      - service: notify.mobile_app
        data:
          message: "Heat cost estimate exceeds 2000 NOK this month"
```

### Viewing Historical Costs

The easiest way to view costs from the most recent completed billing period is to use the **Last Month** sensors (e.g., `sensor.heat_last_month_cost`). These sensors always show data from the previous calendar month.

For older historical data, use one of these methods:

#### Method 1: History Panel
1. Go to **History** in Home Assistant
2. Select the cost sensor (e.g., `sensor.cold_water_monthly_cost`)
3. Use the date picker to select a past month
4. View the cost values for that completed billing period

#### Method 2: Developer Tools
1. Go to **Developer Tools** → **States**
2. Find your cost sensor (e.g., `sensor.heat_monthly_cost`)
3. Click on it to view state history
4. Use the time range selector to view past periods

#### Method 3: Lovelace History Card
Add a history card to your dashboard to track historical costs:

```yaml
type: history-graph
entities:
  - entity: sensor.heat_monthly_cost
  - entity: sensor.cold_water_monthly_cost
  - entity: sensor.hot_water_monthly_cost
hours_to_show: 720  # 30 days (shows last month)
title: Last Month's Utility Costs
```

#### Method 4: SQL Query (Advanced)
If you have the **Recorder** integration enabled, you can query historical data:

```sql
SELECT entity_id, state, last_changed
FROM states
WHERE entity_id = 'sensor.heat_monthly_cost'
  AND last_changed >= '2026-01-01'
  AND last_changed < '2026-02-01'
ORDER BY last_changed DESC;
```

**Example:** To check water costs for January 2026, view the monthly cost sensors in early February 2026 after the billing period closes.

## Troubleshooting

### Common Issues

- **Authentication Fails**: Check username, password, and **Domain Code** (case-sensitive).
- **No Data (0 kWh)**: Verify **Node ID** and ensure your account has access to it.
- **Not Updating**: Check **Update Interval** (default 300s). Don't set it too low (< 60s) to avoid rate limits.
- **Cost Sensors Show 0.00**: This may be expected for current billing periods. See [Cost Data and Billing Periods](#cost-data-and-billing-periods) above.

### Debugging

Enable debug logging to see API responses:

```yaml
logger:
  default: info
  logs:
    custom_components.ecoguard_curves: debug
```

## Development

This integration follows Home Assistant best practices:
- `config_flow.py`: Handles setup and options.
- `coordinator.py`: Manages API data fetching (`DataUpdateCoordinator`).
- `sensor.py`: Defines sensor entities.

## Credits

- Powered by the [Curves API](https://integration.ecoguard.se/) from EcoGuard.
- Developed by [@anton264](https://github.com/anton264).

---

**Note:** This integration is not officially supported by EcoGuard.
