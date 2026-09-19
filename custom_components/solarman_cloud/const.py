"""Constants for the Solarman Cloud (mydc) integration."""
from __future__ import annotations

from datetime import timedelta

DOMAIN = "solarman_cloud"

# Config / options keys
CONF_REFRESH_TOKEN = "refresh_token"
CONF_REGION = "region"
CONF_WEBHOOK_ID = "webhook_id"
CONF_BASE_URL = "base_url"
CONF_STATION_ID = "station_id"
CONF_STATION_NAME = "station_name"
CONF_SCAN_INTERVAL = "scan_interval"

# The web portal client id is a fixed public value used by the SolarmanPV web app.
CLIENT_ID = "test"
SYSTEM = "SOLARMAN"

DEFAULT_BASE_URL = "https://home.solarmanpv.com"
DEFAULT_REGION = "PL"
# Cloud refreshes roughly every 5 min; polling faster is wasted. Default 1 h per user's need.
DEFAULT_SCAN_INTERVAL = 3600
MIN_SCAN_INTERVAL = 300

# API paths
PATH_TOKEN = "/oauth2-s/oauth/token"
PATH_STATION_SEARCH = "/maintain-s/operating/station/search"
PATH_STATION_DETAIL = "/maintain-s/station/"
PATH_DEVICE_LIST = "/maintain-s/power/system/deviceList"

MANUFACTURER = "Solarman"
