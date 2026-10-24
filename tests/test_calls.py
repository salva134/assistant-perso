"""Appels : numéros, assistant vocal, validation par bouton, suivi et compte rendu."""

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app import calls, calls_bot, memory, usage
from app.calls import Appel
from app.llm import Toolbox


def appel(**kw) -> Appel:
    base = dict(
        id=12, numero="+33612345678", destinataire="Maman", objectif="Prévenir qu'Hugo mange au resto ce soir",
        ouverture="J'appelle de sa part pour vous dire qu'il mange au restaurant ce soir.",
        details="", status="en_attente", created_at=datetime.now(timezone.utc), vapi_id=None,
    )
    return Appel(**{**base, **kw})


# --- Numéros ------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "texte, attendu",
    [
        ("06 12 34 56 78", "+33612345678"),
        ("0612345678", "+33612345678"),
        ("06.12.34.56.78", "+33612345678"),
        ("+33 6 12 34 56 78", "+33612345678"),
        ("0033612345678", "+33612345678"),
        ("04 91 00 00 00", "+33491000000"),
        ("+1 775 637 1413", "+17756371413"),
    ],
)
def test_numeros_au_format_international(texte, attendu):
    assert calls.normalize_number(texte) == attendu


@pytest.mark.parametrize("texte", ["", "maman", "06 43", "12"])
def test_numero_illisible(texte):
    with pytest.raises(ValueError, match="illisible"):
        calls.normalize_number(texte)


def test_affichage_d_un_numero_francais():
    assert calls.format_number("+33612345678") == "06 12 34 56 78"
    assert calls.format_number("+17756371413") == "+17756371413"


def test_cout_estime_mobile_et_fixe():
    assert calls.estimated_twilio_cost("+33612345678", 61) == pytest.approx(0.32)  # 2 minutes entamées
    assert calls.estimated_twilio_cost("+33491000000", 30) == pytest.approx(0.05)


# --- Assistant vocal ---------------------------------------------------------------------


def test_assistant_vocal_en_francais():
    a = calls.build_assistant(appel(details="Hugo rentre vers 23 h"))
    assert a["firstMessage"] == (
        "Bonjour, c'est Marcus, l'assistant IA d'Hugo. "
        "J'appelle de sa part pour vous dire qu'il mange au restaurant ce soir."
    )
    consignes = a["model"]["messages"][0]["content"]
    assert "Maman" in consignes and "mange au resto" in consignes and "23 h" in consignes
    assert "sauce pimentée" in consignes  # les petites questions ont une réponse de bon sens
    assert a["voice"]["provider"] == "cartesia"
    assert a["voice"]["voiceId"] in calls.VOIX_FRANCAISES.values()
    assert a["transcriber"]["language"] == "fr"
    assert a["transcriber"]["endpointing"] >= 300  # ne coupe pas au premier silence
    assert a["startSpeakingPlan"]["transcriptionEndpointingPlan"]["onNumberSeconds"] >= 1.0  # adresses, numéros
    assert a["stopSpeakingPlan"]["numWords"] == 0  # s'arrête dès qu'on le coupe
    assert a["artifactPlan"] == {"recordingEnabled": False}
    assert a["maxDurationSeconds"] == calls.DUREE_MAX_APPEL_SECONDES


def test_la_memoire_d_hugo_accompagne_l_appel():
    memoire = "Ce que tu sais d'Hugo :\n- Hugo habite 12 rue des Lilas à Aubagne.\n- Il adore le piment."
    consignes = calls.build_assistant(appel(), memoire)["model"]["messages"][0]["content"]
    assert "12 rue des Lilas" in consignes
    assert "Prends les devants" in consignes


def test_recapitulatif_pour_hugo():
    texte = calls.format_recap(appel(details="2 personnes"))
    assert "Maman" in texte and "06 12 34 56 78" in texte
    assert "« Bonjour, c'est Marcus" in texte
    assert "2 personnes" in texte


def test_compte_rendu():
    texte = calls.format_report(appel(), 72, "C'est transmis.", "AI: Bonjour\nUser: Oui")
    assert texte.startswith("📞 Appel terminé — Maman (06 12 34 56 78)")
    assert "1 min 12 s" in texte and "C'est transmis." in texte and "User: Oui" in texte


def test_duree_et_transcription_lues_dans_vapi():
    etat = {
        "startedAt": "2026-10-24T08:09:25.000Z",
        "endedAt": "2026-10-24T08:10:17.000Z",
        "artifact": {"transcript": " AI: Bonjour \n"},
    }
    assert calls.call_duration(etat) == 52
    assert calls.call_transcript(etat) == "AI: Bonjour"
    assert calls.call_duration({}) == 0


