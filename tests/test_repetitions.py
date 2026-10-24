"""Rappels qui se répètent et modification / suppression de rendez-vous."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import calendar_google, reminders
from app.calendar_google import Evenement, GoogleCalendar
from app.llm import Toolbox
from app.reminders import Rappel

PARIS = "Europe/Paris"
UTC = timezone.utc


def paris(*args):
    return datetime(*args, tzinfo=ZoneInfo(PARIS))


# --- Prochaine occurrence ------------------------------------------------------------------


def prochain(debut, repetition, apres=None):
    resultat = reminders.next_occurrence(debut.astimezone(UTC), repetition, PARIS, apres or debut)
    return resultat.astimezone(ZoneInfo(PARIS))


def test_tous_les_jours():
    assert prochain(paris(2026, 10, 26, 8, 0), "quotidienne") == paris(2026, 10, 27, 8, 0)


def test_jours_ouvres_le_vendredi_passe_au_lundi():
    vendredi = paris(2026, 10, 30, 8, 0)
    assert prochain(vendredi, "jours_ouvres") == paris(2026, 11, 2, 8, 0)


def test_chaque_semaine():
    assert prochain(paris(2026, 10, 26, 8, 0), "hebdomadaire") == paris(2026, 11, 2, 8, 0)


def test_chaque_mois_le_31_devient_le_30():
    assert prochain(paris(2026, 10, 31, 9, 0), "mensuelle") == paris(2026, 11, 30, 9, 0)


def test_l_heure_reste_la_meme_au_changement_d_heure():
    # Nuit du 24 au 25 octobre 2026 : passage à l'heure d'hiver ; 8 h reste 8 h
    samedi = paris(2026, 10, 24, 8, 0)
    dimanche = prochain(samedi, "quotidienne")
    assert (dimanche.hour, dimanche.minute) == (8, 0)
    assert dimanche.utcoffset() == timedelta(hours=1) and samedi.utcoffset() == timedelta(hours=2)


def test_apres_une_absence_on_saute_les_occurrences_ratees():
    lundi_passe = paris(2026, 10, 19, 8, 0)
    assert prochain(lundi_passe, "quotidienne", apres=paris(2026, 10, 24, 12, 0)) == paris(2026, 10, 25, 8, 0)


# --- Envoi d'un rappel répété -------------------------------------------------------------


def test_un_rappel_repete_est_replanifie_au_lieu_d_etre_clos(monkeypatch):
    rappel = Rappel(3, "Sortir les poubelles", datetime.now(UTC), "hebdomadaire")
    deplacements, marques, planifies, envois = [], [], [], []

    async def get(pool, reminder_id):
        return rappel

    async def move(pool, reminder_id, due_at):
        deplacements.append(due_at)

    async def mark_sent(pool, reminder_id):
        marques.append(reminder_id)

    async def send_message(chat_id, texte):
        envois.append(texte)

    monkeypatch.setattr(reminders, "get", get)
    monkeypatch.setattr(reminders, "move", move)
    monkeypatch.setattr(reminders, "mark_sent", mark_sent)
    queue = SimpleNamespace(run_once=lambda cb, when, name, data: planifies.append((when, data)))
    context = SimpleNamespace(
        job=SimpleNamespace(data=3), job_queue=queue, bot=SimpleNamespace(send_message=send_message),
        bot_data={"pool": None, "config": SimpleNamespace(allowed_user_id=1, timezone=PARIS)},
    )
    asyncio.run(reminders.send_job(context))
    assert "Sortir les poubelles" in envois[0]
    assert marques == []  # pas clos
    assert len(deplacements) == 1
    assert timedelta(days=6) < deplacements[0] - rappel.due_at <= timedelta(days=7, hours=1)
    assert planifies == [(deplacements[0], 3)]


def test_creer_un_rappel_repete():
    base = {}

    async def add(pool, text, due_at, repetition="aucune"):
        base[1] = Rappel(1, text, due_at, repetition)
        return 1

    toolbox = Toolbox()
    reminders_add = reminders.add
    reminders.add = add
    try:
        queue = SimpleNamespace(run_once=lambda *a, **k: None)
        reminders.add_tools(toolbox, None, queue, PARIS)
        lundi = (datetime.now(ZoneInfo(PARIS)) + timedelta(days=7)).strftime("%Y-%m-%dT08:00")
        texte = asyncio.run(toolbox.handlers["create_reminder"](
            {"texte": "Sortir les poubelles", "quand": lundi, "repetition": "hebdomadaire"}
        ))
    finally:
        reminders.add = reminders_add
    assert base[1].repetition == "hebdomadaire"
    assert ", puis chaque semaine" in texte
    assert toolbox.definitions[0]["input_schema"]["properties"]["repetition"]["enum"] == list(reminders.REPETITIONS)


def test_la_liste_montre_la_repetition():
    texte = reminders.format_list([Rappel(2, "Poubelles", datetime(2026, 10, 26, 7, 0, tzinfo=UTC), "hebdomadaire")], PARIS)
    assert texte == "n°2 — prochain : lundi 26 octobre 2026 à 8 h 00 — Poubelles (🔁 chaque semaine)"


# --- Agenda : chercher, modifier, supprimer ----------------------------------------------


class FauxAgenda:
    tz = PARIS

    def __init__(self):
        self.modifs, self.suppressions = [], []

    async def search(self, texte):
        return [Evenement("Dentiste", paris(2026, 10, 29, 14, 0), paris(2026, 10, 29, 15, 0), "abc123")]

    async def update_event(self, ref, titre, debut, fin):
        self.modifs.append((ref, titre, debut, fin))
        return Evenement(titre or "Dentiste", debut or paris(2026, 10, 29, 14, 0),
                         fin or (debut + timedelta(hours=1) if debut else paris(2026, 10, 29, 15, 0)), ref)

    async def delete_event(self, ref):
        self.suppressions.append(ref)


@pytest.fixture
def agenda():
    toolbox, faux = Toolbox(), FauxAgenda()
    calendar_google.add_tools(toolbox, faux)
    return toolbox, faux


def test_retrouver_un_rendez_vous(agenda):
    toolbox, _ = agenda
    texte = asyncio.run(toolbox.handlers["find_events"]({"recherche": "dentiste"}))
    assert texte == "- jeudi 29 octobre 2026, 14 h 00 – 15 h 00 : Dentiste (réf. abc123)"


def test_decaler_un_rendez_vous(agenda):
    toolbox, faux = agenda
    texte = asyncio.run(toolbox.handlers["update_event"](
        {"reference": "abc123", "titre": "", "debut": "2026-10-29T16:00", "fin": ""}
    ))
    ref, titre, debut, fin = faux.modifs[0]
    assert ref == "abc123" and titre is None and fin is None
    assert debut == paris(2026, 10, 29, 16, 0)
    assert texte == "Modifié : Dentiste, le jeudi 29 octobre 2026 à 16 h 00 jusqu'à 17 h 00."


def test_annuler_un_rendez_vous(agenda):
    toolbox, faux = agenda
    assert asyncio.run(toolbox.handlers["delete_event"]({"reference": "abc123"})) == "Événement supprimé de l'agenda."
    assert faux.suppressions == ["abc123"]


def google_avec(reponses):
    """GoogleCalendar dont les requêtes renvoient les réponses prévues (et retiennent ce qui part)."""
    g = GoogleCalendar("id", "secret", "refresh", PARIS)
    envoyes = []

    async def request(method, event_id="", **kwargs):
        envoyes.append((method, event_id, kwargs.get("json")))
        return reponses.pop(0)

    g._request = request
    return g, envoyes


EVENEMENT_GOOGLE = {
    "id": "abc123", "summary": "Dentiste",
    "start": {"dateTime": "2026-10-29T14:00:00+01:00"}, "end": {"dateTime": "2026-10-29T15:30:00+01:00"},
}


def test_decaler_garde_la_duree():
    nouveau = {**EVENEMENT_GOOGLE, "start": {"dateTime": "2026-10-29T16:00:00+01:00"},
               "end": {"dateTime": "2026-10-29T17:30:00+01:00"}}
    g, envoyes = google_avec([EVENEMENT_GOOGLE, nouveau])
    asyncio.run(g.update_event("abc123", debut=paris(2026, 10, 29, 16, 0)))
    methode, ref, corps = envoyes[1]
    assert (methode, ref) == ("PATCH", "abc123")
    assert corps["start"]["dateTime"].startswith("2026-10-29T16:00")
    assert corps["end"]["dateTime"].startswith("2026-10-29T17:30")  # 1 h 30 conservée


def test_rien_a_changer_refuse():
    g, _ = google_avec([EVENEMENT_GOOGLE])
    with pytest.raises(calendar_google.AgendaError, match="Rien à changer"):
        asyncio.run(g.update_event("abc123"))
