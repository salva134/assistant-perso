"""Point du matin : le programme de la journée, envoyé à 7 h 30."""

from datetime import datetime
from zoneinfo import ZoneInfo

from app import dates
from app.reminders import Rappel


def todays(rappels: list[Rappel], today: datetime, tz: str) -> list[Rappel]:
    """Rappels prévus le même jour (heure de Paris) que `today`."""
    jour = today.astimezone(ZoneInfo(tz)).date()
    return [r for r in rappels if r.due_at.astimezone(ZoneInfo(tz)).date() == jour]


def format_brief(today: datetime, rappels: list[Rappel], tz: str, agenda: str | None = None) -> str:
    lignes = [f"☀️ Bonjour Hugo ! On est {dates.format_day(today)}.", ""]
    if agenda is not None:
        lignes += ["📅 Ton agenda :", agenda, ""]
    if rappels:
        lignes.append("⏰ Tes rappels du jour :")
        lignes += [f"- {dates.format_hour(r.due_at.astimezone(ZoneInfo(tz)))} : {r.text}" for r in rappels]
    else:
        lignes.append("⏰ Aucun rappel aujourd'hui.")
    lignes += ["", "Bonne journée ! 🙂"]
    return "\n".join(lignes)
