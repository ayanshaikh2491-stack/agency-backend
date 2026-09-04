# admin/tools/sba_time.py
"""SBA Timezone Engine — human-style time awareness for the 24/7 autopilot.

Owner configures SBA_OWNER_TIMEZONE (default Asia/Kolkata). Each lead's
timezone is derived from its US state (or city_state string). The autopilot
uses this to send emails only in the lead's local business hours and to pick
meeting slots that overlap the owner's business hours.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

OWNER_TZ = os.environ.get("SBA_OWNER_TIMEZONE", "Asia/Kolkata")

# US state abbreviation -> IANA timezone (no-DST states included)
STATE_TZ: dict[str, str] = {
    "AL": "America/Chicago", "AK": "America/Anchorage", "AZ": "America/Phoenix",
    "AR": "America/Chicago", "CA": "America/Los_Angeles", "CO": "America/Denver",
    "CT": "America/New_York", "DE": "America/New_York", "FL": "America/New_York",
    "GA": "America/New_York", "HI": "Pacific/Honolulu", "ID": "America/Boise",
    "IL": "America/Chicago", "IN": "America/Indiana/Indianapolis", "IA": "America/Chicago",
    "KS": "America/Chicago", "KY": "America/New_York", "LA": "America/Chicago",
    "ME": "America/New_York", "MD": "America/New_York", "MA": "America/New_York",
    "MI": "America/Detroit", "MN": "America/Chicago", "MS": "America/Chicago",
    "MO": "America/Chicago", "MT": "America/Denver", "NE": "America/Chicago",
    "NV": "America/Los_Angeles", "NH": "America/New_York", "NJ": "America/New_York",
    "NM": "America/Denver", "NY": "America/New_York", "NC": "America/New_York",
    "ND": "America/Chicago", "OH": "America/New_York", "OK": "America/Chicago",
    "OR": "America/Los_Angeles", "PA": "America/New_York", "RI": "America/New_York",
    "SC": "America/New_York", "SD": "America/Chicago", "TN": "America/Chicago",
    "TX": "America/Chicago", "UT": "America/Denver", "VT": "America/New_York",
    "VA": "America/New_York", "WA": "America/Los_Angeles", "WV": "America/New_York",
    "WI": "America/Chicago", "WY": "America/Denver", "DC": "America/New_York",
}

_TZ_CACHE: dict[str, Any] = {}


def load_zone(name: str) -> Any:
    """Return a tzinfo for an IANA name, with tzdata fallback (Windows)."""
    if name in _TZ_CACHE:
        return _TZ_CACHE[name]
    try:
        zone = ZoneInfo(name)
    except ZoneInfoNotFoundError:
        try:
            import tzdata  # noqa: F401  (registers system tz database)
            zone = ZoneInfo(name)
        except Exception:  # noqa: BLE001
            zone = dt.timezone.utc
    _TZ_CACHE[name] = zone
    return zone


def now_in(tz: str) -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc).astimezone(load_zone(tz))


def _extract_state(lead: dict) -> str:
    state = (lead.get("state") or "").strip().upper()
    if len(state) == 2:
        return state
    city_state = (lead.get("city_state") or "").strip()
    m = re.search(r"\b([A-Z]{2})\b", city_state.upper())
    if m:
        return m.group(1)
    return ""


def lead_timezone(lead: dict) -> str:
    st = _extract_state(lead)
    return STATE_TZ.get(st, OWNER_TZ)


def lead_business_hours(lead: dict, tz: str | None = None) -> bool:
    tz = tz or lead_timezone(lead)
    now = now_in(tz)
    if now.weekday() >= 6:  # Sunday = 6
        return False
    return 9 <= now.hour < 17


def next_business_time(lead: dict, tz: str | None = None) -> str:
    tz = tz or lead_timezone(lead)
    now = now_in(tz)
    candidate = now.replace(hour=9, minute=0, second=0, microsecond=0)
    while True:
        if candidate > now and candidate.weekday() < 6:
            return candidate.isoformat()
        candidate += dt.timedelta(days=1)


def meeting_slot(lead: dict, owner_tz: str = OWNER_TZ, hour: int | None = None) -> tuple[str, str]:
    """Pick the lead's next business morning at 10:00 local (skips weekends/past).

    Pass ``hour`` (0-23 local) when the owner asked for a specific time like
    "haan 3 baje". Returns (iso UTC datetime, human text like "India raat
    8:30 = US subah 10").
    """
    h = hour if isinstance(hour, int) and 0 <= hour <= 23 else 10
    lead_tz = lead_timezone(lead)
    lead_now = now_in(lead_tz)
    # Lead's next business morning at h:00 local
    slot = lead_now.replace(hour=h, minute=0, second=0, microsecond=0)
    if slot <= lead_now or slot.weekday() >= 6:
        slot += dt.timedelta(days=1)
        while slot.weekday() >= 6:
            slot += dt.timedelta(days=1)
    iso = slot.astimezone(dt.timezone.utc).isoformat()
    owner_local = slot.astimezone(load_zone(owner_tz))
    lead_local = slot.astimezone(load_zone(lead_tz))
    text = (
        f"Meeting: India {owner_local:%I:%M %p} = US ({lead_tz}) {lead_local:%I:%M %p}, "
        f"date {slot:%Y-%m-%d}"
    )
    return iso, text


def human_time(iso: str, tz: str = OWNER_TZ) -> str:
    parsed = dt.datetime.fromisoformat(iso)
    return parsed.astimezone(load_zone(tz)).strftime("%Y-%m-%d %I:%M %p %Z")
