"""Accueil : Marcus répond quand on appelle son numéro, puis prévient Hugo."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app import calls, calls_bot, memory, usage
from app.calls import AppelRecent

RECENT = AppelRecent(
    "Pizzeria Da Luigi", "+33442000000", "Commander deux pizzas livrées à vingt heures.",
    "Commande confirmée, 25,50 €.", datetime(2026, 10, 24, 9, 2, tzinfo=timezone.utc),
)


def test_l_accueil_connait_les_appels_recents_et_la_memoire():
    a = calls.build_inbound_assistant("Ce que tu sais d'Hugo :\n- Hugo adore le piment.", [RECENT])
    consignes = a["model"]["messages"][0]["content"]
    assert a["name"] == calls.NOM_ACCUEIL
    assert a["firstMessage"].startswith("Bonjour, vous êtes bien sur la ligne d'Hugo")
    assert "Pizzeria Da Luigi" in consignes and "25,50 €" in consignes and "le 24/10 à 11h02" in consignes
    assert "Hugo adore le piment." in consignes
    # Même voix et mêmes réglages que les appels passés
    assert a["voice"] == calls.VOIX and a["transcriber"] == calls.TRANSCRIPTION


def test_l_accueil_protege_les_infos_d_hugo_et_ne_s_engage_pas():
    consignes = calls.build_inbound_assistant("", [])["model"]["messages"][0]["content"]
    assert "Ne donne aucune information personnelle" in consignes
    assert "Ne prends aucun nouvel engagement" in consignes
    assert "démarchage" in consignes


def test_sans_appel_recent():
    assert "aucun appel" in calls.format_recent_calls([])


def test_compte_rendu_d_un_appel_recu():
    texte = calls.format_inbound_report("Pizzeria Da Luigi", "+33442000000", 45, "Livraison décalée à 20h30.", "User: Allô")
    assert texte.startswith("📲 Appel reçu — Pizzeria Da Luigi (04 42 00 00 00)")
    assert "Livraison décalée" in texte and "0 min 45 s" in texte


# --- Surveillance des appels reçus ------------------------------------------------------


@pytest.fixture
def accueil(monkeypatch):
    etat = SimpleNamespace(envois=[], enregistres=[], conversation=[], deja_vus=set(), resume_appels=[])

    async def last_inbound_time(pool):
        return None

    async def known_vapi_ids(pool, ids):
        return {i for i in ids if i in etat.deja_vus}

    async def name_for(pool, numero):
        return "Pizzeria Da Luigi" if numero == "+33442000000" else None

    async def summarize_inbound(claude, model, appelant, transcript, on_response=None):
        etat.resume_appels.append(appelant)
        return "La pizzeria prévient que la livraison aura 15 minutes de retard."

    async def record_inbound(pool, vapi_id, numero, appelant, resume, transcript, duree, raison):
        if vapi_id in etat.deja_vus:
            return False
        etat.deja_vus.add(vapi_id)
        etat.enregistres.append((vapi_id, appelant, resume))
        return True

    async def record(*args, **kwargs):
        pass

    async def save_message(pool, role, content):
        etat.conversation.append(content)

    async def send_message(chat_id, texte):
        etat.envois.append(texte)

    for nom, f in [("last_inbound_time", last_inbound_time), ("known_vapi_ids", known_vapi_ids),
                   ("name_for", name_for), ("summarize_inbound", summarize_inbound),
                   ("record_inbound", record_inbound)]:
        monkeypatch.setattr(calls, nom, f)
    monkeypatch.setattr(usage, "record", record)
    monkeypatch.setattr(memory, "save_message", save_message)

    async def calls_since(depuis):
        return etat.vapi

    etat.vapi = []
    etat.context = SimpleNamespace(
        bot=SimpleNamespace(send_message=send_message),
        bot_data={"pool": None, "claude": None, "vapi": SimpleNamespace(calls_since=calls_since),
                  "config": SimpleNamespace(model="m", allowed_user_id=1)},
    )
    return etat


def appel_vapi(id_, type_="inboundPhoneCall", status="ended", numero="+33442000000", transcript=None):
    return {
        "id": id_, "type": type_, "status": status, "customer": {"number": numero},
        "startedAt": "2026-10-24T12:00:00Z", "endedAt": "2026-10-24T12:00:45Z",
        "artifact": {"transcript": transcript or "AI: Bonjour… User: Oui, c'est Da Luigi, on aura un peu de retard."},
    }


def test_un_appel_recu_est_resume_et_signale_a_hugo(accueil):
    accueil.vapi = [appel_vapi("v1")]
    asyncio.run(calls_bot.inbound_job(accueil.context))
    assert accueil.resume_appels == ["Pizzeria Da Luigi"]  # reconnue grâce à l'appel passé
    assert accueil.envois[0].startswith("📲 Appel reçu — Pizzeria Da Luigi")
    assert "retard" in accueil.conversation[0]  # rejoint la conversation


def test_un_appel_deja_signale_ne_l_est_pas_deux_fois(accueil):
    accueil.vapi = [appel_vapi("v1")]
    asyncio.run(calls_bot.inbound_job(accueil.context))
    asyncio.run(calls_bot.inbound_job(accueil.context))
    assert len(accueil.envois) == 1
    assert accueil.resume_appels == ["Pizzeria Da Luigi"]  # pas de second résumé payé


def test_seuls_les_appels_recus_et_termines_sont_traites(accueil):
    accueil.vapi = [
        appel_vapi("sortant", type_="outboundPhoneCall"),
        appel_vapi("en-cours", status="in-progress"),
    ]
    asyncio.run(calls_bot.inbound_job(accueil.context))
    assert accueil.envois == []


def test_appel_recu_sans_message(accueil):
    accueil.vapi = [appel_vapi("v2", numero="+33600000000", transcript="AI: Bonjour")]
    asyncio.run(calls_bot.inbound_job(accueil.context))
    assert accueil.resume_appels == []  # rien à résumer
    assert "sans laisser de message" in accueil.envois[0]
    assert "06 00 00 00 00" in accueil.envois[0]
