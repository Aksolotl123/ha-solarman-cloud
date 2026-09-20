"""Feed the cloud's monthly production history into long-term statistics.

Home Assistant only keeps statistics for what it recorded itself, so a freshly
installed integration shows a chart that starts today. Solarman, however, knows
every month since the installation went live. Those months are written here as
an *external* statistic - one data point per month, carrying the running total -
which lets a statistics-graph card draw a bar per month for the whole history.

A separate statistic id is used rather than the production sensor's own, so the
imported history can never collide with the sums the recorder derives from the
live sensor.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import async_add_external_statistics
from homeassistant.const import UnitOfEnergy
from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import DOMAIN, STATISTIC_MONTHLY_PRODUCTION

_LOGGER = logging.getLogger(__name__)


def monthly_statistic_id(station_id: int) -> str:
    """Return the statistic id holding the station's monthly production."""
    return STATISTIC_MONTHLY_PRODUCTION.format(domain=DOMAIN, station_id=station_id)


def _month_start(hass: HomeAssistant, year: int, month: int) -> datetime:
    """Return the local start of a month, snapped to the top of an hour in UTC.

    Statistics timestamps must sit exactly on the hour. Local midnight is not
    one in zones with a half-hour offset, so there the point is moved to the
    next full hour - still inside the same month - instead of the previous one.
    """
    tzinfo = dt_util.get_time_zone(hass.config.time_zone) or dt_util.UTC
    start = dt_util.as_utc(datetime(year, month, 1, tzinfo=tzinfo))
    if start.minute or start.second or start.microsecond:
        start = start.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    return start


def async_import_monthly_production(
    hass: HomeAssistant,
    station_id: int,
    station_name: str,
    monthly: dict[tuple[int, int], float],
) -> None:
    """Write the ``{(year, month): kWh}`` history into statistics.

    Each point carries ``sum`` - the running total up to and including that
    month - because Home Assistant renders a bar as the difference between
    consecutive sums. Re-importing overwrites points already stored, so the
    current, still growing month can simply be sent again on every poll.
    """
    if not monthly:
        return

    metadata = StatisticMetaData(
        mean_type=StatisticMeanType.NONE,
        has_sum=True,
        name=f"{station_name}: produkcja miesięczna",
        source=DOMAIN,
        statistic_id=monthly_statistic_id(station_id),
        unit_class="energy",
        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )

    statistics: list[StatisticData] = []
    running_total = 0.0
    for (year, month), value in sorted(monthly.items()):
        running_total += value
        statistics.append(
            StatisticData(
                start=_month_start(hass, year, month),
                state=round(value, 2),
                sum=round(running_total, 2),
            )
        )

    async_add_external_statistics(hass, metadata, statistics)
    _LOGGER.debug(
        "Imported %s monthly production points for station %s (total %.2f kWh)",
        len(statistics),
        station_id,
        running_total,
    )
