"""Calendar day of a portfolio snapshot.

The day is the user's local date, not UTC. ``users.settings["timezone"]`` is an
IANA name; a missing or unknown value falls back to Europe/Minsk (the owner's
zone). A save at 00:30 in Minsk therefore belongs to that calendar day, not to
the previous UTC date.
"""
from __future__ import annotations

from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

DEFAULT_TIMEZONE = "Europe/Minsk"


def resolve_timezone(settings: dict | None) -> ZoneInfo:
    raw = ""
    if isinstance(settings, dict) and settings.get("timezone"):
        raw = str(settings["timezone"]).strip()
    try:
        return ZoneInfo(raw or DEFAULT_TIMEZONE)
    except ZoneInfoNotFoundError:
        return ZoneInfo(DEFAULT_TIMEZONE)


def local_today(settings: dict | None = None, now: datetime | None = None) -> date:
    moment = now or datetime.now(timezone.utc)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(resolve_timezone(settings)).date()
