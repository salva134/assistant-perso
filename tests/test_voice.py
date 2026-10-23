"""Vocaux : transcription, limite de durée, coût, puis traitement comme un message écrit."""

import asyncio
from types import SimpleNamespace

from app import main, usage, voice


class FauxOpenAI:
    def __init__(self, texte):
        self.requetes = []

        async def create(**params):
            self.requetes.append(params)
            return SimpleNamespace(text=texte)

        self.audio = SimpleNamespace(transcriptions=SimpleNamespace(create=create))


def test_transcription_en_francais():
    client = FauxOpenAI("  Rappelle-moi d'acheter du pain.  ")
    texte = asyncio.run(voice.transcribe(client, b"OggS..."))
    assert texte == "Rappelle-moi d'acheter du pain."
    requete = client.requetes[0]
    assert requete["model"] == "gpt-4o-mini-transcribe"
    assert requete["language"] == "fr"
    assert requete["file"] == ("vocal.ogg", b"OggS...")


def test_cout_d_un_vocal_de_30_secondes(monkeypatch):
    enregistre = {}

    async def record(pool, service, model, **kwargs):
        enregistre.update(kwargs, model=model)

    monkeypatch.setattr(usage, "record", record)
    asyncio.run(usage.record_transcription(None, "gpt-4o-mini-transcribe", 30))
    assert enregistre["cost_usd"] == 0.0015


def faux_update(duree, reponses):
    async def reply_text(texte):
        reponses.append(texte)

    return SimpleNamespace(
        message=SimpleNamespace(voice=SimpleNamespace(duration=duree, file_id="f1"), reply_text=reply_text),
        effective_chat=SimpleNamespace(id=1),
    )


def faux_context():
    async def rien(*args, **kwargs):
        return None

    async def get_file(file_id):
        async def download_as_bytearray():
            return bytearray(b"OggS...")

        return SimpleNamespace(download_as_bytearray=download_as_bytearray)

    return SimpleNamespace(
        bot=SimpleNamespace(send_chat_action=rien, get_file=get_file),
        bot_data={"openai": None, "pool": None},
    )


def test_vocal_trop_long_refuse_sans_le_telecharger(monkeypatch):
    async def interdit(*args, **kwargs):
        raise AssertionError("ne doit pas être transcrit")

    monkeypatch.setattr(voice, "transcribe", interdit)
    reponses = []
    asyncio.run(main.on_voice(faux_update(301, reponses), faux_context()))
    assert "5 minutes" in reponses[0]


def test_vocal_traite_comme_un_message_ecrit(monkeypatch):
    async def pas_de_plafond(update, context):
        return False

    async def transcrire(client, audio):
        assert audio == b"OggS..."
        return "C'est quoi mon plat préféré ?"

    async def noter(*args):
        return None

    recu = {}

    async def answer(update, context, texte, entete=""):
        recu.update(texte=texte, entete=entete)

    monkeypatch.setattr(main, "over_budget", pas_de_plafond)
    monkeypatch.setattr(voice, "transcribe", transcrire)
    monkeypatch.setattr(usage, "record_transcription", noter)
    monkeypatch.setattr(main, "answer", answer)

    asyncio.run(main.on_voice(faux_update(30, []), faux_context()))

    assert recu["texte"] == "C'est quoi mon plat préféré ?"
    assert recu["entete"].startswith("🎤 « C'est quoi mon plat préféré ? »")


def test_vocal_silencieux(monkeypatch):
    async def pas_de_plafond(update, context):
        return False

    async def transcrire(client, audio):
        return ""

    async def noter(*args):
        return None

    monkeypatch.setattr(main, "over_budget", pas_de_plafond)
    monkeypatch.setattr(voice, "transcribe", transcrire)
    monkeypatch.setattr(usage, "record_transcription", noter)
    reponses = []
    asyncio.run(main.on_voice(faux_update(3, reponses), faux_context()))
    assert reponses == ["Je n'ai rien entendu dans ce vocal."]
