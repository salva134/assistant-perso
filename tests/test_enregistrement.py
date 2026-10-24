"""Enregistrement des appels : audio MP3 et échange écrit envoyés à Hugo à la fin."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx

from app import calls, calls_bot

ETAT_VAPI = {
    "id": "vapi-1",
    "artifact": {
        "messages": [
            {"role": "system", "message": "Consignes…"},
            {"role": "bot", "message": "Bonjour, c'est Marcus, l'assistant IA d'Hugo."},
            {"role": "user", "message": "Da Luigi, je vous écoute."},
            {"role": "tool_calls", "message": ""},
            {"role": "bot", "message": "Je vérifie avec Hugo, un petit instant."},
        ],
        "transcript": "AI: Bonjour\nUser: Da Luigi",
    },
}


def test_les_appels_sont_enregistres_en_mp3():
    a = calls.voice_assistant("x", "Bonjour", "consignes")
    assert a["artifactPlan"] == {"recordingEnabled": True, "recordingFormat": "mp3"}


def test_echange_en_dialogue():
    assert calls.format_exchange(ETAT_VAPI, "Da Luigi") == (
        "Marcus : Bonjour, c'est Marcus, l'assistant IA d'Hugo.\n"
        "Da Luigi : Da Luigi, je vous écoute.\n"
        "Marcus : Je vérifie avec Hugo, un petit instant."
    )


def test_echange_sans_le_detail_des_messages():
    etat = {"artifact": {"transcript": "AI: Bonjour\nUser: Oui ?"}}
    assert calls.format_exchange(etat, "Maman") == "Marcus : Bonjour\nMaman : Oui ?"


def test_noms_courts_et_fichier_audio():
    assert calls.short_name("Pizzeria Da Luigi (livraison ce soir)") == "Pizzeria Da Luigi"
    quand = datetime(2026, 10, 24, 18, 5, tzinfo=timezone.utc)
    assert calls.recording_filename("Pizzeria Da Luigi (livraison)", quand) == "appel-2026-10-24-20h05-pizzeria-da-luigi.mp3"


def test_compte_rendu_avec_l_echange():
    a = calls.Appel(1, "+33442000000", "Da Luigi", "Réserver", "J'appelle.", "", "termine", datetime.now(timezone.utc))
    texte = calls.format_report(a, 30, "Réservé.", "Marcus : Bonjour\nDa Luigi : Oui ?")
    assert "💬 L'échange :\nMarcus : Bonjour\nDa Luigi : Oui ?" in texte


# --- Téléchargement chez Vapi -----------------------------------------------------------------


def client_vapi(reponses):
    """VapiClient dont les requêtes HTTP renvoient les réponses prévues, une à une."""
    appels = []

    def transport(request):
        appels.append(str(request.url))
        status, contenu = reponses.pop(0)
        return httpx.Response(status, content=contenu)

    client = calls.VapiClient("cle", "num")
    client._http = httpx.AsyncClient(transport=httpx.MockTransport(transport))
    return client, appels


def test_enregistrement_telecharge():
    client, appels = client_vapi([(200, b"ID3audio")])
    assert asyncio.run(client.recording("vapi-1")) == b"ID3audio"
    assert appels == ["https://api.vapi.ai/call/vapi-1/mono-recording"]


def test_enregistrement_pas_encore_pret_puis_disponible():
    client, appels = client_vapi([(404, b""), (404, b""), (200, b"ID3audio")])
    assert asyncio.run(client.recording("vapi-1", attente=0)) == b"ID3audio"
    assert len(appels) == 3


def test_enregistrement_jamais_disponible():
    client, _ = client_vapi([(404, b"")] * 3)
    assert asyncio.run(client.recording("vapi-1", essais=3, attente=0)) is None


# --- Envoi sur Telegram ---------------------------------------------------------------------------


def contexte(audio):
    envoyes = []

    async def recording(vapi_id):
        return audio

    async def send_audio(chat_id, audio, filename, title, performer):
        envoyes.append(("audio", filename, title, audio))

    async def send_message(chat_id, texte):
        envoyes.append(("texte", texte))

    ctx = SimpleNamespace(
        bot=SimpleNamespace(send_audio=send_audio, send_message=send_message),
        bot_data={"vapi": SimpleNamespace(recording=recording), "config": SimpleNamespace(allowed_user_id=1)},
    )
    return ctx, envoyes


def test_l_audio_est_envoye_a_hugo():
    ctx, envoyes = contexte(b"ID3audio")
    asyncio.run(calls_bot.send_recording(ctx, "vapi-1", "Pizzeria Da Luigi (livraison)"))
    genre, fichier, titre, audio = envoyes[0]
    assert genre == "audio" and fichier.endswith("-pizzeria-da-luigi.mp3")
    assert titre == "Appel — Pizzeria Da Luigi" and audio == b"ID3audio"


def test_hugo_est_prevenu_si_l_audio_manque():
    ctx, envoyes = contexte(None)
    asyncio.run(calls_bot.send_recording(ctx, "vapi-1", "Da Luigi"))
    assert envoyes == [("texte", "🎧 L'enregistrement audio n'est pas disponible pour cet appel.")]
