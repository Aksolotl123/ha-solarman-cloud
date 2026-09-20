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
# Production history. ``scope`` is total (one record per year), year (one record
# per month) or month (one record per day).
PATH_HISTORY_STATS = "/maintain-s/history/power/{station_id}/stats/{scope}"

# Long-term statistics fed with the monthly history pulled from the cloud.
STATISTIC_MONTHLY_PRODUCTION = "{domain}:station_{station_id}_production_monthly"

# How many months of history are published as a state attribute. Statistics keep
# every month; the attribute is capped because the recorder stores it on each
# write, and two years is more than any report needs.
MONTHLY_HISTORY_LIMIT = 24

MANUFACTURER = "Solarman"
