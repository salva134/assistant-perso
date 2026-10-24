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


def format_hour(dt: datetime) -> str:
    """« 9 h 05 »"""
    return f"{dt.hour} h {dt.minute:02d}"


def format_now(dt: datetime) -> str:
    """« jeudi 23 octobre 2026, 19 h 05 (heure de Paris) »"""
    return f"{format_day(dt)}, {format_hour(dt)} (heure de Paris)"


def format_moment(dt: datetime, timezone: str) -> str:
    """Date absolue (UTC ou autre) affichée chez Hugo : « samedi 24 octobre 2026 à 9 h 00 »"""
    local = dt.astimezone(ZoneInfo(timezone))
    return f"{format_day(local)} à {format_hour(local)}"


def parse_local(text: str, timezone: str) -> datetime:
    """« 2026-10-24T09:00 » (heure de Paris si rien n'est précisé) → datetime avec fuseau."""
    try:
        dt = datetime.fromisoformat(text.strip())
    except ValueError:
        raise ValueError(f"Date illisible : « {text} ». Format attendu : AAAA-MM-JJTHH:MM.") from None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(timezone))
    return dt
