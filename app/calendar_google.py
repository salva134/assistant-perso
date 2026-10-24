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
    id: str = ""  # identifiant Google, pour modifier ou supprimer l'événement
    jour: date | None = None  # date d'un événement « journée entière »


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

    async def _request(self, method: str, event_id: str = "", **kwargs) -> dict:
        headers = {"Authorization": f"Bearer {await self._token()}"}
        url = f"{EVENTS_URL}/{event_id}" if event_id else EVENTS_URL
        r = await self._http.request(method, url, headers=headers, **kwargs)
        if r.status_code == 404 and event_id:
            raise AgendaError("Cet événement n'existe plus dans l'agenda.")
        if r.status_code >= 400:
            logger.error("Erreur Google Agenda %s : %s", r.status_code, r.text[:200])
            raise AgendaError(f"Google Agenda a répondu par une erreur ({r.status_code}).")
        return r.json() if r.content else {}  # une suppression ne renvoie rien

    async def search(self, texte: str, jours: int = 90) -> list[Evenement]:
        """Événements à venir (jusqu'à `jours` jours) qui contiennent `texte`."""
        maintenant = datetime.now(ZoneInfo(self.tz))
        data = await self._request(
            "GET",
            params={
                "q": texte,
                "timeMin": maintenant.isoformat(),
                "timeMax": (maintenant + timedelta(days=jours)).isoformat(),
                "singleEvents": "true",
                "orderBy": "startTime",
                "timeZone": self.tz,
                "maxResults": 10,
            },
        )
        return [parse_event(e) for e in data.get("items", [])]

    async def update_event(
        self, event_id: str, titre: str | None = None, debut: datetime | None = None, fin: datetime | None = None
    ) -> Evenement:
        """Change le titre et/ou l'horaire. Un nouveau début sans nouvelle fin garde la même durée."""
        actuel = parse_event(await self._request("GET", event_id))
        corps: dict = {}
        if titre:
            corps["summary"] = titre
        if debut is not None:
            if fin is None:
                duree = (actuel.fin - actuel.debut) if actuel.debut and actuel.fin else timedelta(hours=1)
                fin = debut + duree
        if debut is not None or fin is not None:
            debut = debut or actuel.debut
            if debut is None:
                raise AgendaError("Donne aussi l'heure de début pour un événement d'une journée entière.")
            if fin <= debut:
                raise AgendaError("La fin de l'événement doit être après son début.")
            corps["start"] = {"dateTime": debut.isoformat(), "timeZone": self.tz}
            corps["end"] = {"dateTime": fin.isoformat(), "timeZone": self.tz}
        if not corps:
            raise AgendaError("Rien à changer : donne un nouveau titre ou un nouvel horaire.")
        return parse_event(await self._request("PATCH", event_id, json=corps))

    async def delete_event(self, event_id: str) -> None:
        await self._request("DELETE", event_id)

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
            titre, datetime.fromisoformat(start["dateTime"]), datetime.fromisoformat(end["dateTime"]),
            item.get("id", ""),
        )
    jour = date.fromisoformat(start["date"]) if "date" in start else None
    return Evenement(titre, None, None, item.get("id", ""), jour)


def format_with_refs(evenements: list[Evenement], tz: str) -> str:
    """Pour Claude : date, heure, titre et référence de chaque événement (pour le modifier)."""
    if not evenements:
        return "Aucun événement trouvé."
    lignes = []
    for e in evenements:
        if e.debut is None:
            quand = f"{dates.format_day(datetime.combine(e.jour, datetime.min.time()))}, toute la journée" if e.jour else "toute la journée"
        else:
            local = e.debut.astimezone(ZoneInfo(tz))
            quand = f"{dates.format_day(local)}, {dates.format_hour(local)} – {dates.format_hour(e.fin.astimezone(ZoneInfo(tz)))}"
        lignes.append(f"- {quand} : {e.titre} (réf. {e.id})")
    return "\n".join(lignes)


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
    "find_events": {
        "name": "find_events",
        "description": (
            "Cherche dans l'agenda d'Hugo les événements à venir (3 prochains mois) dont le titre contient "
            "un mot, par exemple « dentiste ». Donne la date, l'heure et la référence de chacun."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"recherche": {"type": "string"}},
            "required": ["recherche"],
            "additionalProperties": False,
        },
    },
    "update_event": {
        "name": "update_event",
        "description": (
            "Modifie un événement de l'agenda à partir de sa référence (voir list_events ou find_events). "
            "Laisse vide ce qui ne change pas. Si seul le début change, la durée est conservée. "
            "Heures de Paris au format AAAA-MM-JJTHH:MM."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "reference": {"type": "string"},
                "titre": {"type": "string", "description": "Nouveau titre, ou vide."},
                "debut": {"type": "string", "description": "Nouveau début, ou vide."},
                "fin": {"type": "string", "description": "Nouvelle fin, ou vide."},
            },
            "required": ["reference", "titre", "debut", "fin"],
            "additionalProperties": False,
        },
    },
    "delete_event": {
        "name": "delete_event",
        "description": (
            "Supprime un événement de l'agenda à partir de sa référence (voir list_events ou find_events). "
            "Si plusieurs événements correspondent à la demande d'Hugo, demande-lui lequel avant de supprimer."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"reference": {"type": "string"}},
            "required": ["reference"],
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
        detail = format_with_refs(evenements, tz) if evenements else "Rien de prévu."
        return f"{dates.format_day(datetime.combine(jour, datetime.min.time()))} :\n{detail}"

    async def find_events(entree: dict) -> str:
        recherche = entree["recherche"].strip()
        if not recherche:
            raise ValueError("Dis-moi quoi chercher dans l'agenda.")
        return format_with_refs(await agenda.search(recherche), tz)

    async def update_event(entree: dict) -> str:
        reference = entree["reference"].strip()
        titre = entree["titre"].strip() or None
        debut = dates.parse_local(entree["debut"], tz) if entree["debut"].strip() else None
        fin = dates.parse_local(entree["fin"], tz) if entree["fin"].strip() else None
        e = await agenda.update_event(reference, titre, debut, fin)
        quand = (
            f"le {dates.format_moment(e.debut, tz)} jusqu'à {dates.format_hour(e.fin.astimezone(ZoneInfo(tz)))}"
            if e.debut else "toute la journée"
        )
        return f"Modifié : {e.titre}, {quand}."

    async def delete_event(entree: dict) -> str:
        await agenda.delete_event(entree["reference"].strip())
        return "Événement supprimé de l'agenda."

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
    toolbox.add(DEFINITIONS["find_events"], find_events)
    toolbox.add(DEFINITIONS["update_event"], update_event)
    toolbox.add(DEFINITIONS["delete_event"], delete_event)
