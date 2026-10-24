"""Rappels : table reminders (heures en UTC), planifiés dans la JobQueue, rechargés au démarrage.

Un rappel peut se répéter (tous les jours, jours ouvrés, chaque semaine, chaque mois) : après
chaque envoi, sa prochaine date est calculée à la même heure de Paris, changement d'heure compris.
"""

import calendar
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from telegram.ext import ContextTypes, JobQueue

from app import dates
from app.llm import Toolbox

logger = logging.getLogger(__name__)

# Au-delà de ce retard, le rappel est signalé « en retard » (Marcus était éteint à l'heure dite)
TOLERANCE_RETARD = timedelta(minutes=2)

REPETITIONS = {
    "aucune": "",
    "quotidienne": "tous les jours",
    "jours_ouvres": "du lundi au vendredi",
    "hebdomadaire": "chaque semaine",
    "mensuelle": "chaque mois",
}


@dataclass(frozen=True)
class Rappel:
    id: int
    text: str
    due_at: datetime  # avec fuseau (UTC en base)
    repetition: str = "aucune"


# --- Prochaine occurrence d'un rappel qui se répète -------------------------------


def _mois_suivant(local: datetime) -> datetime:
    """Même jour le mois suivant ; le 31 devient le dernier jour d'un mois plus court."""
    annee, mois = (local.year + 1, 1) if local.month == 12 else (local.year, local.month + 1)
    jour = min(local.day, calendar.monthrange(annee, mois)[1])
    return local.replace(year=annee, month=mois, day=jour)


def _suivant(local: datetime, repetition: str) -> datetime:
    if repetition == "quotidienne":
        return local + timedelta(days=1)
    if repetition == "jours_ouvres":
        suivant = local + timedelta(days=1)
        while suivant.weekday() >= 5:  # samedi, dimanche
            suivant += timedelta(days=1)
        return suivant
    if repetition == "hebdomadaire":
        return local + timedelta(days=7)
    if repetition == "mensuelle":
        return _mois_suivant(local)
    raise ValueError(f"Répétition inconnue : {repetition}")


def next_occurrence(due_at: datetime, repetition: str, tz: str, apres: datetime) -> datetime:
    """Prochaine date strictement après `apres`, à la même heure locale (calcul en heure de Paris :
    un rappel de 8 h reste à 8 h après le passage à l'heure d'été ou d'hiver)."""
    fuseau = ZoneInfo(tz)
    # Heure « murale » sans fuseau, puis fuseau réappliqué : l'heure locale ne bouge pas
    local = due_at.astimezone(fuseau).replace(tzinfo=None)
    while True:
        local = _suivant(local, repetition)
        candidat = local.replace(tzinfo=fuseau)
        if candidat > apres:
            return candidat.astimezone(timezone.utc)


# --- Base ---------------------------------------------------------------------

COLONNES = "id, text, due_at, repetition"


async def add(pool, text: str, due_at: datetime, repetition: str = "aucune") -> int:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "insert into reminders (text, due_at, repetition) values (%s, %s, %s) returning id",
            (text, due_at, repetition),
        )
        return (await cur.fetchone())[0]


async def pending(pool) -> list[Rappel]:
    async with pool.connection() as conn:
        cur = await conn.execute(f"select {COLONNES} from reminders where not sent order by due_at")
        return [Rappel(*row) for row in await cur.fetchall()]


async def get(pool, reminder_id: int) -> Rappel | None:
    async with pool.connection() as conn:
        cur = await conn.execute(f"select {COLONNES} from reminders where id = %s and not sent", (reminder_id,))
        row = await cur.fetchone()
    return Rappel(*row) if row else None


async def mark_sent(pool, reminder_id: int) -> None:
    async with pool.connection() as conn:
        await conn.execute("update reminders set sent = true where id = %s", (reminder_id,))


async def move(pool, reminder_id: int, due_at: datetime) -> None:
    async with pool.connection() as conn:
        await conn.execute("update reminders set due_at = %s where id = %s", (due_at, reminder_id))


async def delete(pool, reminder_id: int) -> bool:
    async with pool.connection() as conn:
        cur = await conn.execute(
            "delete from reminders where id = %s and not sent returning id", (reminder_id,)
        )
        return await cur.fetchone() is not None


# --- Messages -------------------------------------------------------------------


def format_reminder(text: str, late: bool = False) -> str:
    """Le plan demande au moins 5 emojis bien visibles."""
    fin = "⌛ En retard : je n'étais pas allumé à l'heure prévue." if late else "✅ C'est l'heure !"
    return f"⏰🔔 RAPPEL 🔔⏰\n\n👉 {text}\n\n{fin}"


def format_list(rappels: list[Rappel], tz: str) -> str:
    if not rappels:
        return "Aucun rappel en attente."
    lignes = []
    for r in rappels:
        rythme = f" (🔁 {REPETITIONS[r.repetition]})" if REPETITIONS.get(r.repetition) else ""
        prochain = "prochain : " if rythme else ""
        lignes.append(f"n°{r.id} — {prochain}{dates.format_moment(r.due_at, tz)} — {r.text}{rythme}")
    return "\n".join(lignes)


# --- Planification --------------------------------------------------------------

