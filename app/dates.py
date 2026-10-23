"""Date et heure en français, dans le fuseau d'Hugo."""

from datetime import datetime
from zoneinfo import ZoneInfo

JOURS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
MOIS = [
    "janvier", "février", "mars", "avril", "mai", "juin",
    "juillet", "août", "septembre", "octobre", "novembre", "décembre",
]


def now(timezone: str) -> datetime:
    return datetime.now(ZoneInfo(timezone))


def format_day(dt: datetime) -> str:
    """« jeudi 23 octobre 2026 »"""
    return f"{JOURS[dt.weekday()]} {dt.day} {MOIS[dt.month - 1]} {dt.year}"


def format_now(dt: datetime) -> str:
    """« jeudi 23 octobre 2026, 19 h 05 (heure de Paris) »"""
    return f"{format_day(dt)}, {dt.hour} h {dt.minute:02d} (heure de Paris)"
