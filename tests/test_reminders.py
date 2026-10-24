"""Rappels : dates, outils, planification, rechargement au démarrage, point du matin."""

import asyncio
import re
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest

from app import brief, dates, reminders
from app.llm import Toolbox
from app.reminders import Rappel

PARIS = "Europe/Paris"
UTC = timezone.utc


# --- Dates --------------------------------------------------------------------------


def test_heure_de_paris_en_hiver():
    dt = dates.parse_local("2026-12-01T09:00", PARIS)
    assert dt.astimezone(UTC) == datetime(2026, 12, 1, 8, 0, tzinfo=UTC)


def test_heure_de_paris_en_ete():
    dt = dates.parse_local("2026-07-01T09:00", PARIS)
    assert dt.astimezone(UTC) == datetime(2026, 7, 1, 7, 0, tzinfo=UTC)


def test_fuseau_explicite_conserve():
    dt = dates.parse_local("2026-12-01T09:00+00:00", PARIS)
    assert dt == datetime(2026, 12, 1, 9, 0, tzinfo=UTC)


def test_date_illisible():
    with pytest.raises(ValueError, match="illisible"):
        dates.parse_local("demain 9h", PARIS)


def test_affichage_en_heure_de_paris():
    assert dates.format_moment(datetime(2026, 10, 24, 7, 0, tzinfo=UTC), PARIS) == "samedi 24 octobre 2026 à 9 h 00"


# --- Message de rappel ------------------------------------------------------------------

EMOJI = re.compile("[\U0001F300-\U0001FAFF☀-➿⌚-⌛⏩-⏺]")


@pytest.mark.parametrize("late", [False, True])
def test_rappel_avec_au_moins_5_emojis(late):
    texte = reminders.format_reminder("Appeler le garage", late)
    assert "Appeler le garage" in texte
    assert len(EMOJI.findall(texte)) >= 5


def test_rappel_en_retard_le_dit():
    assert "retard" in reminders.format_reminder("x", late=True)


# --- Outils avec une fausse base et une fausse JobQueue -------------------------------


class FausseJobQueue:
    def __init__(self):
        self.jobs = []

    def run_once(self, callback, when, name, data):
        self.jobs.append(SimpleNamespace(callback=callback, when=when, name=name, data=data, retire=False))

    def get_jobs_by_name(self, name):
        jobs = [j for j in self.jobs if j.name == name]
        for j in jobs:
            j.schedule_removal = lambda j=j: setattr(j, "retire", True)
        return jobs


@pytest.fixture
def outils(monkeypatch):
    base = {}

    async def add(pool, text, due_at, repetition="aucune"):
        base[len(base) + 1] = Rappel(len(base) + 1, text, due_at, repetition)
        return len(base)

    async def delete(pool, reminder_id):
        return base.pop(reminder_id, None) is not None

    async def pending(pool):
        return sorted(base.values(), key=lambda r: r.due_at)

    monkeypatch.setattr(reminders, "add", add)
    monkeypatch.setattr(reminders, "delete", delete)
    monkeypatch.setattr(reminders, "pending", pending)
    toolbox, queue = Toolbox(), FausseJobQueue()
    reminders.add_tools(toolbox, None, queue, PARIS)
    return SimpleNamespace(toolbox=toolbox, queue=queue, base=base)


def appeler(outils, nom, entree):
    return asyncio.run(outils.toolbox.handlers[nom](entree))


def demain_9h():
    demain = datetime.now(ZoneInfo(PARIS)) + timedelta(days=1)
    return demain.strftime("%Y-%m-%dT09:00")


def test_les_trois_outils_sont_declares(outils):
    noms = [d["name"] for d in outils.toolbox.definitions]
    assert noms == ["create_reminder", "list_reminders", "cancel_reminder"]
    assert all(d["strict"] for d in outils.toolbox.definitions)


def test_creer_un_rappel_le_planifie(outils):
    resultat = appeler(outils, "create_reminder", {"texte": "Appeler le garage", "quand": demain_9h()})
    assert resultat.startswith("Rappel n°1 programmé pour le ")
    assert "à 9 h 00" in resultat
    job = outils.queue.jobs[0]
    assert job.name == "rappel-1" and job.data == 1
    assert job.when.astimezone(ZoneInfo(PARIS)).hour == 9


def test_date_passee_refusee(outils):
    with pytest.raises(ValueError, match="déjà passée"):
        appeler(outils, "create_reminder", {"texte": "x", "quand": "2020-01-01T09:00"})
    assert outils.base == {}


