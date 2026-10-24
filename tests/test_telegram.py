"""Partie Telegram : commandes, point du matin, traitement complet d'un message, coûts Twilio."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import calendar_google, llm, main, memory, reminders, twilio_costs, usage
from app.calendar_google import Evenement
from app.memory import Souvenir
from app.reminders import Rappel

PARIS = "Europe/Paris"


class Faux:
    """Update et contexte Telegram minimaux qui retiennent tout ce que Marcus envoie."""

    def __init__(self, texte="", args=(), **bot_data):
        self.reponses, self.envois, self.taches = [], [], []

        async def reply_text(t, **kwargs):
            self.reponses.append(t)

        async def send_message(chat_id, t, **kwargs):
            self.envois.append(t)

        async def send_chat_action(*args, **kwargs):
            pass

        self.update = SimpleNamespace(
            message=SimpleNamespace(text=texte, reply_text=reply_text),
            effective_chat=SimpleNamespace(id=1),
            effective_user=SimpleNamespace(id=42),
        )
        donnees = {"pool": None, "embed": None, "config": SimpleNamespace(
            allowed_user_id=42, timezone=PARIS, daily_budget_usd=10.0, model="claude-haiku-5-5")}
        donnees.update(bot_data)
        self.context = SimpleNamespace(
            args=list(args),
            bot=SimpleNamespace(send_message=send_message, send_chat_action=send_chat_action),
            bot_data=donnees,
            application=SimpleNamespace(create_task=lambda coro, update=None: self.taches.append(coro)),
        )

    def run(self, handler):
        asyncio.run(handler(self.update, self.context))
        for tache in self.taches:  # tâches de fond non lancées : on les ferme proprement
            tache.close()
        return self


# --- /souvenirs, /oublie, /corrige -----------------------------------------------------------

SOUVENIRS = [Souvenir(1, "profil", "Hugo habite à Aubagne."), Souvenir(2, "preference", "Hugo adore le piment.")]


@pytest.fixture
def souvenirs(monkeypatch):
    etat = SimpleNamespace(liste=list(SOUVENIRS), supprimes=[], corriges=[])

    async def all_memories(pool):
        return etat.liste

    async def find_memories(pool, query):
        return [s for s in etat.liste if query.lower() in s.content.lower() or query == str(s.id)]

    async def delete_memory(pool, memory_id):
        etat.supprimes.append(memory_id)

    async def update_memory(pool, embed, memory_id, content, type_=None):
        etat.corriges.append((memory_id, content))

    for nom, f in [("all_memories", all_memories), ("find_memories", find_memories),
                   ("delete_memory", delete_memory), ("update_memory", update_memory)]:
        monkeypatch.setattr(memory, nom, f)
    return etat


def test_souvenirs_listes(souvenirs):
    f = Faux().run(main.on_souvenirs)
    assert f.reponses[0].startswith("2 souvenir(s) :")
    assert "n°1 — Hugo habite à Aubagne." in f.reponses[0]


def test_aucun_souvenir(souvenirs):
    souvenirs.liste = []
    assert Faux().run(main.on_souvenirs).reponses == ["Je n'ai encore aucun souvenir."]


def test_oublie_sans_argument(souvenirs):
    assert "Dis-moi quoi oublier" in Faux().run(main.on_oublie).reponses[0]


def test_oublie_un_souvenir(souvenirs):
    f = Faux(args=["piment"]).run(main.on_oublie)
    assert souvenirs.supprimes == [2] and f.reponses == ["Oublié : Hugo adore le piment."]


def test_oublie_ambigu_demande_le_numero(souvenirs):
    f = Faux(args=["Hugo"]).run(main.on_oublie)
    assert souvenirs.supprimes == [] and "donne le numéro" in f.reponses[0]


def test_oublie_introuvable(souvenirs):
    assert "Aucun souvenir ne correspond" in Faux(args=["chat"]).run(main.on_oublie).reponses[0]


def test_corrige(souvenirs):
    f = Faux(args=["Aubagne", "->", "Hugo", "habite", "à", "Marseille."]).run(main.on_corrige)
    assert souvenirs.corriges == [(1, "Hugo habite à Marseille.")]
    assert f.reponses == ["Corrigé : Hugo habite à Marseille."]


def test_corrige_mal_forme(souvenirs):
    assert "Forme attendue" in Faux(args=["Aubagne"]).run(main.on_corrige).reponses[0]


def test_corrige_refuse_une_donnee_sensible(souvenirs):
    f = Faux(args=["1", "->", "Mon", "mot", "de", "passe", "est", "azerty"]).run(main.on_corrige)
    assert souvenirs.corriges == [] and "Je ne garde pas" in f.reponses[0]


# --- /couts et /rappels -------------------------------------------------------------------------


def test_couts_avec_une_part_estimee(monkeypatch):
    async def spent(pool):
        return 0.4321, 2.5

    async def estimated_today(pool):
        return 0.32

    monkeypatch.setattr(usage, "spent", spent)
    monkeypatch.setattr(usage, "estimated_today", estimated_today)
    texte = Faux().run(main.on_couts).reponses[0]
    assert texte.startswith("Aujourd'hui : 0,4321 $ (plafond 10,0000 $)\nCe mois-ci : 2,5000 $")
    assert "Dont 0,3200 $ de minutes d'appel encore estimées" in texte


def test_couts_sans_estimation(monkeypatch):
    async def spent(pool):
        return 0.01, 0.01

    async def estimated_today(pool):
        return 0.0

    monkeypatch.setattr(usage, "spent", spent)
    monkeypatch.setattr(usage, "estimated_today", estimated_today)
    assert "estimées" not in Faux().run(main.on_couts).reponses[0]


def test_rappels(monkeypatch):
    async def pending(pool):
        return [Rappel(5, "Garage", datetime(2026, 10, 26, 8, 0, tzinfo=timezone.utc))]

    monkeypatch.setattr(reminders, "pending", pending)
    texte = Faux().run(main.on_rappels).reponses[0]
    assert "n°5 — lundi 26 octobre 2026 à 9 h 00 — Garage" in texte and "annule le rappel" in texte


# --- Point du matin ---------------------------------------------------------------------------


@pytest.fixture
def sans_rappel(monkeypatch):
    async def pending(pool):
        return []

    monkeypatch.setattr(reminders, "pending", pending)


def test_point_du_matin_avec_l_agenda(sans_rappel):
    class Agenda:
        async def events_of_day(self, jour):
            return [Evenement("Dentiste", datetime(2026, 10, 29, 13, 0, tzinfo=timezone.utc),
                              datetime(2026, 10, 29, 14, 0, tzinfo=timezone.utc))]

    f = Faux(agenda=Agenda())
    asyncio.run(main.morning_brief(f.context))
    assert "☀️ Bonjour Hugo" in f.envois[0] and "Dentiste" in f.envois[0]


def test_point_du_matin_si_l_agenda_est_en_panne(sans_rappel):
    class Agenda:
        async def events_of_day(self, jour):
            raise calendar_google.AgendaError("expiré")

    f = Faux(agenda=Agenda())
    asyncio.run(main.morning_brief(f.context))
    assert "(agenda indisponible ce matin)" in f.envois[0]


# --- Traitement complet d'un message ------------------------------------------------------


@pytest.fixture
def conversation(monkeypatch):
    etat = SimpleNamespace(enregistres=[], appel_claude={})

    async def recent_messages(pool, n):
        return [{"role": "user", "content": "Salut"}, {"role": "assistant", "content": "Salut !"}]

    async def memories_for_prompt(pool, embed, texte):
        return [Souvenir(1, "profil", "Hugo habite à Aubagne.")]

    async def save_message(pool, role, content):
        etat.enregistres.append((role, content))
        return len(etat.enregistres)

    async def reply(client, model, system, history, user_text, now_text=None, on_response=None, toolbox=None, images=()):
        etat.appel_claude = dict(system=system, history=history, user_text=user_text, now_text=now_text)
        if etat.panne:
            raise RuntimeError("API en panne")
        return "Il fait beau à Aubagne."

    etat.panne = False
    monkeypatch.setattr(memory, "recent_messages", recent_messages)
    monkeypatch.setattr(memory, "memories_for_prompt", memories_for_prompt)
    monkeypatch.setattr(memory, "save_message", save_message)
    monkeypatch.setattr(llm, "reply", reply)
    return etat


def faux_message(texte):
    return Faux(texte, claude=None, system_prompt="CONSIGNES", toolbox=llm.Toolbox())


def test_un_message_passe_par_la_memoire_claude_et_la_base(conversation):
    f = faux_message("Quel temps fait-il ?").run(lambda u, c: main.answer(u, c, "Quel temps fait-il ?"))
    appel = conversation.appel_claude
    assert appel["user_text"] == "Quel temps fait-il ?"
    assert "Hugo habite à Aubagne." in appel["system"][1]["text"]  # mémoire dans le prompt
    assert "heure de Paris" in appel["now_text"]
    assert conversation.enregistres == [("user", "Quel temps fait-il ?"), ("assistant", "Il fait beau à Aubagne.")]
    assert f.reponses == ["Il fait beau à Aubagne."]
    assert len(f.taches) == 1  # apprentissage lancé en arrière-plan


def test_si_claude_est_en_panne(conversation):
    conversation.panne = True
    f = faux_message("Salut").run(lambda u, c: main.answer(u, c, "Salut"))
    assert f.reponses == [main.MESSAGE_ERREUR]
    assert conversation.enregistres == []  # rien en base sans réponse
    assert f.context.bot_data["echecs"] == 1


# --- Coûts Twilio réels ---------------------------------------------------------------------


def test_les_estimations_sont_remplacees_par_le_prix_reel(monkeypatch):
    regles = []

    async def pending_estimates(pool, service, jours=3):
        return [(10, "CA-connu"), (11, "CA-pas-encore")]

    async def settle(pool, usage_id, cout):
        regles.append((usage_id, cout))

    class Twilio:
        async def call_price(self, sid):
            return 0.0425 if sid == "CA-connu" else None

    monkeypatch.setattr(usage, "pending_estimates", pending_estimates)
    monkeypatch.setattr(usage, "settle", settle)
    assert asyncio.run(twilio_costs.reconcile(None, Twilio())) == 1
    assert regles == [(10, 0.0425)]  # l'autre reste estimé en attendant Twilio


def test_lecture_du_prix_twilio():
    import httpx

    def transport(request):
        assert request.url.path.endswith("/Calls/CA123.json")
        prix = None if "vide" in str(request.url) else "-0.01700"
        return httpx.Response(200, json={"price": prix})

    client = twilio_costs.TwilioClient("AC1", "jeton")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    assert asyncio.run(client.call_price("CA123")) == 0.017