def test_fins_d_appel_expliquees():
    assert calls.explain_end("customer-did-not-answer") == "personne n'a décroché"
    assert "échoué" in calls.explain_end("call.start.error-get-transport")
    assert calls.explain_end("assistant-ended-call") is None


# --- Outil prepare_call ---------------------------------------------------------------------


@pytest.fixture
def outil(monkeypatch):
    prepares, crees = [], []

    async def count_today(pool):
        return outil.deja

    async def create_pending(pool, numero, destinataire, objectif, ouverture, details):
        a = appel(numero=numero, destinataire=destinataire, objectif=objectif, ouverture=ouverture, details=details)
        crees.append(a)
        return a

    async def on_prepared(a):
        prepares.append(a)

    monkeypatch.setattr(calls, "count_today", count_today)
    monkeypatch.setattr(calls, "create_pending", create_pending)
    toolbox = Toolbox()
    calls.add_tools(toolbox, None, on_prepared)
    outil = SimpleNamespace(toolbox=toolbox, prepares=prepares, crees=crees, deja=0)
    return outil


ENTREE = {
    "numero": "06 12 34 56 78", "destinataire": "Maman", "objectif": "Prévenir pour le resto",
    "ouverture": "J'appelle pour vous dire qu'il mange au restaurant.", "details": "",
}


def test_preparer_un_appel_envoie_le_recapitulatif_sans_appeler(outil):
    texte = asyncio.run(outil.toolbox.handlers["prepare_call"](ENTREE))
    assert outil.crees[0].numero == "+33612345678"
    assert outil.prepares == outil.crees  # boutons envoyés à Hugo
    assert "ne dis pas qu'il est lancé" in texte
    assert outil.toolbox.definitions[0]["strict"] is True


def test_limite_d_appels_par_jour(outil):
    outil.deja = calls.APPELS_MAX_PAR_JOUR
    with pytest.raises(ValueError, match="Limite"):
        asyncio.run(outil.toolbox.handlers["prepare_call"](ENTREE))
    assert outil.crees == []


def test_numero_faux_refuse(outil):
    with pytest.raises(ValueError, match="illisible"):
        asyncio.run(outil.toolbox.handlers["prepare_call"]({**ENTREE, "numero": "maman"}))


# --- Boutons ------------------------------------------------------------------------------


def test_lecture_des_boutons():
    assert calls_bot.parse_callback("appel:oui:12") == ("oui", 12)
    assert calls_bot.parse_callback("appel:non:3") == ("non", 3)
    assert calls_bot.parse_callback("appel:peut-etre:3") is None
    assert calls_bot.parse_callback("autre") is None


class FausseQuery:
    def __init__(self, data):
        self.data, self.reponses, self.textes = data, [], []

    async def answer(self, texte=None, show_alert=False):
        self.reponses.append(texte)

    async def edit_message_text(self, texte):
        self.textes.append(texte)

    async def edit_message_reply_markup(self, markup):
        pass


@pytest.fixture
def bouton(monkeypatch):
    etat = SimpleNamespace(appel=appel(), statuts=[], suivis=[], lances=[], refus=None, deja_pris=False)

    async def get(pool, call_id):
        return etat.appel

    async def claim(pool, call_id):
        return not etat.deja_pris

    async def set_status(pool, call_id, status, vapi_id=None):
        etat.statuts.append((status, vapi_id))

    async def refusal_reason(context):
        return etat.refus

    async def start(a, memoire="", outils=None):
        etat.lances.append(a)
        etat.memoire = memoire
        return "vapi-123"

    async def all_memories(pool):
        return [memory.Souvenir(1, "profil", "Hugo habite à Aubagne.")]

    monkeypatch.setattr(memory, "all_memories", all_memories)
    monkeypatch.setattr(calls, "get", get)
    monkeypatch.setattr(calls, "claim", claim)
    monkeypatch.setattr(calls, "set_status", set_status)
    monkeypatch.setattr(calls_bot, "refusal_reason", refusal_reason)
    monkeypatch.setattr(calls_bot, "follow", lambda job_queue, call_id: etat.suivis.append(call_id))
    etat.context = SimpleNamespace(bot_data={"pool": None, "vapi": SimpleNamespace(start=start)}, job_queue=None)
    return etat


def cliquer(etat, data):
    query = FausseQuery(data)
    asyncio.run(calls_bot.on_button(SimpleNamespace(callback_query=query), etat.context))
    return query


def test_bouton_appeler_lance_et_suit_l_appel(bouton):
    query = cliquer(bouton, "appel:oui:12")
    assert bouton.lances == [bouton.appel]
    assert bouton.statuts == [("en_cours", "vapi-123")]
    assert bouton.suivis == [12]
    assert "Appel en cours" in query.textes[0]
    assert "Hugo habite à Aubagne." in bouton.memoire  # sa mémoire part avec l'appel