def test_lister_puis_annuler(outils):
    appeler(outils, "create_reminder", {"texte": "Appeler le garage", "quand": demain_9h()})
    assert "n°1" in appeler(outils, "list_reminders", {})
    assert appeler(outils, "cancel_reminder", {"numero": 1}) == "Rappel n°1 annulé."
    assert outils.queue.jobs[0].retire
    assert appeler(outils, "list_reminders", {}) == "Aucun rappel en attente."


def test_annuler_un_numero_inconnu(outils):
    with pytest.raises(ValueError, match="Aucun rappel"):
        appeler(outils, "cancel_reminder", {"numero": 42})


# --- Rechargement après redémarrage ---------------------------------------------------


def test_redemarrage_replanifie_tout_et_envoie_les_rappels_rates(monkeypatch):
    maintenant = datetime.now(UTC)
    rate = Rappel(1, "Rappel raté pendant l'arrêt", maintenant - timedelta(hours=1))
    futur = Rappel(2, "Appeler le garage", maintenant + timedelta(hours=10))

    async def pending(pool):
        return [rate, futur]

    monkeypatch.setattr(reminders, "pending", pending)
    queue = FausseJobQueue()
    assert asyncio.run(reminders.reload_all(None, queue)) == 2
    quand = {j.data: j.when for j in queue.jobs}
    assert quand[1] >= maintenant  # le rappel raté part tout de suite
    assert quand[2] == futur.due_at


def test_envoi_marque_le_rappel_comme_envoye(monkeypatch):
    envoyes, marques = [], []
    rappel = Rappel(7, "Appeler le garage", datetime.now(UTC))

    async def get(pool, reminder_id):
        return rappel

    async def mark_sent(pool, reminder_id):
        marques.append(reminder_id)

    async def send_message(chat_id, texte):
        envoyes.append(texte)

    monkeypatch.setattr(reminders, "get", get)
    monkeypatch.setattr(reminders, "mark_sent", mark_sent)
    context = SimpleNamespace(
        job=SimpleNamespace(data=7),
        bot=SimpleNamespace(send_message=send_message),
        bot_data={"pool": None, "config": SimpleNamespace(allowed_user_id=1)},
    )
    asyncio.run(reminders.send_job(context))
    assert "Appeler le garage" in envoyes[0] and "retard" not in envoyes[0]
    assert marques == [7]


def test_rappel_annule_entre_temps_n_est_pas_envoye(monkeypatch):
    async def get(pool, reminder_id):
        return None

    async def interdit(*args):
        raise AssertionError("ne doit pas être envoyé")

    monkeypatch.setattr(reminders, "get", get)
    context = SimpleNamespace(
        job=SimpleNamespace(data=7),
        bot=SimpleNamespace(send_message=interdit),
        bot_data={"pool": None, "config": SimpleNamespace(allowed_user_id=1)},
    )
    asyncio.run(reminders.send_job(context))


# --- Point du matin ----------------------------------------------------------------------


def test_point_du_matin_ne_garde_que_les_rappels_du_jour():
    matin = datetime(2026, 10, 24, 7, 30, tzinfo=ZoneInfo(PARIS))
    rappels = [
        Rappel(1, "Garage", datetime(2026, 10, 24, 7, 0, tzinfo=UTC)),  # 9 h à Paris
        Rappel(2, "Dentiste", datetime(2026, 10, 25, 12, 0, tzinfo=UTC)),
        Rappel(3, "Tard le soir", datetime(2026, 10, 24, 21, 30, tzinfo=UTC)),  # 23 h 30 à Paris
    ]
    assert [r.id for r in brief.todays(rappels, matin, PARIS)] == [1, 3]


def test_point_du_matin_texte():
    matin = datetime(2026, 10, 24, 7, 30, tzinfo=ZoneInfo(PARIS))
    texte = brief.format_brief(matin, [Rappel(1, "Garage", datetime(2026, 10, 24, 7, 0, tzinfo=UTC))], PARIS)
    assert "samedi 24 octobre 2026" in texte
    assert "- 9 h 00 : Garage" in texte


def test_point_du_matin_sans_rappel():
    matin = datetime(2026, 10, 24, 7, 30, tzinfo=ZoneInfo(PARIS))
    assert "Aucun rappel" in brief.format_brief(matin, [], PARIS)
