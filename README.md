# EcoGuard Curves Integration for Home Assistant

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge)](https://github.com/hacs/integration)
[![License](https://img.shields.io/github/license/anton264/ecoguard_curves_home_assistant?style=for-the-badge)](LICENSE)
[![Maintainer](https://img.shields.io/badge/maintainer-@anton264-blue.svg?style=for-the-badge)](https://github.com/anton264)

A custom Home Assistant integration that tracks electricity consumption using the [Curves API](https://integration.ecoguard.se/) from EcoGuard. This integration provides real-time monitoring of electricity consumption and costs with support for multiple time periods.

## Features

- **Real-time Monitoring**: Tracks current power usage and cumulative consumption.
- **Cost Calculation**: Automatically calculates costs based on consumption and configurable currency.
- **Time Periods**: Provides daily, monthly, and yearly consumption and cost stats.
- **Config Flow**: Easy setup via Home Assistant UI.
- **Token Management**: Handles authentication and token refreshing automatically.

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
| **Update Interval** | Fetch frequency (seconds) | Yes | `300` (5 min) |
| **Currency** | Cost currency | Yes | `SEK` |

### Options

You can adjust the configuration after installation by clicking **Configure** on the integration entry.

## Usage

### Sensors Created

| Sensor Name | Entity ID | Description | Unit |
|------------|-----------|-------------|------|
| **Electricity Consumption** | `sensor.electricity_consumption` | Total cumulative consumption | kWh |
| **Electricity Daily Consumption** | `sensor.electricity_daily_consumption` | Consumption today | kWh |
| **Electricity Monthly Consumption** | `sensor.electricity_monthly_consumption` | Consumption this month | kWh |
| **Electricity Cost** | `sensor.electricity_cost` | Current cost (latest period) | Currency |
| **Electricity Daily Cost** | `sensor.electricity_daily_cost` | Cost today | Currency |
| **Electricity Monthly Cost** | `sensor.electricity_monthly_cost` | Cost this month | Currency |

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
            Cost: {{ states('sensor.electricity_daily_cost') }} SEK
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
  - entity: sensor.electricity_cost
    name: Current Cost
  - entity: sensor.electricity_daily_cost
    name: Today's Cost
  - entity: sensor.electricity_monthly_cost
    name: Monthly Cost
```

## Troubleshooting

### Common Issues

- **Authentication Fails**: Check username, password, and **Domain Code** (case-sensitive).
- **No Data (0 kWh)**: Verify **Node ID** and ensure your account has access to it.
- **Not Updating**: Check **Update Interval** (default 300s). Don't set it too low (< 60s) to avoid rate limits.

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