def test_bouton_annuler_n_appelle_pas(bouton):
    query = cliquer(bouton, "appel:non:12")
    assert bouton.lances == [] and bouton.statuts == [("annule", None)]
    assert "Annulé" in query.textes[0]


def test_recapitulatif_expire(bouton):
    bouton.appel = appel(created_at=datetime.now(timezone.utc) - timedelta(minutes=11))
    query = cliquer(bouton, "appel:oui:12")
    assert bouton.lances == []
    assert "expiré" in query.textes[0]


def test_appel_deja_traite_n_est_pas_relance(bouton):
    bouton.appel = appel(status="termine")
    query = cliquer(bouton, "appel:oui:12")
    assert bouton.lances == [] and "déjà" in query.reponses[0]


def test_double_clic_ne_lance_qu_un_appel(bouton):
    # Le 2e clic arrive alors que le 1er a déjà pris l'appel en base
    bouton.deja_pris = True
    query = cliquer(bouton, "appel:oui:12")
    assert bouton.lances == []
    assert "déjà" in query.reponses[0]


def test_suivi_compte_depuis_le_lancement_chez_vapi():
    # Récapitulatif créé il y a 9 min, appel lancé il y a 2 min : on doit encore le suivre
    a = appel(created_at=datetime.now(timezone.utc) - timedelta(minutes=9))
    lancement = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat().replace("+00:00", "Z")
    depuis = datetime.now(timezone.utc) - calls_bot.launch_time(a, {"createdAt": lancement})
    assert depuis < timedelta(minutes=3)
    assert calls_bot.SUIVI_MAX > timedelta(seconds=calls.DUREE_MAX_APPEL_SECONDES)


def test_refus_si_un_appel_est_deja_en_cours(bouton):
    bouton.refus = "Un appel est déjà en cours : attends son compte rendu."
    query = cliquer(bouton, "appel:oui:12")
    assert bouton.lances == [] and bouton.statuts == []
    assert "déjà en cours" in query.reponses[0]


# --- Compte rendu --------------------------------------------------------------------------


@pytest.fixture
def compte_rendu(monkeypatch):
    etat = SimpleNamespace(fini=[], couts=[], envois=[], conversation=[])

    async def save_message(pool, role, content):
        etat.conversation.append((role, content))
        return 1

    monkeypatch.setattr(memory, "save_message", save_message)

    async def finish(pool, call_id, status, raison, resume, transcript, duree):
        etat.fini.append((status, raison, resume, duree))

    async def record(pool, service, model, cost_usd=None, **kw):
        etat.couts.append((service, cost_usd))

    async def summarize(claude, model, a, transcript, on_response=None):
        return "Message transmis, elle te souhaite bon appétit."

    async def send_message(chat_id, texte):
        etat.envois.append(texte)

    monkeypatch.setattr(calls, "finish", finish)
    monkeypatch.setattr(calls, "summarize", summarize)
    monkeypatch.setattr(usage, "record", record)
    etat.context = SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
        bot_data={"pool": None, "claude": None, "config": SimpleNamespace(model="m", allowed_user_id=1)},
    )
    return etat


def test_compte_rendu_apres_une_conversation(compte_rendu):
    etat_vapi = {
        "endedReason": "assistant-ended-call", "cost": 0.054,
        "startedAt": "2026-10-24T08:09:25Z", "endedAt": "2026-10-24T08:10:17Z",
        "artifact": {"transcript": "AI: Bonjour, c'est Marcus…\nUser: Ah d'accord, merci, bon appétit à lui !"},
    }
    asyncio.run(calls_bot.report(compte_rendu.context, appel(), etat_vapi))
    assert compte_rendu.fini == [("termine", "assistant-ended-call", "Message transmis, elle te souhaite bon appétit.", 52)]
    assert ("vapi", 0.054) in compte_rendu.couts
    assert any(service == "twilio" for service, _ in compte_rendu.couts)
    assert "Message transmis" in compte_rendu.envois[0]
    # Le compte rendu rejoint la conversation pour les questions suivantes d'Hugo
    role, contenu = compte_rendu.conversation[0]
    assert role == "assistant" and "Maman" in contenu and "Message transmis" in contenu


def test_personne_n_a_decroche(compte_rendu):
    asyncio.run(calls_bot.report(compte_rendu.context, appel(), {"endedReason": "customer-did-not-answer"}))
    status, _, resume, _ = compte_rendu.fini[0]
    assert status == "echec" and "personne n'a décroché" in resume
    assert "personne n'a décroché" in compte_rendu.envois[0]
