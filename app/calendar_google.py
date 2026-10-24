"""Google Agenda (agenda principal d'Hugo) via l'API Calendar, avec le refresh token du .env."""

import logging
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx

from app import dates
from app.llm import Toolbox

logger = logging.getLogger(__name__)

TOKEN_URL = "https://oauth2.googleapis.com/token"
EVENTS_URL = "https://www.googleapis.com/calendar/v3/calendars/primary/events"
SCOPE = "https://www.googleapis.com/auth/calendar.events"


class AgendaError(Exception):
    pass


@dataclass(frozen=True)
class Evenement:
    titre: str
    debut: datetime | None  # None : journée entière
    fin: datetime | None


class GoogleCalendar:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, tz: str):
        self._identifiants = {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }
        self.tz = tz
        self._jeton: str | None = None
        self._expire_a = 0.0
        self._http = httpx.AsyncClient(timeout=20)

    async def _token(self) -> str:
        # Le jeton d'accès vit une heure : on le renouvelle une minute avant
        if self._jeton is None or time.monotonic() > self._expire_a - 60:
            r = await self._http.post(TOKEN_URL, data=self._identifiants)
            if r.status_code != 200:
                logger.error("Renouvellement du jeton Google refusé : %s", r.text[:200])
                raise AgendaError(
                    "L'accès à Google Agenda a expiré ou a été retiré. "
                    "Il faut relancer scripts/google_auth.py."
                )
            data = r.json()
            self._jeton = data["access_token"]
            self._expire_a = time.monotonic() + data.get("expires_in", 3600)
        return self._jeton

    async def _request(self, method: str, **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        r = await self._http.request(method, EVENTS_URL, headers=headers, **kwargs)
        if r.status_code >= 400:
            logger.error("Erreur Google Agenda %s : %s", r.status_code, r.text[:200])
            raise AgendaError(f"Google Agenda a répondu par une erreur ({r.status_code}).")
        return r.json()

    async def events_of_day(self, jour: date) -> list[Evenement]:
        debut = datetime.combine(jour, datetime.min.time(), ZoneInfo(self.tz))
        data = await self._request(
            "GET",
            params={
                "timeMin": debut.isoformat(),
                "timeMax": (debut + timedelta(days=1)).isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeZone": self.tz,
            },
        )
        return [parse_event(e) for e in data.get("items", [])]

    async def add_event(self, titre: str, debut: datetime, fin: datetime) -> dict:
        return await self._request(
            "POST",
            json={
                "summary": titre,
                "start": {"dateTime": debut.isoformat(), "timeZone": self.tz},
                "end": {"dateTime": fin.isoformat(), "timeZone": self.tz},
            },
        )


def parse_event(item: dict) -> Evenement:
    titre = item.get("summary") or "(sans titre)"
    start, end = item.get("start", {}), item.get("end", {})
    if "dateTime" in start:
        return Evenement(
            titre, datetime.fromisoformat(start["dateTime"]), datetime.fromisoformat(end["dateTime"])
        )
    return Evenement(titre, None, None)


def format_events(evenements: list[Evenement], tz: str) -> str:
    if not evenements:
        return "Rien de prévu."
    lignes = []
    for e in evenements:
        if e.debut is None:
            lignes.append(f"- Toute la journée : {e.titre}")
        else:
            debut = dates.format_hour(e.debut.astimezone(ZoneInfo(tz)))
            fin = dates.format_hour(e.fin.astimezone(ZoneInfo(tz)))
            lignes.append(f"- {debut} – {fin} : {e.titre}")
    return "\n".join(lignes)


# --- Outils pour Claude ------------------------------------------------------------

DEFINITIONS = {
    "list_events": {
        "name": "list_events",
        "description": "Lit l'agenda Google d'Hugo pour un jour donné (heure de Paris).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"jour": {"type": "string", "description": "Format AAAA-MM-JJ."}},
            "required": ["jour"],
            "additionalProperties": False,
        },
    },
    "add_event": {
        "name": "add_event",
        "description": (
            "Ajoute un événement dans l'agenda Google d'Hugo. Sans heure de fin précisée, "
            "compte une heure. Heures de Paris au format AAAA-MM-JJTHH:MM."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "titre": {"type": "string"},
                "debut": {"type": "string"},
                "fin": {"type": "string"},
            },
            "required": ["titre", "debut", "fin"],
            "additionalProperties": False,
        },
    },
}


def add_tools(toolbox: Toolbox, agenda: GoogleCalendar) -> None:
    tz = agenda.tz

    async def list_events(entree: dict) -> str:
        try:
            jour = date.fromisoformat(entree["jour"].strip())
        except ValueError:
            raise ValueError(f"Jour illisible : « {entree['jour']} ». Format attendu : AAAA-MM-JJ.")
        evenements = await agenda.events_of_day(jour)
        return f"{dates.format_day(datetime.combine(jour, datetime.min.time()))} :\n{format_events(evenements, tz)}"

    async def add_event(entree: dict) -> str:
        titre = entree["titre"].strip()
        debut = dates.parse_local(entree["debut"], tz)
        fin = dates.parse_local(entree["fin"], tz)
        if not titre:
            raise ValueError("Le titre de l'événement est vide.")
        if fin <= debut:
            raise ValueError("La fin de l'événement doit être après son début.")
        await agenda.add_event(titre, debut, fin)
        return (
            f"Ajouté à l'agenda : {titre}, le {dates.format_moment(debut, tz)} "
            f"jusqu'à {dates.format_hour(fin.astimezone(ZoneInfo(tz)))}."
        )

    toolbox.add(DEFINITIONS["list_events"], list_events)
    toolbox.add(DEFINITIONS["add_event"], add_event)
