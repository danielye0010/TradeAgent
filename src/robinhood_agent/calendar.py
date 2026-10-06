"""Pinned exchange calendar; no weekday-only fallbacks. Quotes still prove venue freshness."""

from datetime import datetime
from functools import lru_cache
from zoneinfo import ZoneInfo

from .model import Halt


@lru_cache(maxsize=8)
def calendar(year):
    import exchange_calendars as xcals

    return xcals.get_calendar("XNYS", start=f"{year - 1}-01-01", end=f"{year + 1}-12-31")


def session_bounds(day):
    try:
        cal = calendar(day.year)
        label = day.isoformat()
        if not cal.is_session(label):
            return None
        return cal.session_open(label).timestamp(), cal.session_close(label).timestamp()
    except Exception as exc:
        raise Halt("exchange calendar unavailable/out of range") from exc


def regular_session(now):
    day = datetime.fromtimestamp(now, ZoneInfo("America/New_York")).date()
    bounds = session_bounds(day)
    return bounds is not None and bounds[0] <= now < bounds[1]