def job_name(reminder_id: int) -> str:
    return f"rappel-{reminder_id}"


def schedule(job_queue: JobQueue, rappel: Rappel) -> None:
    """Un rappel dont l'heure est passée part tout de suite."""
    quand = max(rappel.due_at, datetime.now(timezone.utc))
    job_queue.run_once(send_job, when=quand, name=job_name(rappel.id), data=rappel.id)


def unschedule(job_queue: JobQueue, reminder_id: int) -> None:
    for job in job_queue.get_jobs_by_name(job_name(reminder_id)):
        job.schedule_removal()


async def send_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    data = context.bot_data
    rappel = await get(data["pool"], context.job.data)
    if rappel is None:  # annulé entre-temps
        return
    maintenant = datetime.now(timezone.utc)
    late = maintenant - rappel.due_at > TOLERANCE_RETARD
    await context.bot.send_message(data["config"].allowed_user_id, format_reminder(rappel.text, late))
    if rappel.repetition == "aucune":
        await mark_sent(data["pool"], rappel.id)
    else:
        # Même après une absence, une seule fois en retard, puis on repart sur la prochaine date
        suivant = next_occurrence(rappel.due_at, rappel.repetition, data["config"].timezone, maintenant)
        await move(data["pool"], rappel.id, suivant)
        schedule(context.job_queue, Rappel(rappel.id, rappel.text, suivant, rappel.repetition))
    logger.info("Rappel n°%s envoyé", rappel.id)


async def reload_all(pool, job_queue: JobQueue) -> int:
    """Au démarrage : replanifie tous les rappels non envoyés."""
    rappels = await pending(pool)
    for rappel in rappels:
        schedule(job_queue, rappel)
    return len(rappels)


# --- Outils pour Claude -----------------------------------------------------------

DEFINITIONS = {
    "create_reminder": {
        "name": "create_reminder",
        "description": (
            "Programme un rappel : Marcus enverra le texte à Hugo sur Telegram à la date et l'heure "
            "données, une fois ou de façon répétée. Calcule les dates relatives (« demain », « dans 2 h ») "
            "à partir de la date et de l'heure indiquées en tête du message d'Hugo. Pour un rappel "
            "répété, « quand » est la première occurrence (par exemple le prochain lundi à 8 h)."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "texte": {"type": "string", "description": "Ce qu'il faut rappeler, court et clair."},
                "quand": {
                    "type": "string",
                    "description": "Date et heure de Paris au format AAAA-MM-JJTHH:MM, par exemple 2026-10-24T09:00.",
                },
                "repetition": {
                    "type": "string",
                    "enum": list(REPETITIONS),
                    "description": "aucune, quotidienne (tous les jours), jours_ouvres (lundi à vendredi), "
                    "hebdomadaire (même jour chaque semaine) ou mensuelle (même date chaque mois).",
                },
            },
            "required": ["texte", "quand", "repetition"],
            "additionalProperties": False,
        },
    },
    "list_reminders": {
        "name": "list_reminders",
        "description": "Liste les rappels en attente, avec leur numéro, leur date, leur texte et leur répétition.",
        "strict": True,
        "input_schema": {"type": "object", "properties": {}, "required": [], "additionalProperties": False},
    },
    "cancel_reminder": {
        "name": "cancel_reminder",
        "description": "Annule un rappel en attente (et toutes ses répétitions) à partir de son numéro (voir list_reminders).",
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {"numero": {"type": "integer"}},
            "required": ["numero"],
            "additionalProperties": False,
        },
    },
}


def add_tools(toolbox: Toolbox, pool, job_queue: JobQueue, tz: str) -> None:
    async def create_reminder(entree: dict) -> str:
        texte = entree["texte"].strip()
        if not texte:
            raise ValueError("Le texte du rappel est vide.")
        repetition = entree.get("repetition") or "aucune"
        if repetition not in REPETITIONS:
            raise ValueError(f"Répétition inconnue : {repetition}.")
        quand = dates.parse_local(entree["quand"], tz)
        if quand < datetime.now(timezone.utc) - timedelta(minutes=1):
            raise ValueError(
                f"Cette date est déjà passée ({dates.format_moment(quand, tz)}). Vérifie le jour et l'heure."
            )
        rappel = Rappel(await add(pool, texte, quand, repetition), texte, quand, repetition)
        schedule(job_queue, rappel)
        rythme = f", puis {REPETITIONS[repetition]}" if REPETITIONS[repetition] else ""
        return f"Rappel n°{rappel.id} programmé pour le {dates.format_moment(quand, tz)}{rythme} : {texte}"

    async def list_reminders(_entree: dict) -> str:
        return format_list(await pending(pool), tz)

    async def cancel_reminder(entree: dict) -> str:
        numero = entree["numero"]
        if not await delete(pool, numero):
            raise ValueError(f"Aucun rappel en attente n°{numero}.")
        unschedule(job_queue, numero)
        return f"Rappel n°{numero} annulé."

    toolbox.add(DEFINITIONS["create_reminder"], create_reminder)
    toolbox.add(DEFINITIONS["list_reminders"], list_reminders)
    toolbox.add(DEFINITIONS["cancel_reminder"], cancel_reminder)
